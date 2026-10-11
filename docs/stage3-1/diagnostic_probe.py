"""Read-only, bounded real probes. Does not write production market data.

Run with the existing project interpreter. Network settings are unchanged.
The diagnostic-only request wrapper supplies an 8-second timeout if absent.
"""
import contextlib
import io
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent


def timestamp():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def sanitize(value):
    text = str(value).replace(str(Path.home()), "<home>")
    text = re.sub(r"https?://[^\s'\")]+", "<url>", text)
    text = re.sub(r"(with url: [^\s?'\"()]+)\?[^\s'\"()]+", r"\1?<redacted-query>", text)
    text = re.sub(r"host=['\"][^'\"]+['\"]", "host='<redacted>'", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<address>", text)
    text = re.sub(r"(?i)(authorization|password|token|api_key)\s*[:=]\s*\S+", r"\1=<redacted>", text)
    return text[:1800]


def child():
    payload = json.load(sys.stdin)
    result = {"started_at": timestamp(), "requests": []}
    start = time.monotonic()
    try:
        import akshare as ak
        import requests
        original = requests.sessions.Session.request

        def tracked(self, method, url, *args, **kwargs):
            parsed = urlsplit(url)
            event = {"method": method.upper(), "host": parsed.hostname, "path": parsed.path}
            result["requests"].append(event)
            if kwargs.get("timeout") is None:
                kwargs["timeout"] = 8
                event["diagnostic_timeout_supplied"] = True
            try:
                response = original(self, method, url, *args, **kwargs)
                event["http_status"] = response.status_code
                return response
            except Exception as exc:
                event["error_type"] = type(exc).__name__
                raise

        requests.sessions.Session.request = tracked
        result["akshare_version"] = ak.__version__
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            frame = getattr(ak, payload["interface"])(**payload["parameters"])
        result.update(status="ok" if len(frame) else "empty", rows=len(frame), fields=list(frame.columns))
    except Exception as exc:
        seen, chain = set(), []

        def visit(error):
            if not isinstance(error, BaseException) or id(error) in seen:
                return
            seen.add(id(error))
            chain.append({"type": type(error).__name__, "message": sanitize(error)})
            visit(error.__cause__)
            visit(error.__context__)
            visit(getattr(error, "reason", None))
            for arg in error.args:
                visit(arg)

        visit(exc)
        result.update(status="error", error_type=type(exc).__name__, exception_chain=chain)
    result.update(finished_at=timestamp(), duration_seconds=round(time.monotonic() - start, 2))
    print(json.dumps(result, ensure_ascii=False, default=str))


def main():
    cases = [
        ("index_em", "index_zh_a_hist", {"symbol": "000001", "period": "daily", "start_date": "20260305", "end_date": "20261009"}),
        ("stock_em", "stock_zh_a_hist", {"symbol": "601001", "period": "daily", "adjust": "", "start_date": "20260305", "end_date": "20261009"}),
        ("metadata_em", "stock_individual_info_em", {"symbol": "601001", "timeout": 8}),
        ("fund_flow_em", "stock_individual_fund_flow", {"stock": "601001", "market": "sh"}),
        ("control_index_sina", "stock_zh_index_daily", {"symbol": "sh000001"}),
        ("control_stock_tx", "stock_zh_a_hist_tx", {"symbol": "sh601001", "start_date": "20260305", "end_date": "20261009", "adjust": ""}),
    ]
    report = {"started_at": timestamp(), "environment": {"platform": platform.platform(), "python": platform.python_version()},
              "scope": "six diagnostic-only real calls, not a 42-case acceptance rerun",
              "network_configuration_changed": False, "diagnostic_request_timeout_seconds": 8,
              "process_timeout_seconds": 22, "explicit_retries": 0, "results": []}
    report["environment"]["proxy_env_presence_only"] = {key: bool(os.environ.get(key)) for key in
        ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy")}
    report["dns"] = []
    for host in ("push2.eastmoney.com", "push2his.eastmoney.com", "web.ifzq.gtimg.cn", "finance.sina.com.cn"):
        try:
            socket.getaddrinfo(host, 443)
            report["dns"].append({"host": host, "status": "resolved", "checked_at": timestamp()})
        except OSError as exc:
            report["dns"].append({"host": host, "status": "error", "error_type": type(exc).__name__, "checked_at": timestamp()})
    for key, interface, parameters in cases:
        case = {"key": key, "interface": interface, "parameters": parameters}
        try:
            proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child"], input=json.dumps(case),
                                  text=True, capture_output=True, timeout=22, check=False)
            if proc.returncode == 0:
                case.update(json.loads(proc.stdout))
            else:
                case.update(status="worker_failed", returncode=proc.returncode)
        except subprocess.TimeoutExpired:
            case.update(status="timeout", error_type="diagnostic_process_timeout")
        report["results"].append(case)
        print(json.dumps({"key": key, "status": case["status"], "error_type": case.get("error_type"),
                          "duration_seconds": case.get("duration_seconds")}, ensure_ascii=False), flush=True)
    report["finished_at"] = timestamp()
    (HERE / "representative-live-probes.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    child() if "--child" in sys.argv else main()
