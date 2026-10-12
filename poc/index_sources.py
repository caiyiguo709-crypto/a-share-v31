"""Beta03 isolated index diagnostics. Never emits production signals."""
import contextlib
import io
import json
import math
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from poc import backup_sources as old
from ashare.provider import cross_check
from ashare.engine import valid_clock
from ashare.stage1 import TZ, expected_date, iso_now

ROOT = old.ROOT
SYMBOLS = {'sh000001', 'sz399001', 'sz399006'}
PROVIDERS = {'index_sina': 'sina', 'index_tx': 'tencent', 'index_em': 'eastmoney', 'index_bs': 'baostock'}


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def normalize_index(adapter, records, start, end):
    aliases = dict(zip(('日期', '开盘', '最高', '最低', '收盘', '成交量', '成交额'),
                       ('date', 'open', 'high', 'low', 'close', 'volume', 'amount')))
    bars = []
    for raw in records:
        r = {aliases.get(k, k): v for k, v in raw.items()}
        day = str(r.get('date', 'invalid'))[:10]
        if not start <= day <= end: continue
        volume = number(r.get('amount' if adapter == 'index_tx' else 'volume'))
        shares = None if adapter == 'index_sina' or volume is None else volume * (100 if adapter in ('index_tx', 'index_em') else 1)
        bars.append({'date': day, **{k: number(r.get(k)) for k in ('open', 'high', 'low', 'close')},
                     'volume': volume, 'volume_observed': volume, 'volume_shares': shares,
                     'amount_cny': number(r.get('amount')) if adapter in ('index_em', 'index_bs') else None,
                     'adapter': adapter})
    return bars


def quantity_check(packet):
    adapter = packet.get('adapter'); bars = packet.get('bars', [])
    known_volume = adapter in ('index_tx', 'index_em', 'index_bs')
    known_money = adapter in ('index_em', 'index_bs')
    failures = []
    if packet.get('volume_unit') != ('shares' if known_volume else 'unknown'): failures.append('volume_unit')
    if packet.get('amount_unit') != ('CNY' if known_money else 'unknown'): failures.append('amount_unit')
    for bar in bars:
        raw = bar.get('volume_observed'); shares = bar.get('volume_shares'); money = bar.get('amount_cny')
        if not isinstance(raw, (int, float)) or not math.isfinite(raw) or raw <= 0: failures.append('volume_invalid')
        if known_volume:
            if shares != (raw * (100 if adapter in ('index_tx', 'index_em') else 1) if raw is not None else None): failures.append('volume_scale')
        elif shares is not None: failures.append('invented_shares')
        if known_money:
            if not isinstance(money, (int, float)) or not math.isfinite(money) or money <= 0: failures.append('amount_missing_or_invalid')
        elif money is not None: failures.append('invented_currency')
    if not bars: failures.append('empty')
    volume_status = 'PASS' if known_volume and not failures else ('FAIL' if failures else 'UNVERIFIED')
    amount_status = 'PASS' if known_money and not failures else ('FAIL' if failures else 'UNVERIFIED')
    scope_known = packet.get('quantity_scope') in ('index_constituents', 'exchange_wide', 'other_verified_scope') and bool(packet.get('scope_evidence'))
    return {'status': 'FAIL' if failures else ('PASS' if known_volume and known_money else 'UNVERIFIED'),
            'volume_status': volume_status, 'amount_status': amount_status,
            'scope_status': 'PASS' if scope_known else 'UNVERIFIED',
            'market_quantity_usable': bool(not failures and known_volume and known_money and scope_known),
            'failures': sorted(set(failures))}


def evaluate_pair(left, right, calendar, now):
    reasons = []
    for p in (left, right):
        symbol = p.get('symbol', '')
        if symbol not in SYMBOLS or p.get('exchange') != symbol[:2] or p.get('kind') != 'index': reasons.append('identity')
        if PROVIDERS.get(p.get('adapter')) != p.get('provider') or p.get('identity_status') != 'PASS': reasons.append('provider_identity')
        if p.get('status') != 'PASS' or p.get('cached') or p.get('evidence_kind') != 'real_network': reasons.append('evidence')
        if not valid_clock(p.get('collected_at'), now) or p.get('basis') != 'unadjusted': reasons.append('clock_or_basis')
        if any(b.get('adapter') != p.get('adapter') for b in p.get('bars', [])): reasons.append('mixed_series')
    independent = left.get('provider') != right.get('provider')
    if not independent: reasons.append('same_provider')
    if left.get('symbol') != right.get('symbol'): reasons.append('symbol_mismatch')
    check = cross_check(left.get('bars', []), right.get('bars', []), expected_date(calendar, now), calendar, now)
    if check['status'] != 'passed': reasons.append('price_quality')
    units = [quantity_check(p) for p in (left, right)]
    scopes = [p.get('quantity_scope', 'unknown') for p in (left, right)]
    quantity = 'FAIL' if scopes[0] != scopes[1] or any(q['status'] == 'FAIL' for q in units) else 'UNVERIFIED'
    return {'numerical_price_status': 'FAIL' if reasons else 'PASS', 'reasons': sorted(set(reasons)),
            'cross_check': check, 'service_path_independence': 'PASS' if independent else 'FAIL',
            'underlying_feed_independence': 'UNVERIFIED', 'quantity_pair_status': quantity,
            'quantity_units': units, 'volume_comparison': None,
            'object_quality_status': 'FAIL' if reasons or quantity == 'FAIL' else 'UNVERIFIED',
            'buy_permission': False, 'research_permission': False}


def journal_counts(events):
    count = lambda event: sum(e.get('event') == event for e in events)
    return {'sdk_http_calls': count('session_start'), 'http_transport_attempts_started': count('wire_start'),
            'transport_attempts_pending': count('wire_start') - count('wire_end') - count('wire_error')}


def probe(adapter, symbol, start, end, trace, bs_path=None):
    if adapter in ('index_tx', 'index_bs') and symbol not in SYMBOLS: raise ValueError('exact index identity required')
    target = old.output_path(trace); target.parent.mkdir(parents=True, exist_ok=True); target.write_text('')
    nonce = uuid.uuid4().hex
    spec = dict(adapter=adapter, symbol=symbol, start=start, end=end, trace=str(target), run_nonce=nonce, bs_path=bs_path)
    packet = dict(adapter=adapter, symbol=symbol, bars=[], application_attempts=1, explicit_retries=0)
    tick = time.monotonic()
    try:
        result = subprocess.run([sys.executable, '-m', 'poc.index_sources', '--child'], input=json.dumps(spec),
                                text=True, capture_output=True, timeout=22, cwd=ROOT, check=False)
        answer = json.loads(result.stdout)
        if result.returncode or answer.get('run_nonce') != nonce: raise ValueError('worker identity')
        packet.update(answer)
    except subprocess.TimeoutExpired:
        packet.update(status='FAIL', failure_stage='process_deadline', error_chain=['TimeoutExpired'])
    except (OSError, ValueError) as exc:
        packet.update(status='FAIL', failure_stage='worker_protocol', error_chain=old.error_chain(exc))
    events = [json.loads(line) for line in target.read_text().splitlines() if line]
    packet.update(journal_counts=journal_counts(events), journal=events, duration_seconds=round(time.monotonic()-tick, 3))
    return packet


def worker():
    spec = json.load(sys.stdin); target = old.output_path(spec['trace'])
    def event(name, **details):
        with target.open('a') as f: f.write(json.dumps(dict(event=name, at=iso_now(), **details))+'\n')
    packet = dict(adapter=spec['adapter'], symbol=spec['symbol'], run_nonce=spec['run_nonce'], bars=[],
                  evidence_kind='real_network', cached=False, collected_at=iso_now(), quantity_scope='unknown', scope_evidence=None)
    stage = 'dependency_import'; responses = []
    try:
        import requests
        import urllib3
        from urllib.parse import urlsplit
        original = requests.Session.request
        wire = urllib3.connectionpool.HTTPConnectionPool._make_request
        def tracked(session, method, url, **kwargs):
            parsed = urlsplit(url); event('session_start', host=parsed.hostname, path=parsed.path)
            if kwargs.get('timeout') is None: kwargs['timeout'] = (5, 8)
            try:
                response = original(session, method, url, **kwargs)
                event('session_end', status=response.status_code)
                responses.append({'host': parsed.hostname, 'path': parsed.path, 'body': response.text})
                return response
            except Exception as exc:
                event('session_error', error_chain=old.error_chain(exc)); raise
        def transport(pool, *args, **kwargs):
            event('wire_start', host=pool.host)
            try:
                result = wire(pool, *args, **kwargs); event('wire_end', status=result.status); return result
            except Exception as exc:
                event('wire_error', error_chain=old.error_chain(exc)); raise
        requests.Session.request = tracked
        urllib3.connectionpool.HTTPConnectionPool._make_request = transport
        stage = 'request_or_decode'; event('phase', phase=stage)
        adapter = spec['adapter']; symbol = spec['symbol']
        if adapter not in ('index_tx', 'index_bs'):
            request = {'adapter': adapter, 'code': symbol, 'start': spec['start'], 'end': spec['end']}
            sys.stdin = io.StringIO(json.dumps(request)); stream = io.StringIO()
            with contextlib.redirect_stdout(stream): old.child()
            packet.update(json.loads(stream.getvalue()))
            if adapter in ('index_sina', 'index_em'):
                index_symbol = ('sh' if symbol == '000001' else 'sz') + symbol
                packet.update(symbol=index_symbol, exchange=index_symbol[:2], kind='index', provider=PROVIDERS[adapter],
                              identity_status='PASS', volume_unit='unknown' if adapter == 'index_sina' else 'shares',
                              amount_unit='unknown' if adapter == 'index_sina' else 'CNY')
                packet['bars'] = normalize_index(adapter, packet.get('raw_records', []), spec['start'], spec['end'])
        else:
            if symbol not in SYMBOLS: raise ValueError('exact index identity required')
            packet.update(provider=PROVIDERS[adapter], exchange=symbol[:2], kind='index', volume_unit='shares',
                          amount_unit='CNY' if adapter == 'index_bs' else 'unknown', basis='unadjusted')
            if adapter == 'index_tx':
                import akshare as ak
                if ak.__version__ != '1.19.1': raise RuntimeError('unaudited SDK version')
                frame = ak.stock_zh_index_daily_tx(symbol=symbol, start_date=spec['start'].replace('-', ''), end_date=spec['end'].replace('-', ''))
                records = json.loads(frame.to_json(orient='records', date_format='iso'))
                payloads = [json.loads(r['body'].split('=', 1)[1]) for r in responses if r['host'] == 'proxy.finance.qq.com']
                branches = [p['data'][symbol] for p in payloads]
                packet['response_branches'] = ['day' if 'day' in p else 'qfqday' for p in branches]
                packet['basis'] = 'unadjusted' if branches and all('day' in p for p in branches) else 'UNVERIFIED'
                packet['identity_status'] = 'PASS' if branches else 'FAIL'
                raw_days = [str(r[0])[:10] for branch in branches for r in branch.get('day', branch.get('qfqday', []))
                            if spec['start'] <= str(r[0])[:10] <= spec['end']]
                if len(raw_days) != len(set(raw_days)) or len(raw_days) != len(records):
                    raise ValueError('SDK deduplication or response window mismatch')
            else:
                sys.path.insert(0, str(old.output_path(spec['bs_path'])))
                import baostock as bs
                import socket
                from baostock.common import context, contants
                if getattr(context, 'apiKey', '0') not in ('0', None): raise ValueError('paid key forbidden')
                if contants.BAOSTOCK_SERVER_IP != 'public-api.baostock.com': raise ValueError('public endpoint required')
                original_socket = socket.socket
                class BoundedSocket(original_socket):
                    def connect(self, address):
                        self.settimeout(8); event('tcp_connect_start', host=address[0], port=address[1])
                        try: result = super().connect(address); event('tcp_connect_end'); return result
                        except Exception as exc: event('tcp_error', error_chain=old.error_chain(exc)); raise
                    def recv(self, size, *args):
                        value = super().recv(size, *args)
                        if not value: raise EOFError('public server closed stream')
                        return value
                socket.socket = BoundedSocket
                stage = 'anonymous_login'; event('phase', phase=stage)
                with contextlib.redirect_stdout(io.StringIO()): login = bs.login()
                packet['login_error_code'] = login.error_code
                if login.error_code != '0': raise RuntimeError('anonymous login failed')
                stage = 'index_query'; event('phase', phase=stage)
                result = bs.query_history_k_data_plus(symbol[:2]+'.'+symbol[2:],
                    'date,code,open,high,low,close,volume,amount,adjustflag', start_date=spec['start'], end_date=spec['end'], frequency='d', adjustflag='3')
                packet['query_error_code'] = result.error_code
                if result.error_code != '0': raise RuntimeError('index query failed')
                records = []
                while result.next(): records.append(dict(zip(result.fields, result.get_row_data())))
                packet['identity_status'] = 'PASS' if records and all(r['code'] == symbol[:2]+'.'+symbol[2:] and r['adjustflag'] == '3' for r in records) else 'FAIL'
                with contextlib.redirect_stdout(io.StringIO()): bs.logout()
            packet['raw_records'] = records
            packet['bars'] = normalize_index(adapter, records, spec['start'], spec['end'])
            packet.update(status='PASS' if packet['bars'] and packet['identity_status'] == 'PASS' else 'FAIL', failure_stage=None)
    except ImportError as exc:
        packet.update(status='BLOCKED_DEPENDENCY', failure_stage=stage, error_chain=old.error_chain(exc))
    except Exception as exc:
        packet.update(status='FAIL', failure_stage=stage, error_chain=old.error_chain(exc))
    packet.update(run_nonce=spec['run_nonce'], raw_http=responses, collected_at=iso_now())
    print(json.dumps(packet, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    if '--child' in sys.argv: worker()
