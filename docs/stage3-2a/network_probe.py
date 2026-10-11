"""Observe real SDK requests with unchanged proxy/TLS settings; no fake replies.

Logs only redacted configuration and public request hosts/paths, never headers,
cookies, query strings, proxy addresses or credentials. Runs the original fixed
42-case matrix with a bounded supervisor. This is not a production adapter.
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
ALLOWED = {'tool_trade_date_hist_sina', 'index_zh_a_hist', 'stock_zh_index_daily',
           'stock_zh_a_hist', 'stock_zh_a_hist_tx', 'stock_individual_info_em', 'stock_individual_fund_flow'}
PUBLIC_HOSTS = {'80.push2.eastmoney.com', 'push2.eastmoney.com', 'push2his.eastmoney.com',
                'finance.sina.com.cn', 'web.ifzq.gtimg.cn', 'proxy.finance.qq.com'}
ENV_KEYS = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy',
            'all_proxy', 'no_proxy', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE')


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='milliseconds')


def proxy_summary(proxies):
    result = {}
    for key, value in (proxies or {}).items():
        if key in {'no', 'no_proxy'}:
            result[key] = {'configured': bool(value), 'value': '<redacted>'}
            continue
        if not value:
            result[key] = {'configured': False}
            continue
        parsed = urlsplit(value if '://' in value else 'http://' + value)
        identity = f'{parsed.scheme}://{parsed.hostname}:{parsed.port}'
        result[key] = {'configured': True, 'scheme': parsed.scheme,
                       'endpoint_id': hashlib.sha256(identity.encode()).hexdigest()[:12],
                       'has_credentials': bool(parsed.username or parsed.password)}
    return result


def configuration():
    import requests
    environment_proxies = urllib.request.getproxies_environment()
    effective_proxies = urllib.request.getproxies()
    sysconf = getattr(urllib.request, 'getproxies_macosx_sysconf', None)
    session = requests.Session()
    return {'platform': platform.platform(), 'python': platform.python_version(),
            'requests': requests.__version__, 'environment_presence': {k: bool(os.environ.get(k)) for k in ENV_KEYS},
            'urllib_environment_proxies': proxy_summary(environment_proxies),
            'macos_system_proxies': proxy_summary(sysconf() if sysconf else {}),
            'urllib_effective_proxies': proxy_summary(effective_proxies),
            'proxy_resolution_implementation': 'getproxies_environment() or getproxies_macosx_sysconf()' if sys.platform == 'darwin' else 'platform urllib.getproxies()',
            'new_session_trust_env': session.trust_env, 'new_session_verify': session.verify,
            'new_session_proxies': proxy_summary(session.proxies),
            'requests_effective_by_target': {host: proxy_summary(requests.utils.get_environ_proxies('https://' + host)) for host in sorted(PUBLIC_HOSTS)}}


def sanitizer():
    private = set()
    for value in urllib.request.getproxies().values():
        if not value or '://' not in value:
            continue
        parsed = urlsplit(value)
        private.update(x for x in (value, parsed.hostname, parsed.username, parsed.password) if x)

    def clean(value):
        text = str(value).replace(str(Path.home()), '<home>')
        for item in sorted(private, key=len, reverse=True):
            text = text.replace(item, '<redacted>')
        text = re.sub(r'https?://[^\s\'\")]+', '<redacted-url>', text)
        text = re.sub(r"(with url: [^\s?'\"()]+)\?[^\s'\"()]+", r'\1?<redacted-query>', text)
        text = re.sub(r"host=['\"][^'\"]+['\"]", "host='<redacted>'", text)
        text = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '<address>', text)
        return text[:1400]
    return clean


def exception_chain(error, clean):
    seen, chain = set(), []

    def visit(exc):
        if not isinstance(exc, BaseException) or id(exc) in seen:
            return
        seen.add(id(exc))
        chain.append({'type': type(exc).__name__, 'message': clean(exc)})
        visit(exc.__cause__)
        visit(exc.__context__)
        visit(getattr(exc, 'reason', None))
        for arg in exc.args:
            visit(arg)
    visit(error)
    return chain


def child():
    import requests
    import urllib3.connection as connection
    case = json.load(sys.stdin)
    assert case['interface'] in ALLOWED
    clean = sanitizer()
    result = {'started_at': now(), 'requests': [], 'configuration': configuration()}
    original_environment = {k: os.environ.get(k) for k in ENV_KEYS}
    original_proxies = urllib.request.getproxies()
    active = [None]
    started = time.monotonic()

    def emit(data):
        sys.__stdout__.write(json.dumps(data, ensure_ascii=False, default=str) + '\n')
        sys.__stdout__.flush()

    def trace(phase, operation):
        def wrapped(*args, **kwargs):
            event = {'phase': phase, 'started_at': now()}
            tick = time.monotonic()
            if active[0] is not None:
                active[0]['transport_phases'].append(event)
            emit({'record_kind': 'transport_event', 'phase': phase, 'state': 'started', 'at': event['started_at']})
            try:
                value = operation(*args, **kwargs)
                event['status'] = 'ok'
                return value
            except Exception as exc:
                event.update(status='error', error_type=type(exc).__name__, message=clean(exc))
                raise
            finally:
                event.update(finished_at=now(), duration_seconds=round(time.monotonic() - tick, 4))
                emit({'record_kind': 'transport_event', **event})
        return wrapped

    connection.HTTPConnection._new_conn = trace('tcp_connect', connection.HTTPConnection._new_conn)
    connection.HTTPConnection._tunnel = trace('proxy_connect_tunnel', connection.HTTPConnection._tunnel)
    connection.HTTPSConnection.connect = trace('https_connect', connection.HTTPSConnection.connect)
    connection._ssl_wrap_socket_and_match_hostname = trace('tls_handshake', connection._ssl_wrap_socket_and_match_hostname)
    original_dns = socket.getaddrinfo

    def traced_dns(host, *args, **kwargs):
        role = 'target_dns' if host in PUBLIC_HOSTS else 'proxy_or_other_dns'
        return trace(role, original_dns)(host, *args, **kwargs)
    socket.getaddrinfo = traced_dns
    original_request = requests.sessions.Session.request
    original_send = requests.adapters.HTTPAdapter.send

    def observe_request(self, method, url, *args, **kwargs):
        parsed = urlsplit(url)
        event = {'request_number': len(result['requests']) + 1, 'started_at': now(),
                 'method': method.upper(), 'host': parsed.hostname if parsed.hostname in PUBLIC_HOSTS else '<other-host>',
                 'path': parsed.path, 'trust_env': self.trust_env, 'transport_phases': [], 'adapter_sends': []}
        result['requests'].append(event)
        previous = active[0]
        active[0] = event
        tick = time.monotonic()
        if kwargs.get('timeout') is None:
            kwargs['timeout'] = (5, 8)
            event['diagnostic_timeout_supplied'] = True
        event['timeout'] = kwargs.get('timeout')
        emit({'record_kind': 'request_start', **{k: v for k, v in event.items() if k not in {'transport_phases', 'adapter_sends'}}})
        try:
            response = original_request(self, method, url, *args, **kwargs)
            event['http_status'] = response.status_code
            return response
        except Exception as exc:
            event.update(error_type=type(exc).__name__, exception_chain=exception_chain(exc, clean))
            raise
        finally:
            event.update(finished_at=now(), duration_seconds=round(time.monotonic() - tick, 4))
            active[0] = previous

    def observe_send(self, request, *args, **kwargs):
        selected = requests.utils.select_proxy(request.url, kwargs.get('proxies') or {})
        verify = kwargs.get('verify', True)
        entry = {'started_at': now(), 'effective_proxies': proxy_summary(kwargs.get('proxies')),
                 'selected_proxy': proxy_summary({'selected': selected}),
                 'verify': verify if isinstance(verify, bool) else 'custom_ca_bundle',
                 'adapter_retry_settings': {k: getattr(self.max_retries, k, None) for k in ('total', 'connect', 'read', 'status')}}
        if active[0] is not None:
            active[0]['adapter_sends'].append(entry)
        tick = time.monotonic()
        try:
            response = original_send(self, request, *args, **kwargs)
            entry['http_status'] = response.status_code
            return response
        except Exception as exc:
            entry.update(error_type=type(exc).__name__, exception_chain=exception_chain(exc, clean))
            raise
        finally:
            entry.update(finished_at=now(), duration_seconds=round(time.monotonic() - tick, 4))
    requests.sessions.Session.request = observe_request
    requests.adapters.HTTPAdapter.send = observe_send
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            import akshare as ak
            result['akshare_version'] = ak.__version__
            frame = getattr(ak, case['interface'])(**case['parameters'])
        result.update(status='ok' if len(frame) else 'empty', rows=len(frame), fields=list(frame.columns))
        if case['interface'] != 'stock_individual_info_em':
            result['actual_tail_records'] = json.loads(frame.tail(60).to_json(orient='records', date_format='iso', force_ascii=False))
    except Exception as exc:
        result.update(status='error', error_type=type(exc).__name__, exception_chain=exception_chain(exc, clean))
    result.update(finished_at=now(), duration_seconds=round(time.monotonic() - started, 3),
                  proxy_configuration_unchanged=original_proxies == urllib.request.getproxies(),
                  environment_unchanged=original_environment == {k: os.environ.get(k) for k in ENV_KEYS})
    emit({'record_kind': 'result', **result})


def run_case(case):
    history = []
    started = time.monotonic()
    for attempt in range(2):
        output, timeout = '', False
        tick = time.monotonic()
        stamp = now()
        try:
            proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child'], input=json.dumps(case),
                                  text=True, capture_output=True, timeout=22, check=False)
            output = proc.stdout
            returncode = proc.returncode
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ''
            timeout = True
            returncode = None
        if isinstance(output, bytes):
            output = output.decode('utf-8', errors='replace')
        parsed = []
        for line in output.splitlines():
            try:
                parsed.append(json.loads(line))
            except ValueError:
                pass
        summaries = [p for p in parsed if p.get('record_kind') == 'result']
        if summaries:
            result = summaries[-1]
        else:
            result = {'status': 'timeout' if timeout else 'worker_error', 'error_type': 'supervisor_timeout' if timeout else 'no_result',
                      'started_at': stamp, 'finished_at': now(), 'partial_trace': parsed, 'returncode': returncode,
                      'duration_seconds': round(time.monotonic() - tick, 3)}
        result['application_attempt'] = attempt + 1
        history.append(result)
        if result['status'] == 'ok':
            break
        if attempt == 0:
            time.sleep(1)
    return {**case, 'status': history[-1]['status'], 'attempt_count': len(history), 'attempt_history': history,
            'duration_seconds_including_backoff': round(time.monotonic() - started, 3)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--keys')
    parser.add_argument('--output', default='network-results.json')
    options = parser.parse_args()
    old = json.loads((ROOT / 'docs/interface-report-stage2.json').read_text())
    cases = [{'original_case_number': i, 'key': r['key'], 'interface': r['interface'], 'parameters': r['parameters'],
              'stage2_status': r['status'], 'stage2_error': r.get('error')} for i, r in enumerate(old['requests'], 1)]
    if options.keys:
        keys = options.keys.split(',')
        cases = [c for c in cases if c['key'] in keys]
        assert len(cases) == len(keys)
    original_environment = {k: os.environ.get(k) for k in ENV_KEYS}
    original_proxies = urllib.request.getproxies()
    report = {'started_at': now(), 'matrix': 'original fixed 42-case Stage 2 matrix' if not options.keys else 'instrumentation pilot subset',
              'historical_window': '2026-03-05 to 2026-10-09; actual historical queries, not live intraday data',
              'configuration': configuration(), 'diagnostic_defaults': {'sequential': True, 'application_attempt_limit': 2,
                  'backoff_seconds': 1, 'process_timeout_seconds': 22, 'missing_request_timeout': [5, 8],
                  'sdk_existing_timeout_preserved': True, 'sdk_internal_retries_unchanged': True,
                  'trust_env_changed': False, 'proxy_configuration_changed': False, 'tls_verification_disabled': False},
              'target_dns': [], 'results': []}
    for host in sorted(PUBLIC_HOSTS):
        tick = time.monotonic()
        try:
            socket.getaddrinfo(host, 443)
            report['target_dns'].append({'host': host, 'status': 'resolved', 'duration_seconds': round(time.monotonic() - tick, 4)})
        except OSError as exc:
            report['target_dns'].append({'host': host, 'status': 'error', 'error_type': type(exc).__name__})
    for case in cases:
        result = run_case(case)
        report['results'].append(result)
        print(json.dumps({'case': case['original_case_number'], 'key': case['key'], 'status': result['status'],
                          'attempts': result['attempt_count'], 'seconds': result['duration_seconds_including_backoff']}, ensure_ascii=False), flush=True)
        (OUT / options.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    report.update(finished_at=now(), summary=dict(Counter(r['status'] for r in report['results'])),
                  proxy_configuration_unchanged=original_proxies == urllib.request.getproxies(),
                  environment_unchanged=original_environment == {k: os.environ.get(k) for k in ENV_KEYS})
    (OUT / options.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    child() if '--child' in sys.argv else main()
