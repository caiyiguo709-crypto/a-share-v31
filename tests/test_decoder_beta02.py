"""Offline decoder contracts. Fault fixtures are never real interface evidence."""
from types import SimpleNamespace

import pytest

from poc.backup_sources import decoder_details, decode_sina_records, sina_window, encoded_sina_payload


def records():
    return [{'date': '2026-10-09', 'open': 10, 'high': 11, 'low': 9, 'close': 10,
             'volume': 1200, 'amount': 12000}]


def sdk(backend):
    return SimpleNamespace(py_mini_racer=SimpleNamespace(MiniRacer=backend), hk_js_decode='codec')


class Legacy:
    output = records()
    def eval(self, code): assert code == 'codec'
    def call(self, function, payload):
        assert function == 'd' and payload == 'encoded'
        return self.output


class Modern(Legacy):
    closes = 0
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
    def close(self): type(self).closes += 1


@pytest.mark.parametrize('backend', [Legacy, Modern])
def test_legacy_and_modern_decoder_load_and_decode(backend):
    before = Modern.closes
    assert decode_sina_records(sdk(backend), 'encoded') == records()
    if backend is Modern: assert Modern.closes == before + 1


def test_decoder_metadata_matches_actual_sdk_import_without_private_path():
    import importlib
    module = importlib.import_module('akshare.stock.stock_zh_a_sina')
    details = decoder_details(module)
    assert details['class_module'] == module.py_mini_racer.MiniRacer.__module__
    assert details['import_module'] == module.py_mini_racer.__name__
    assert '/' not in details['module_file'] and '/Users/' not in str(details)
    assert details['packages']['akshare'] == '1.19.1'


def test_decoder_failure_propagates_and_modern_resource_is_closed():
    class Broken(Modern):
        closes = 0
        def call(self, *args): raise ValueError('offline decode fault')
    with pytest.raises(ValueError): decode_sina_records(sdk(Broken), 'encoded')
    assert Broken.closes == 1


@pytest.mark.parametrize('value', [None, {}, [], 'not bars', [None]])
def test_invalid_decoded_result_is_rejected(value):
    class Invalid(Legacy): output = value
    with pytest.raises(ValueError): decode_sina_records(sdk(Invalid), 'encoded')


@pytest.mark.parametrize('value', [None, 'invalid', '2026-02-30', '2026-10-09junk', '2099-01-01'])
def test_invalid_decoded_date_is_rejected(value):
    row = records()[0]; row['date'] = value
    with pytest.raises(ValueError): sina_window([row], '2026-03-06', '2026-10-09')


@pytest.mark.parametrize('key', ['date', 'open', 'high', 'low', 'close', 'volume', 'amount'])
def test_missing_decoded_field_is_rejected(key):
    row = records()[0]; del row[key]
    with pytest.raises(ValueError): sina_window([row], '2026-03-06', '2026-10-09')


def test_window_preserves_rows_and_excludes_incomplete_later_session():
    row = records()[0]; later = {**row, 'date': '2026-10-12'}
    assert sina_window([row, later], '2026-03-06', '2026-10-09') == [row]
    with pytest.raises(ValueError): sina_window([later], '2026-03-06', '2026-10-09')


def test_response_identity_rejects_wrong_security_before_decoder():
    assert encoded_sina_payload('var KLC_K2_sz000983="encoded";', 'sz000983') == 'encoded'
    with pytest.raises(ValueError): encoded_sina_payload('var KLC_K2_sh601001="encoded";', 'sz000983')


@pytest.mark.parametrize('fault', ['duplicate', 'missing_session', 'future_date', 'missing_price'])
def test_decoder_faults_keep_both_permissions_closed(fault):
    import copy
    from datetime import datetime
    from ashare.fixtures import synthetic_dataset
    from poc.backup_sources import assess_pair, CONTRACTS
    raw = synthetic_dataset(2)['snapshots'][0]; code = raw['watchlist'][0]
    packets = [{**CONTRACTS[a], 'adapter': a, 'code': code, 'status': 'PASS',
                'evidence_kind': 'real_network', 'collected_at': raw['collected_at'],
                'bars': copy.deepcopy(raw['stocks'][code])} for a in ('sina_raw', 'tencent_sdk')]
    bars = packets[0]['bars']
    if fault == 'duplicate': bars[-1]['date'] = bars[-2]['date']
    elif fault == 'missing_session': bars.pop(-3)
    elif fault == 'future_date': bars[-1]['date'] = '2099-01-01'
    elif fault == 'missing_price': bars[-1]['close'] = None
    result = assess_pair(*packets, raw['calendar'], datetime.fromisoformat(raw['collected_at']))
    assert result['price_quality_status'] != 'PASS'
    assert not result['buy_permission'] and not result['research_permission']
