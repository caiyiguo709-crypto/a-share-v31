"""Isolated, observation-only stock-source PoC. Never writes production signals."""
import argparse
import contextlib
import importlib
import io
import json
import math
import platform
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from ashare.config import WATCHLIST, INDICES
from ashare.engine import valid_clock
from ashare.provider import cross_check
from ashare.stage1 import TZ, eligible_code, expected_date, iso_now
from ashare.storage import atomic_json

ROOT = Path(__file__).resolve().parents[1]
AKSHARE_VERSION = '1.19.1'
CONTRACTS = {
    'sina_raw': {'upstream': 'sina', 'interface': 'Sina KLC decoded raw bars',
                 'basis': 'unadjusted', 'volume_unit': 'shares', 'amount_unit': 'CNY'},
    'tencent_sdk': {'upstream': 'tencent', 'interface': 'stock_zh_a_hist_tx',
                    'basis': 'unadjusted', 'volume_unit': 'shares', 'amount_unit': 'CNY'},
    'calendar_sina': {'upstream': 'sina', 'interface': 'tool_trade_date_hist_sina'},
    'index_sina': {'upstream': 'sina', 'interface': 'stock_zh_index_daily',
                   'basis': 'unadjusted', 'volume_unit': 'UNVERIFIED', 'amount_unit': 'UNVERIFIED'},
    'index_em': {'upstream': 'eastmoney', 'interface': 'index_zh_a_hist',
                 'basis': 'unadjusted', 'volume_unit': 'shares', 'amount_unit': 'CNY'},
}


def error_chain(error):
    """Keep nested cause types, never error messages containing credentials/URLs."""
    seen, types = set(), []
    def visit(exc):
        if not isinstance(exc, BaseException) or id(exc) in seen: return
        seen.add(id(exc))
        types.append(type(exc).__name__)
        visit(exc.__cause__)
        visit(exc.__context__)
        visit(getattr(exc, 'reason', None))
        for arg in exc.args: visit(arg)
    visit(error)
    return types


def normalize_records(records):
    """No sorting, filling, deduplication or scaling already-normalized SDK shares."""
    bars = []
    for record in records:
        bar = {'date': str(record.get('date', 'invalid'))[:10]}
        for key in ('open', 'high', 'low', 'close', 'volume', 'amount'):
            try:
                value = float(record[key])
                bar[key] = value if math.isfinite(value) else None
            except (KeyError, TypeError, ValueError):
                bar[key] = None
        bars.append(bar)
    return bars


def tencent_stock_records(records, code):
    if not eligible_code(code): raise ValueError('mainboard stock identity required')
    bars = normalize_records(records)
    # AKShare 1.19.1 incorrectly excludes sz000 stocks with its index prefix check.
    # Their SDK output remains hands; other eligible stock output is already shares.
    if code.startswith('000'):
        for bar in bars:
            if bar['volume'] is not None: bar['volume'] *= 100
    return bars


def unit_sanity(bars):
    """Diagnostic CNY/share check, not a new production risk threshold."""
    failures = []
    for bar in bars:
        values = [bar.get(k) for k in ('amount', 'volume', 'low', 'high')]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in values):
            failures.append({'date': bar.get('date'), 'reason': 'missing_or_invalid_units'})
            continue
        amount, volume, low, high = values
        # One cent allows published price/turnover rounding. Never repair by inferred scale.
        if not low - 0.01 <= amount / volume <= high + 0.01:
            failures.append({'date': bar.get('date'), 'reason': 'turnover_per_share_outside_daily_range'})
    return {'status': 'PASS' if bars and not failures else 'FAIL', 'failures': failures,
            'scope': 'diagnostic unit plausibility only; not independent unit certification'}


def encoded_sina_payload(text, symbol):
    match = re.search(r'\bvar\s+KLC_K2_' + re.escape(symbol) + r'\s*=\s*"([^"]+)"\s*;', text)
    if not match: raise ValueError('Sina response identity/encoding not verified')
    return match.group(1)


def decoder_details(module):
    """Describe the SDK's actual import, without publishing machine paths."""
    import inspect
    from importlib.metadata import version, PackageNotFoundError
    backend = module.py_mini_racer
    cls = backend.MiniRacer
    packages = {}
    for name in ('akshare', 'mini-racer', 'py-mini-racer', 'akracer'):
        try: packages[name] = version(name)
        except PackageNotFoundError: packages[name] = None
    return {'import_module': backend.__name__, 'module_file': Path(backend.__file__).name,
            'class_module': cls.__module__, 'class_file': Path(inspect.getfile(cls)).name,
            'context_manager': hasattr(cls, '__enter__') and hasattr(cls, '__exit__'),
            'close_method': callable(getattr(cls, 'close', None)), 'packages': packages}


def decode_sina_records(module, encoded):
    decoder = module.py_mini_racer.MiniRacer()
    try:
        decoder.eval(module.hk_js_decode)
        records = decoder.call('d', encoded)
    finally:
        # Legacy/akracer releases free the V8 context in __del__, without a close API.
        # Modern releases expose close; do not require the optional context protocol.
        close = getattr(decoder, 'close', None)
        if callable(close): close()
    if not isinstance(records, list) or not records or any(not isinstance(r, dict) for r in records):
        raise ValueError('decoded Sina result is not a nonempty record list')
    return records


def sina_window(records, start, end):
    selected = []
    for record in records:
        day = datetime.fromisoformat(str(record.get('date', 'invalid'))).date().isoformat()
        if day > datetime.now(TZ).date().isoformat():
            raise ValueError('decoded Sina date is in the future')
        if start <= day <= end:
            if any(key not in record for key in ('date', 'open', 'high', 'low', 'close', 'volume', 'amount')):
                raise ValueError('decoded Sina record missing required fields')
            selected.append(record)
    if not selected: raise ValueError('decoded Sina window is empty')
    return selected


def index_records(records, adapter):
    if adapter == 'index_em':
        aliases = dict(zip(('日期', '开盘', '最高', '最低', '收盘', '成交量', '成交额'),
                           ('date', 'open', 'high', 'low', 'close', 'volume', 'amount')))
        bars = normalize_records([{aliases.get(k, k): v for k, v in r.items()} for r in records])
        for bar in bars:
            if bar['volume'] is not None: bar['volume'] *= 100
        return bars
    if adapter == 'index_sina': return normalize_records(records)
    raise ValueError('unapproved index source')


def assess_index(left, right, calendar, now):
    reasons = []
    for packet in (left, right):
        adapter = packet.get('adapter')
        if adapter not in ('index_sina', 'index_em') or packet.get('upstream') != CONTRACTS[adapter]['upstream']:
            reasons.append('source_identity_unverified')
        if packet.get('status') != 'PASS' or packet.get('evidence_kind') != 'real_network':
            reasons.append('source_request_not_passed')
        if packet.get('basis') != 'unadjusted' or not valid_clock(packet.get('collected_at'), now):
            reasons.append('basis_or_clock_unverified')
    if left.get('upstream') == right.get('upstream'): reasons.append('same_upstream')
    if left.get('code') not in INDICES or left.get('code') != right.get('code'):
        reasons.append('index_identity_mismatch')
    check = cross_check(left.get('bars', []), right.get('bars', []), expected_date(calendar, now), calendar, now)
    if check['status'] != 'passed': reasons.append('index_price_quality_failed')
    quantity = {}
    for packet in (left, right):
        bars = packet.get('bars', [])
        positive = bool(bars) and all(isinstance(b.get(k), (int, float)) and math.isfinite(b[k]) and b[k] > 0
                                     for b in bars for k in ('volume', 'amount'))
        quantity[packet.get('adapter')] = {'status': 'PASS' if positive and
            packet.get('volume_unit') == 'shares' and packet.get('amount_unit') == 'CNY' else 'UNVERIFIED',
            'reason': 'published numeric/unit contract check; index points are not stock VWAP'}
    return {'code': left.get('code'), 'name': INDICES.get(left.get('code')),
            'price_quality_status': 'FAIL' if reasons else 'PASS', 'reasons': sorted(set(reasons)),
            'cross_check': check, 'quantity_units': quantity,
            'buy_permission': False, 'research_permission': False}


def child():
    request = json.load(sys.stdin)
    adapter, code = request['adapter'], request['code']
    index_adapter = adapter in ('index_sina', 'index_em')
    if adapter not in CONTRACTS or (index_adapter and code not in INDICES) or (
            adapter != 'calendar_sina' and not index_adapter and not eligible_code(code)):
        raise ValueError('unapproved source/code')
    packet = {**CONTRACTS[adapter], 'adapter': adapter, 'code': code, 'bars': [],
              'evidence_kind': 'real_network', 'started_at': iso_now(), 'http_requests': []}
    stage = 'dependency_import'
    try:
        import akshare as ak
        import requests
        if ak.__version__ != AKSHARE_VERSION:
            raise RuntimeError('AKShare version differs from audited unit contract')
        packet['akshare_version'] = ak.__version__
        original = requests.Session.request
        def tracked(session, method, url, **kwargs):
            from urllib.parse import urlsplit
            parsed = urlsplit(url)
            event = {'method': method.upper(), 'host': parsed.hostname, 'path': parsed.path,
                     'started_at': iso_now(), 'proxy_configured': bool(requests.utils.get_environ_proxies(url))}
            packet['http_requests'].append(event)
            # Diagnostic timeout only. Preserve proxy resolution and TLS verification.
            if kwargs.get('timeout') is None: kwargs['timeout'] = (5, 8)
            try:
                response = original(session, method, url, **kwargs)
                event['http_status'] = response.status_code
                return response
            except Exception as exc:
                event['error_chain'] = error_chain(exc)
                raise
        requests.Session.request = tracked
        stage = 'request_or_decode'
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if adapter == 'calendar_sina':
                frame = ak.tool_trade_date_hist_sina()
                records = json.loads(frame.to_json(orient='records', date_format='iso'))
                packet['calendar'] = [str(r['trade_date'])[:10] for r in records]
            elif adapter == 'sina_raw':
                symbol = ('sh' if code.startswith('6') else 'sz') + code
                module = importlib.import_module('akshare.stock.stock_zh_a_sina')
                response = requests.get(module.zh_sina_a_stock_hist_url.format(symbol), timeout=(5, 8))
                response.raise_for_status()
                stage = 'response_identity'
                packet['raw_response'] = response.text
                # Decode pinned SDK's codec directly: avoid stock_zh_a_daily's ffill/drop_duplicates.
                encoded = encoded_sina_payload(response.text, symbol)
                stage = 'decode'
                packet['decoder'] = decoder_details(module)
                records = decode_sina_records(module, encoded)
                stage = 'normalization'
                packet['raw_records'] = records
                packet['bars'] = normalize_records(sina_window(records, request['start'], request['end']))
            elif index_adapter:
                if adapter == 'index_sina':
                    frame = ak.stock_zh_index_daily(symbol=('sh' if code == '000001' else 'sz') + code)
                else:
                    frame = ak.index_zh_a_hist(symbol=code, period='daily',
                        start_date=request['start'].replace('-', ''), end_date=request['end'].replace('-', ''))
                records = json.loads(frame.to_json(orient='records', date_format='iso'))
                packet['raw_records'] = records
                packet['bars'] = [r for r in index_records(records, adapter)
                                  if request['start'] <= r['date'] <= request['end']]
                packet['unit_conversion'] = {'volume_multiplier': 100 if adapter == 'index_em' else 1,
                    'reason': 'EM documented hands to shares; Sina units not certified',
                    'contract_document': 'https://github.com/akfamily/akshare/blob/main/docs/data/index/index.md'}
            else:
                symbol = ('sh' if code.startswith('6') else 'sz') + code
                frame = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=request['start'].replace('-', ''),
                                             end_date=request['end'].replace('-', ''), adjust='', timeout=(5, 8))
                records = json.loads(frame.to_json(orient='records', date_format='iso'))
                packet['raw_records'] = records
                packet['bars'] = tencent_stock_records(records, code)
                packet['unit_conversion'] = {
                    'volume_multiplier': 100 if code.startswith('000') else 1,
                    'reason': 'pinned AKShare 1.19.1 sz000 stock/index prefix defect',
                    'input': 'SDK output preserved in raw_records', 'output': 'shares'}
        stage = 'response_contract'
        nonempty = bool(packet.get('calendar') if adapter == 'calendar_sina' else packet['bars'])
        packet.update(status='PASS' if nonempty else 'FAIL', failure_stage=None if nonempty else stage)
    except ImportError as exc:
        packet.update(status='BLOCKED_DEPENDENCY', failure_stage=stage, error_chain=error_chain(exc))
    except Exception as exc:
        packet.update(status='FAIL', failure_stage=stage, error_chain=error_chain(exc))
    packet['collected_at'] = iso_now()
    print(json.dumps(packet, ensure_ascii=False, allow_nan=False))


def probe(adapter, code, start, end):
    if adapter not in CONTRACTS: raise ValueError('unapproved adapter')
    packet = {**CONTRACTS[adapter], 'adapter': adapter, 'code': code, 'bars': [],
              'evidence_kind': 'real_network', 'started_at': iso_now(), 'application_attempts': 1,
              'explicit_retries': 0, 'sdk_http_attempts': None}
    tick = time.monotonic()
    try:
        result = subprocess.run([sys.executable, '-m', 'poc.backup_sources', '--child'],
            input=json.dumps({'adapter': adapter, 'code': code, 'start': start, 'end': end}),
            text=True, capture_output=True, timeout=22, cwd=ROOT, check=False)
        if result.returncode: raise RuntimeError('worker failed')
        packet.update(json.loads(result.stdout))
    except subprocess.TimeoutExpired:
        packet.update(status='FAIL', failure_stage='process_deadline', error_chain=['TimeoutExpired'])
    except (OSError, ValueError, RuntimeError) as exc:
        packet.update(status='FAIL', failure_stage='worker_protocol', error_chain=error_chain(exc))
    packet['duration_seconds'] = round(time.monotonic() - tick, 3)
    return packet


def assess_pair(left, right, calendar, now):
    reasons = []
    for packet in (left, right):
        contract = CONTRACTS.get(packet.get('adapter'), {})
        if not contract or packet.get('upstream') != contract.get('upstream'):
            reasons.append('source_identity_unverified')
        if packet.get('status') != 'PASS': reasons.append('source_request_not_passed')
        if packet.get('evidence_kind') != 'real_network': reasons.append('not_real_network_evidence')
        if packet.get('basis') != 'unadjusted': reasons.append('adjustment_basis_unverified')
        if not valid_clock(packet.get('collected_at'), now): reasons.append('collection_clock_invalid')
    if left.get('upstream') == right.get('upstream'): reasons.append('same_upstream')
    if left.get('code') != right.get('code') or not eligible_code(left.get('code', '')):
        reasons.append('security_identity_mismatch')
    expected = expected_date(calendar, now)
    check = cross_check(left.get('bars', []), right.get('bars', []), expected, calendar, now)
    if check['status'] != 'passed': reasons.append(check.get('reason', 'price_quality_failed'))
    units = {packet.get('adapter'): unit_sanity(packet.get('bars', [])) for packet in (left, right)}
    return {'code': left.get('code'), 'expected_quote_date': expected,
            'price_quality_status': 'FAIL' if reasons else 'PASS', 'reasons': sorted(set(reasons)),
            'unit_sanity_status': 'PASS' if all(v['status'] == 'PASS' for v in units.values()) else 'FAIL',
            'unit_sanity': units,
            'cross_check': check, 'research_permission': False, 'buy_permission': False,
            'missing_acceptance': ['security_metadata', 'corporate_actions', 'industry', 'funds']}


def output_path(value):
    path = Path(value)
    path = (ROOT/path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(ROOT/'private') or path == ROOT/'private':
        raise ValueError('PoC output must stay in private; public output is forbidden')
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='private/poc/backup-sources.json')
    parser.add_argument('--include-indices', action='store_true')
    options = parser.parse_args()
    target = output_path(options.output)  # Reject public output before any request.
    now = datetime.now(TZ)
    calendar_packet = probe('calendar_sina', '', '', '')
    calendar = calendar_packet.get('calendar', []) if calendar_packet['status'] == 'PASS' else []
    end = expected_date(calendar, now)
    report = {'schema_version': 'backup-poc-1', 'started_at': now.isoformat(),
              'scope': '7 original stocks, 2 independent service paths; observations only',
              'python': platform.python_version(), 'contracts': CONTRACTS,
              'network_configuration_changed': False, 'tls_verification_disabled': False,
              'research_permission': False, 'buy_permission': False, 'calendar_probe': calendar_packet,
              'probes': [], 'pairs': [], 'index_pairs': [], 'pending': ['metadata', 'corporate_actions',
                  'industry', 'funds', 'github_actions', 'five_session_sampling']}
    if end is not None:
        start = (now - timedelta(days=220)).date().isoformat()
        cases = [(adapter, code) for code in WATCHLIST for adapter in ('sina_raw', 'tencent_sdk')]
        if options.include_indices:
            cases += [(adapter, code) for code in INDICES for adapter in ('index_sina', 'index_em')]
        else: report['pending'].append('index_pairs')
        with ThreadPoolExecutor(max_workers=2) as pool:
            for packet in pool.map(lambda case: probe(*case, start, end), cases):
                report['probes'].append(packet)
                atomic_json(target, report)
                print(json.dumps({k: packet.get(k) for k in ('adapter', 'code', 'status', 'duration_seconds')}, ensure_ascii=False), flush=True)
        packets = {(p['adapter'], p['code']): p for p in report['probes']}
        check_time = datetime.now(TZ)
        report['pairs'] = [assess_pair(packets['sina_raw', code], packets['tencent_sdk', code], calendar, check_time)
                           for code in WATCHLIST]
        if options.include_indices:
            report['index_pairs'] = [assess_index(packets['index_sina', code], packets['index_em', code], calendar, check_time)
                                     for code in INDICES]
    else:
        report['blocked_reason'] = 'calendar_unverified; planned stock/index probes NOT_RUN'
    report['finished_at'] = iso_now()
    atomic_json(target, report)
    passed = len(report['pairs']) == 7 and all(
        p['price_quality_status'] == p['unit_sanity_status'] == 'PASS' for p in report['pairs'])
    if options.include_indices:
        passed = passed and len(report['index_pairs']) == 3 and all(
            p['price_quality_status'] == 'PASS' and all(q['status'] == 'PASS' for q in p['quantity_units'].values())
            for p in report['index_pairs'])
    return 0 if passed else 2


if __name__ == '__main__':
    if '--child' in sys.argv: child()
    else: sys.exit(main())
