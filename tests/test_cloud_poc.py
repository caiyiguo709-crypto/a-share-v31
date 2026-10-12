"""Additional cloud contracts; original 103 and backup 38 tests remain unchanged."""
import copy
from datetime import datetime

import pytest
import yaml

from ashare.fixtures import synthetic_dataset
from poc.backup_sources import CONTRACTS, ROOT, assess_index, index_records
from poc.cloud_report import public_packet, test_summary as summarize_tests


def test_workflow_dispatch_only_and_no_publication():
    config = yaml.load((ROOT / '.github/workflows/collect.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(config['on']) == {'workflow_dispatch'}
    assert config['permissions'] == {'contents': 'read'}
    steps = config['jobs']['diagnose']['steps']
    assert steps[0]['with']['persist-credentials'] == 'false'
    uploads = [s for s in steps if s.get('uses', '').startswith('actions/upload-artifact@')]
    assert uploads[0]['if'] == '${{ always() }}'
    assert 'site/' not in uploads[0]['with']['path']
    text = (ROOT / '.github/workflows/collect.yml').read_text()
    assert 'secrets.' not in text and 'ashare.cli' not in text and 'schedule:' not in text


def test_public_packet_removes_raw_responses_credentials_and_private_fields():
    packet = {'raw_response': 'secret', 'raw_records': [{'token': 'secret'}], 'password': 'secret',
              'private_account': {'positions': 'secret'}, 'bars': [{'date': '2026-10-09', 'close': 10, 'token': 'secret'}],
              'http_requests': [{'url': 'https://user:secret@proxy', 'http_status': 200, 'headers': {'token': 'secret'}}]}
    result = public_packet(packet)
    assert 'secret' not in str(result)
    assert result['bars'][0]['close'] == 10 and result['http_requests'] == [{'http_status': 200}]


def test_index_em_maps_hands_to_shares_and_preserves_amount():
    rows = [{'日期': '2026-10-09', '开盘': 3000, '最高': 3100, '最低': 2900, '收盘': 3050,
             '成交量': 120, '成交额': 300000}]
    result = index_records(rows, 'index_em')
    assert result[0]['volume'] == 12000 and result[0]['amount'] == 300000
    assert rows[0]['成交量'] == 120


def test_index_sina_missing_turnover_is_not_filled():
    assert index_records([{'date': '2026-10-09', 'volume': 1200}], 'index_sina')[0]['amount'] is None
    with pytest.raises(ValueError): index_records([], 'unknown')


@pytest.mark.parametrize('fault', ['none', 'same_upstream', 'empty', 'wrong_code', 'stale_clock'])
def test_index_provenance_and_units_never_grant_permission(fault):
    raw = synthetic_dataset(2)['snapshots'][0]
    now = datetime.fromisoformat(raw['collected_at'])
    packets = [{**CONTRACTS[a], 'adapter': a, 'code': '000001', 'status': 'PASS',
                'evidence_kind': 'real_network', 'collected_at': now.isoformat(),
                'bars': copy.deepcopy(raw['indices']['000001'])} for a in ('index_sina', 'index_em')]
    if fault == 'same_upstream': packets[1]['upstream'] = 'sina'
    elif fault == 'empty': packets[1]['bars'] = []
    elif fault == 'wrong_code': packets[1]['code'] = '399001'
    elif fault == 'stale_clock': packets[1]['collected_at'] = '2000-01-01T16:00:00+08:00'
    result = assess_index(*packets, raw['calendar'], now)
    assert result['price_quality_status'] == ('PASS' if fault == 'none' else 'FAIL')
    assert result['quantity_units']['index_sina']['status'] != 'PASS'
    assert not result['buy_permission'] and not result['research_permission']


def test_test_summary_never_promotes_missing_skipped_or_partial_results(tmp_path):
    path = tmp_path / 'results.xml'
    assert summarize_tests(path, 38, None)['status'] == 'NOT_RUN'
    path.write_text('<testsuites><testsuite><testcase name="a"><skipped/></testcase></testsuite></testsuites>')
    assert summarize_tests(path, 1, 0)['status'] == 'FAIL'
    path.write_text('<testsuites><testsuite><testcase name="a"/></testsuite></testsuites>')
    assert summarize_tests(path, 38, 0)['status'] == 'FAIL'


def test_test_summary_drops_failure_messages_and_stdout(tmp_path):
    path = tmp_path / 'results.xml'
    path.write_text('<testsuites><testsuite><testcase name="a"><failure message="secret">secret</failure>'
                   '<system-out>token=secret</system-out></testcase></testsuite></testsuites>')
    result = summarize_tests(path, 1, 1)
    assert result['counts']['FAIL'] == 1 and 'secret' not in str(result)
