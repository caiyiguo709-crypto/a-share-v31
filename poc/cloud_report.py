"""Beta 0.2 evidence runner. No account data, publication or trading paths."""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'private' / 'beta02-cloud'
OUT = ROOT / 'beta02-artifacts'
COUNTS = ('PASS', 'FAIL', 'BLOCKED_DEPENDENCY', 'PENDING_SESSION', 'NOT_RUN')
GROUPS = {'original': (103, ['tests', '--ignore=tests/test_backup_poc.py', '--ignore=tests/test_cloud_poc.py',
                            '--ignore=tests/test_decoder_beta02.py']),
          'backup': (38, ['tests/test_backup_poc.py']),
          'cloud_contracts': (11, ['tests/test_cloud_poc.py']),
          'decoder_beta02': (27, ['tests/test_decoder_beta02.py'])}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def test_summary(path, expected, exit_code):
    result = {'expected': expected, 'exit_code': exit_code, 'counts': dict.fromkeys(COUNTS, 0), 'tests': []}
    if not path.exists():
        result['counts']['NOT_RUN'] = expected or 1
        result['status'] = 'NOT_RUN'
        return result
    root = ET.parse(path).getroot()
    for case in root.iter('testcase'):
        status = 'FAIL' if case.find('failure') is not None or case.find('error') is not None else (
            'NOT_RUN' if case.find('skipped') is not None else 'PASS')
        result['counts'][status] += 1
        # Do not export traceback, stdout, environment, or failure messages.
        result['tests'].append({'name': case.get('classname', '') + '::' + case.get('name', ''), 'status': status})
    passed = len(result['tests']) == expected if expected is not None else bool(result['tests'])
    result['status'] = 'PASS' if passed and exit_code == 0 and not result['counts']['FAIL'] and not result['counts']['NOT_RUN'] else 'FAIL'
    return result


def public_packet(packet):
    """Export only known public market fields; exclude raw payloads, URLs and logs."""
    fields = ('adapter', 'upstream', 'interface', 'code', 'basis', 'volume_unit', 'amount_unit',
              'status', 'evidence_kind', 'started_at', 'collected_at', 'akshare_version',
              'application_attempts', 'explicit_retries', 'sdk_http_attempts', 'duration_seconds',
              'failure_stage', 'error_chain', 'unit_conversion', 'decoder')
    result = {k: packet[k] for k in fields if k in packet}
    result['http_requests'] = [{k: event[k] for k in ('method', 'http_status', 'started_at', 'proxy_configured', 'error_chain')
                                if k in event} for event in packet.get('http_requests', [])]
    result['bars'] = []
    for row in packet.get('bars', []):
        clean = {'date': row.get('date') if re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(row.get('date'))) else None}
        for key in ('open', 'high', 'low', 'close', 'volume', 'amount'):
            value = row.get(key)
            clean[key] = value if isinstance(value, (int, float)) and math.isfinite(value) else None
        result['bars'].append(clean)
    return result


def finalize():
    WORK.mkdir(parents=True, exist_ok=True)
    codes_path = WORK / 'exit-codes.json'
    codes = json.loads(codes_path.read_text()) if codes_path.exists() else {}
    groups = {name: test_summary(WORK / (name + '.xml'), expected, codes.get(name))
              for name, (expected, _) in GROUPS.items()}
    source_path = WORK / 'network.json'
    raw = json.loads(source_path.read_text()) if source_path.exists() else {}
    packets = ([raw['calendar_probe']] if 'calendar_probe' in raw else []) + raw.get('probes', [])
    counts = dict.fromkeys(COUNTS, 0)
    for packet in packets:
        counts[packet['status'] if packet.get('status') in COUNTS else 'FAIL'] += 1
    counts['NOT_RUN'] += max(0, 21 - len(packets))
    pairs, indices = raw.get('pairs', []), raw.get('index_pairs', [])
    passed = counts['PASS'] == 21 and all(g['status'] == 'PASS' for g in groups.values()) and codes.get('network') == 0
    status = 'COMPLETE' if passed else ('PARTIAL' if counts['PASS'] else 'BLOCKED')
    # COMPLETE above refers ONLY to one-run Beta scope; production acceptance is always false.
    sha = os.environ.get('GITHUB_SHA', '')
    run_id = os.environ.get('GITHUB_RUN_ID', '')
    report = {'scope': 'V3.1 Beta 0.2 one manual diagnostic run; not production acceptance',
              'status': status, 'generated_at': datetime.now(timezone.utc).isoformat(),
              'commit': sha if re.fullmatch(r'[0-9a-f]{40}', sha) else None,
              'run_url': f'https://github.com/caiyiguo709-crypto/a-share-v31/actions/runs/{run_id}' if run_id.isdigit() else None,
              'test_groups': groups, 'network_counts': counts, 'network_exit_code': codes.get('network'),
              'started_at': raw.get('started_at'), 'finished_at': raw.get('finished_at'),
              'stock_pairs': pairs, 'index_pairs': indices, 'probes': [public_packet(p) for p in packets],
              'buy_permission': False, 'research_permission': False, 'production_accepted': False,
              'remaining': ['five real trading sessions', 'metadata', 'corporate actions', 'industry', 'funds',
                            'index Sina volume unit/amount certification', 'core integration separately approved'],
              'commands': {'original': 'python -m pytest tests --ignore=tests/test_backup_poc.py --ignore=tests/test_cloud_poc.py --ignore=tests/test_decoder_beta02.py',
                           'backup': 'python -m pytest tests/test_backup_poc.py',
                           'cloud_contracts': 'python -m pytest tests/test_cloud_poc.py',
                           'decoder_beta02': 'python -m pytest tests/test_decoder_beta02.py',
                           'network': 'python -m poc.backup_sources --include-indices --output private/beta02-cloud/network.json'}}
    save(OUT / 'acceptance.json', report)
    for name, group in groups.items(): save(OUT / (name + '-tests.json'), group)
    lines = ['# V3.1 Beta 0.2 云端验收', '', f"状态：**{status}**（仅本次诊断；正式系统未验收）", '',
             f"Commit：{report['commit']}", f"运行：{report['run_url']}", '',
             '| 测试组 | 状态 | PASS | FAIL | NOT_RUN |', '|---|---|---:|---:|---:|']
    for name, group in groups.items():
        c = group['counts']; lines.append(f"| {name} | {group['status']} | {c['PASS']} | {c['FAIL']} | {c['NOT_RUN']} |")
    lines += ['', '真实逻辑请求（不与离线测试混算）：' + json.dumps(counts), '',
              '| 标的 | 价格双源 | 成交量/成交额单位 | 买入许可 |', '|---|---|---|---|']
    for pair in pairs: lines.append(f"| 股票 {pair['code']} | {pair['price_quality_status']} | {pair['unit_sanity_status']} | false |")
    for pair in indices:
        units = ', '.join(f"{k}:{v['status']}" for k, v in pair['quantity_units'].items())
        lines.append(f"| {pair['name']} {pair['code']} | {pair['price_quality_status']} | {units} | false |")
    lines += ['', '异常/数据缺失拦截：见 original-tests.json 中原安全门禁测试、backup-tests.json 中故障注入测试。离线故障注入不是实际行情。',
              '', '新浪指数金额/量单位未认证，不能据非空行情宣布完整质量通过。详情、错误类型链及公共归一化日线见 acceptance.json。',
              '', '未执行：连续五日验证、证券信息/除权/行业/资金流完整验收、正式核心接入、合并、Pages、定时任务、真实下单。',
              '', '下一阶段：待用户确认后处理失败原因和单位证据，再决定是否追加验证；不自动重跑。']
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'acceptance.md').write_text('\n'.join(lines) + '\n')
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.iterdir()) if p.name != 'sha256.json'}
    save(OUT / 'sha256.json', manifest)
    print(json.dumps({'status': status, 'network_counts': counts, 'test_groups': {k: g['status'] for k, g in groups.items()}}))
    return 0 if passed else 2


def run():
    WORK.mkdir(parents=True, exist_ok=True)
    codes = {}
    try:
        for name, (_, args) in GROUPS.items():
            with (WORK / (name + '.log')).open('w') as log:
                codes[name] = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                    '--junitxml=' + str(WORK / (name + '.xml')), *args], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False).returncode
            save(WORK / 'exit-codes.json', codes)
        with (WORK / 'network.log').open('w') as log:
            codes['network'] = subprocess.run([sys.executable, '-m', 'poc.backup_sources', '--include-indices',
                '--output', str(WORK / 'network.json')], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False).returncode
    finally:
        save(WORK / 'exit-codes.json', codes)
        result = finalize()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['run', 'finalize'])
    args = parser.parse_args()
    sys.exit(run() if args.mode == 'run' else finalize())
