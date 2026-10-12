"""Offline fault injection only; these fixtures are never network evidence."""
import copy
import json
import subprocess
from datetime import datetime, timedelta

import pytest

from ashare.fixtures import synthetic_dataset
from poc.backup_sources import assess_pair, normalize_records, probe, output_path, error_chain, encoded_sina_payload, tencent_stock_records, unit_sanity


@pytest.fixture
def pair():
    raw = synthetic_dataset(2)['snapshots'][0]
    code = raw['watchlist'][0]
    now = datetime.fromisoformat(raw['collected_at'])
    packets = []
    for adapter, upstream in [('sina_raw', 'sina'), ('tencent_sdk', 'tencent')]:
        packets.append({'adapter': adapter, 'upstream': upstream, 'code': code,
                        'basis': 'unadjusted', 'status': 'PASS', 'evidence_kind': 'real_network',
                        'collected_at': now.isoformat(), 'bars': copy.deepcopy(raw['stocks'][code])})
    return packets, raw['calendar'], now


def test_two_valid_independent_sources_are_observations_only(pair):
    packets, calendar, now = pair
    result = assess_pair(*packets, calendar, now)
    assert result['price_quality_status'] == 'PASS'
    assert result['research_permission'] is False and result['buy_permission'] is False
    assert result['missing_acceptance'] == ['security_metadata', 'corporate_actions', 'industry', 'funds']


@pytest.mark.parametrize('failure', ['empty', 'stale', 'future', 'duplicate', 'gap', 'nan', 'volume', 'conflict'])
def test_price_faults_never_pass(pair, failure):
    packets, calendar, now = pair
    bars = packets[1]['bars']
    if failure == 'empty': bars.clear()
    elif failure == 'stale': bars.pop()
    elif failure == 'future': bars[-1]['date'] = '2099-01-01'
    elif failure == 'duplicate': bars[-1]['date'] = bars[-2]['date']
    elif failure == 'gap': bars.pop(-4)
    elif failure == 'nan': bars[-1]['close'] = float('nan')
    elif failure == 'volume': bars[-1]['volume'] = 0
    elif failure == 'conflict':
        bars[-1]['close'] *= 1.003
        bars[-1]['high'] = max(bars[-1]['close'], bars[-1]['high'])
    result = assess_pair(*packets, calendar, now)
    assert result['price_quality_status'] != 'PASS'
    assert not result['research_permission'] and not result['buy_permission']


@pytest.mark.parametrize('failure', ['same_adapter', 'forged_upstream', 'different_code', 'qfq', 'synthetic', 'request_failure', 'clock', 'old_clock', 'calendar'])
def test_provenance_and_clock_faults_never_pass(pair, failure):
    packets, calendar, now = pair
    if failure == 'same_adapter': packets[1] = copy.deepcopy(packets[0])
    elif failure == 'forged_upstream': packets[1]['upstream'] = 'sina'
    elif failure == 'different_code': packets[1]['code'] = '600000'
    elif failure == 'qfq': packets[1]['basis'] = 'qfq'
    elif failure == 'synthetic': packets[1]['evidence_kind'] = 'synthetic'
    elif failure == 'request_failure': packets[1]['status'] = 'FAIL'
    elif failure == 'clock': packets[1]['collected_at'] = None
    elif failure == 'old_clock': packets[1]['collected_at'] = (now - timedelta(hours=37)).isoformat()
    elif failure == 'calendar': calendar = []
    assert assess_pair(*packets, calendar, now)['price_quality_status'] != 'PASS'


def test_normalization_preserves_amount_and_sdk_share_units():
    rows = [{'date': '2026-10-09', 'open': 10, 'high': 11, 'low': 9, 'close': 10,
             'volume': 1200, 'amount': 12000}]
    assert normalize_records(rows)[0]['volume'] == 1200
    assert normalize_records(rows)[0]['amount'] == 12000


def test_missing_fields_are_not_filled_and_order_not_repaired():
    rows = [{'date': '2026-10-09', 'open': 10}, {'date': '2026-10-08', 'open': 9}]
    normalized = normalize_records(rows)
    assert [r['date'] for r in normalized] == ['2026-10-09', '2026-10-08']
    assert normalized[0]['close'] is None and normalized[1]['amount'] is None


def test_error_chain_keeps_cause_types_without_proxy_credentials():
    inner = ConnectionError('https://user:secret@private-proxy:8080')
    outer = RuntimeError('token=private-value')
    outer.__cause__ = inner
    chain = error_chain(outer)
    assert chain == ['RuntimeError', 'ConnectionError']
    assert 'secret' not in json.dumps(chain)


def test_timeout_is_a_failure_with_unknown_http_attempts(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('worker', 22)
    monkeypatch.setattr(subprocess, 'run', timeout)
    result = probe('sina_raw', '601001', '2026-03-05', '2026-10-09')
    assert result['status'] == 'FAIL' and result['failure_stage'] == 'process_deadline'
    assert result['application_attempts'] == 1 and result['sdk_http_attempts'] is None
    assert result['bars'] == []


@pytest.mark.parametrize('path', ['site/data/poc.json', 'docs/poc.json', 'private/../site/poc.json'])
def test_output_cannot_enter_public_directories(path):
    with pytest.raises(ValueError): output_path(path)


def test_private_output_rejects_symlink_escape(tmp_path, monkeypatch):
    import poc.backup_sources as module
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path/'private').mkdir()
    (tmp_path/'site').mkdir()
    (tmp_path/'private'/'escape').symlink_to(tmp_path/'site', target_is_directory=True)
    with pytest.raises(ValueError): output_path(str(tmp_path/'private'/'escape'/'poc.json'))


def test_unknown_adapter_is_rejected_before_network():
    with pytest.raises(ValueError): probe('unapproved_vendor', '601001', '2026-03-05', '2026-10-09')


def test_real_sina_klc2_response_uses_k2_variable():
    # Header observed in the first real request; body shortened, offline regression only.
    assert encoded_sina_payload('var KLC_K2_sh601001="encoded";', 'sh601001') == 'encoded'


@pytest.mark.parametrize('response', ['var KLC_K2_sh600000="encoded";', '<html>maintenance</html>'])
def test_sina_wrong_security_or_nondata_response_is_rejected(response):
    with pytest.raises(ValueError): encoded_sina_payload(response, 'sh601001')


@pytest.mark.parametrize('code,volume,amount,low,high', [
    ('000983', 740894, 490175700, 6.56, 6.72),
    ('000066', 803718, 1100909300, 13.5, 14.1),
])
def test_observed_tencent_000_stock_unit_bug(code, volume, amount, low, high):
    # Values observed in real 2026-10-09 responses; offline regression only.
    rows = [{'date': '2026-10-09', 'open': low, 'close': high,
             'low': low, 'high': high, 'volume': volume, 'amount': amount}]
    assert unit_sanity(normalize_records(rows))['status'] == 'FAIL'
    bars = tencent_stock_records(rows, code)
    assert bars[0]['volume'] == volume * 100
    assert rows[0]['volume'] == volume  # Preserve SDK response.
    assert unit_sanity(bars)['status'] == 'PASS'


def test_tencent_other_mainboard_stock_is_not_scaled_twice():
    rows = [{'date': '2026-10-09', 'volume': 1200, 'amount': 12000}]
    assert tencent_stock_records(rows, '600000')[0]['volume'] == 1200
    with pytest.raises(ValueError): tencent_stock_records(rows, '399001')


@pytest.mark.parametrize('amount', [None, 0, -1, float('nan'), 1000000])
def test_invalid_or_mismatched_amount_cannot_pass_unit_sanity(amount):
    bars = [{'date': '2026-10-09', 'low': 9, 'high': 11, 'volume': 100, 'amount': amount}]
    assert unit_sanity(bars)['status'] == 'FAIL'
