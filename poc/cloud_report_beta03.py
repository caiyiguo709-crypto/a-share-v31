"""One manual Beta03B run; explicit public evidence whitelist, no trading."""
import argparse
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from poc.cloud_report import save, test_summary, public_packet, COUNTS
from poc.index_sources import ROOT, probe, evaluate_pair, quantity_check
from poc.backup_sources import assess_pair
from ashare.config import WATCHLIST, INDICES
from ashare.stage1 import TZ, expected_date

WORK = ROOT / 'private/beta03-cloud'
OUT = ROOT / 'beta03-artifacts'
GROUPS = {'original': (103, ['tests/test_safety.py', 'tests/test_stage2.py']),
          'backup': (38, ['tests/test_backup_poc.py']),
          'cloud_contracts': (11, ['tests/test_cloud_poc.py']),
          'decoder_beta02': (27, ['tests/test_decoder_beta02.py']),
          'index_beta03': (32, ['tests/test_index_beta03.py'])}
WHITELIST = ('acceptance.json', 'acceptance.md', 'original-tests.json', 'backup-tests.json',
             'cloud_contracts-tests.json', 'decoder_beta02-tests.json', 'index_beta03-tests.json', 'sha256.json')


def counts(packets, expected):
    result = dict.fromkeys(COUNTS, 0)
    for p in packets: result[p.get('status') if p.get('status') in COUNTS else 'FAIL'] += 1
    result['NOT_RUN'] += max(0, expected - len(packets))
    return result


def export_packet(packet):
    result = public_packet(packet)
    for key in ('symbol', 'exchange', 'kind', 'provider', 'identity_status', 'quantity_scope',
                'cached', 'login_error_code', 'query_error_code', 'response_branches', 'journal_counts'):
        if key in packet: result[key] = packet[key]
    # Explicit per-bar public market whitelist. Never copy records or responses.
    for clean, raw in zip(result['bars'], packet.get('bars', [])):
        for key in ('volume_observed', 'volume_shares', 'amount_cny'):
            value = raw.get(key)
            clean[key] = value if isinstance(value, (int, float)) and math.isfinite(value) else None
    result['returned_data_count'] = len(result['bars'])
    result['valid_data_count'] = sum(bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(b.get('date')))) and
        all(isinstance(b.get(k), (int, float)) and math.isfinite(b[k]) and b[k] > 0 for k in ('open', 'high', 'low', 'close', 'volume')) and
        b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high'] for b in result['bars'])
    providers = {'calendar_sina': 'sina', 'sina_raw': 'sina', 'tencent_sdk': 'tencent', 'index_sina': 'sina', 'index_em': 'eastmoney', 'index_tx': 'tencent', 'index_bs': 'baostock'}
    result['provider'] = providers.get(packet.get('adapter'), 'unknown')
    symbol = packet.get('symbol', '')
    result['request_symbol'] = symbol[:2]+'.'+symbol[2:] if packet.get('adapter') == 'index_bs' else symbol
    result['latest_date'] = result['bars'][-1]['date'] if result['bars'] else None
    result['protocol_events'] = [{k: e[k] for k in ('event', 'at', 'status', 'phase', 'port', 'error_chain') if k in e}
                                 for e in packet.get('journal', [])]
    result['buy_permission'] = result['research_permission'] = False
    return result


def network():
    now = datetime.now(TZ)
    start = (now - timedelta(days=220)).date().isoformat()
    calendar_packet = probe('calendar_sina', '', '', '', WORK / 'calendar.jsonl')
    calendar = calendar_packet.get('calendar', []) if calendar_packet.get('status') == 'PASS' else []
    end = expected_date(calendar, now)
    # Still execute every matrix slot if the calendar fails; quality cannot pass.
    query_end = end or now.date().isoformat()
    original_cases = [(a, c) for c in WATCHLIST for a in ('sina_raw', 'tencent_sdk')]
    original_cases += [(a, c) for c in INDICES for a in ('index_sina', 'index_em')]
    candidates = [(a, s) for a in ('index_tx', 'index_bs') for s in ('sh000001', 'sz399001', 'sz399006')]
    report = {'started_at': now.isoformat(), 'expected_date': end, 'original': [calendar_packet], 'candidates': [],
              'index_pairs': [], 'stock_pairs': [], 'quantity': [], 'buy_permission': False, 'research_permission': False}
    save(WORK / 'network.json', report)
    def request(case):
        a, s = case
        return probe(a, s, start, query_end, WORK / f'{a}-{s}.jsonl', str(WORK / 'baostock-isolated'))
    for section, cases in (('original', original_cases), ('candidates', candidates)):
        with ThreadPoolExecutor(max_workers=2) as pool:
            for packet in pool.map(request, cases):
                report[section].append(packet)
                save(WORK / 'network.json', report)
    packets = {(p['adapter'], p.get('code', p.get('symbol'))): p for p in report['original']}
    candidate_packets = {(p['adapter'], p['symbol']): p for p in report['candidates']}
    for symbol in ('sh000001', 'sz399001', 'sz399006'):
        left, right = candidate_packets['index_tx', symbol], candidate_packets['index_bs', symbol]
        result = evaluate_pair(left, right, calendar, datetime.now(TZ)) if end else {
            'numerical_price_status': 'NOT_RUN', 'object_quality_status': 'UNVERIFIED', 'reasons': ['calendar_unverified'],
            'underlying_feed_independence': 'UNVERIFIED', 'buy_permission': False, 'research_permission': False}
        # Observations only; no ratio/comparison across unverified quantity scopes.
        result['observed_latest_volumes'] = {p['adapter']: p['bars'][-1].get('volume_shares') if p.get('bars') else None for p in (left, right)}
        report['index_pairs'].append({'symbol': symbol, **result})
    if end:
        report['stock_pairs'] = [assess_pair(packets['sina_raw', c], packets['tencent_sdk', c], calendar, datetime.now(TZ)) for c in WATCHLIST]
    report['quantity'] = [{'adapter': p['adapter'], 'symbol': p['symbol'], **quantity_check(p)} for p in report['candidates']]
    report['finished_at'] = datetime.now(TZ).isoformat()
    save(WORK / 'network.json', report)


def finalize():
    codes_path = WORK / 'exit-codes.json'
    codes = json.loads(codes_path.read_text()) if codes_path.exists() else {}
    groups = {n: test_summary(WORK / (n+'.xml'), count, codes.get(n)) for n, (count, _) in GROUPS.items()}
    path = WORK / 'network.json'; raw = json.loads(path.read_text()) if path.exists() else {}
    original, candidates = raw.get('original', []), raw.get('candidates', [])
    original_counts, candidate_counts = counts(original, 21), counts(candidates, 6)
    sha, run_id = os.environ.get('GITHUB_SHA', ''), os.environ.get('GITHUB_RUN_ID', '')
    operational = codes.get('network') == 0 and all(g['status'] == 'PASS' for g in groups.values()) and len(original) == 21 and candidate_counts['PASS'] == 6 and len(raw.get('index_pairs', [])) == 3 and all(p['numerical_price_status'] == 'PASS' for p in raw.get('index_pairs', []))
    status = 'PARTIAL' if original_counts['PASS'] or candidate_counts['PASS'] else 'BLOCKED'
    report = {'scope': 'V3.1 Beta0.3B one manual run; isolated observation only', 'status': status,
              'python_version': platform.python_version(),
              'observation_objectives_passed': operational, 'generated_at': datetime.now(timezone.utc).isoformat(),
              'commit': sha if re.fullmatch(r'[0-9a-f]{40}', sha) else None,
              'run_url': f'https://github.com/caiyiguo709-crypto/a-share-v31/actions/runs/{run_id}' if run_id.isdigit() else None,
              'started_at': raw.get('started_at'), 'finished_at': raw.get('finished_at'), 'expected_date': raw.get('expected_date'),
              'test_groups': groups, 'original21_counts': original_counts, 'candidate6_counts': candidate_counts,
              'original21': [export_packet(p) for p in original], 'candidate6': [export_packet(p) for p in candidates],
              'index_pairs': raw.get('index_pairs', []), 'stock_pairs': raw.get('stock_pairs', []), 'quantity': raw.get('quantity', []),
              'failure_interfaces': [{'adapter': p.get('adapter'), 'symbol': p.get('symbol'), 'code': p.get('code'), 'status': p.get('status'), 'failure_stage': p.get('failure_stage'), 'error_chain': p.get('error_chain', [])} for p in original+candidates if p.get('status') != 'PASS'],
              'risk_interception': {'buy_permission': False, 'research_permission': False, 'production_accepted': False,
                                   'offline_evidence': ['original-tests.json', 'backup-tests.json', 'cloud_contracts-tests.json', 'decoder_beta02-tests.json', 'index_beta03-tests.json']},
              'remaining': ['quantity scope unknown; no market volume signals', 'underlying feed independence UNVERIFIED', 'Tencent historical CNY turnover null', 'ChiNext quantity discrepancy UNVERIFIED', 'five trading sessions PENDING_SESSION', 'production integration NOT_RUN'],
              'commands': {n: 'python -m pytest -q -p no:cacheprovider '+ ' '.join(args) for n, (_, args) in GROUPS.items()},
              'network_exit_code': codes.get('network'), 'runner_error': json.loads((WORK/'runner-error.json').read_text()) if (WORK/'runner-error.json').exists() else None, 'paid_permissions_used': False}
    save(OUT/'acceptance.json', report)
    for name, group in groups.items(): save(OUT/(name+'-tests.json'), group)
    lines = ['# V3.1 Beta 0.3B 云端验收', '', f'状态：{status}。数值价格通过不能替代完整P0质量。',
             f"Commit：{report['commit']}", f"Actions：{report['run_url']}", f"生成时间：{report['generated_at']}", '',
             '|测试组|PASS|FAIL|BLOCKED_DEPENDENCY|PENDING_SESSION|NOT_RUN|', '|---|---:|---:|---:|---:|---:|']
    for name, group in groups.items(): lines.append('|'+name+'|'+ '|'.join(str(group['counts'][s]) for s in COUNTS)+'|')
    lines += ['', 'F 原21项：'+json.dumps(original_counts), 'G 新候选6项：'+json.dumps(candidate_counts), '',
              'H 指数交叉验证 / I 量额验证：见acceptance.json，分别报告价格、单位、范围及独立性。',
              'J 所有PoC买入/研究许可=false；失败与缺失不放行。离线拦截证据独立于真实行情。', '',
              '|接口|标的|状态|耗时秒|最新日期|有效条数|量单位|额单位|统计范围|错误类型|', '|---|---|---|---:|---|---:|---|---|---|---|']
    for p in report['original21']+report['candidate6']:
        lines.append('|'+ '|'.join(str(p.get(k)) for k in ('adapter','symbol','status','duration_seconds','latest_date','valid_data_count','volume_unit','amount_unit','quantity_scope'))+'|'+ ','.join(p.get('error_chain', []))+'|')
    lines += ['', '完整OHLC、归一化成交量原值/股数、成交额null和协议事件类型见JSON白名单字段。未上传原HTTP、private目录或异常文本。',
              '创业板数量仅保留两个公开来源原值，不做跨未知范围比值或拼接。股×100转换沿用已核验文档。',
              '未确认：底层供应链独立性、量能统计范围和创业板差异原因。不能依赖市场量能产生买入信号。',
              '持续观察条件只由observation_objectives_passed标记本次基本可运行性；长期稳定性仍PENDING_SESSION。',
              '未执行：正式接入、main合并、Pages、定时任务、交易执行、收费权限。阶段结束暂停，不自动重跑。']
    OUT.mkdir(parents=True, exist_ok=True); (OUT/'acceptance.md').write_text('\n'.join(lines)+'\n')
    save(OUT/'sha256.json', {name: hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in WHITELIST if name != 'sha256.json'})
    print(json.dumps({'status': status, 'observation_objectives_passed': operational, 'original21': original_counts, 'candidate6': candidate_counts}))
    return 0 if operational else 2


def run():
    WORK.mkdir(parents=True, exist_ok=True); OUT.mkdir(parents=True, exist_ok=True)
    # A new run must not reuse old results. Preserve only the pinned isolated dependency.
    for name in GROUPS:
        for suffix in ('.xml', '.log'): (WORK/(name+suffix)).unlink(missing_ok=True)
    (WORK/'network.json').unlink(missing_ok=True)
    (WORK/'runner-error.json').unlink(missing_ok=True)
    codes = {}; save(WORK/'exit-codes.json', codes)
    try:
        for name, (_, args) in GROUPS.items():
            with (WORK/(name+'.log')).open('w') as log:
                codes[name] = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--junitxml='+str(WORK/(name+'.xml')), *args], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False).returncode
            save(WORK/'exit-codes.json', codes)
        try: network(); codes['network'] = 0
        except Exception as exc:
            # Exception messages/tracebacks may expose environment details; keep type only.
            save(WORK/'runner-error.json', {'error_type': type(exc).__name__}); codes['network'] = 2
    finally:
        save(WORK/'exit-codes.json', codes)
    return finalize()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('mode', choices=('run', 'finalize', 'tests', 'install-dependency'))
    mode = parser.parse_args().mode
    if mode == 'install-dependency':
        WORK.mkdir(parents=True, exist_ok=True)
        with (WORK/'dependency-install.log').open('w') as log:
            code = subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--only-binary=:all:', '--require-hashes',
                '--target', str(WORK/'baostock-isolated'), '-r', str(ROOT/'poc/requirements-beta03.txt')], stdout=log, stderr=subprocess.STDOUT, check=False).returncode
        print(json.dumps({'isolated_dependency': 'baostock==0.9.4', 'exit_code': code, 'hash_required': True}))
        sys.exit(code)
    if mode == 'tests':
        WORK.mkdir(parents=True, exist_ok=True); (WORK/'network.json').unlink(missing_ok=True); codes = {}
        for name, (_, args) in GROUPS.items():
            with (WORK/(name+'.log')).open('w') as log:
                codes[name] = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--junitxml='+str(WORK/(name+'.xml')), *args], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False).returncode
        save(WORK/'exit-codes.json', codes); finalize()
        sys.exit(0 if all(test_summary(WORK/(n+'.xml'), count, codes[n])['status'] == 'PASS' for n, (count, _) in GROUPS.items()) else 1)
    sys.exit(run() if mode == 'run' else finalize())
