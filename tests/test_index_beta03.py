"""Offline fault injection only. No fixture is counted as real network evidence."""
import copy
import json
import subprocess
from datetime import datetime, timedelta

import pytest

from ashare.fixtures import synthetic_dataset
from poc.index_sources import normalize_index, evaluate_pair, quantity_check, probe, journal_counts


@pytest.fixture
def pair():
    raw = synthetic_dataset(2)['snapshots'][0]
    now = datetime.fromisoformat(raw['collected_at'])
    packets = []
    for adapter, provider in [('index_sina', 'sina'), ('index_tx', 'tencent')]:
        bars = []
        for r in raw['indices']['000001']:
            bars.append({**r, 'volume_observed': r['volume'], 'volume_shares': r['volume'] * 100 if adapter == 'index_tx' else None,
                         'amount_cny': None, 'adapter': adapter})
        packets.append({'adapter': adapter, 'provider': provider, 'symbol': 'sh000001', 'exchange': 'sh',
                        'kind': 'index', 'status': 'PASS', 'evidence_kind': 'real_network', 'cached': False,
                        'collected_at': now.isoformat(), 'identity_status': 'PASS', 'basis': 'unadjusted',
                        'volume_unit': 'shares' if adapter == 'index_tx' else 'unknown',
                        'amount_unit': 'unknown', 'quantity_scope': 'unknown', 'scope_evidence': None, 'bars': bars})
    return packets, raw['calendar'], now


def test_tencent_amount_is_lots_not_currency():
    row = {'date': '2026-10-09', 'open': 3000, 'high': 3100, 'low': 2900, 'close': 3050, 'amount': 12}
    bar = normalize_index('index_tx', [row], '2026-03-06', '2026-10-09')[0]
    assert bar['volume_observed'] == 12 and bar['volume_shares'] == 1200
    assert bar['amount_cny'] is None and row['amount'] == 12


def test_sina_unknown_unit_does_not_create_shares_or_currency():
    row = {'date': '2026-10-09', 'volume': 1200}
    bar = normalize_index('index_sina', [row], '2026-03-06', '2026-10-09')[0]
    assert bar['volume_observed'] == 1200 and bar['volume_shares'] is None and bar['amount_cny'] is None


def test_missing_currency_stays_null_and_blocks_market_quantity(pair):
    packets, calendar, now = pair
    q = quantity_check(packets[1])
    assert q['volume_status'] == 'PASS' and q['amount_status'] == 'UNVERIFIED'
    assert q['status'] == 'UNVERIFIED' and not q['market_quantity_usable']
    result = evaluate_pair(*packets, calendar, now)
    assert result['numerical_price_status'] == 'PASS'
    assert result['object_quality_status'] == 'UNVERIFIED'
    assert not result['buy_permission'] and not result['research_permission']


@pytest.mark.parametrize('fault', ['stock_identity', 'exchange', 'same_provider', 'forged_provider', 'wrong_date',
    'conflict', 'empty', 'stale', 'future', 'duplicate', 'gap', 'clock', 'qfq', 'cached', 'mixed_series'])
def test_index_faults_cannot_pass_price_acceptance(pair, fault):
    packets, calendar, now = pair
    p = packets[1]; bars = p['bars']
    if fault == 'stock_identity': p['symbol'] = 'sz000001'; p['kind'] = 'stock'
    elif fault == 'exchange': p['exchange'] = 'sz'
    elif fault == 'same_provider': p.update(adapter='index_sina', provider='sina')
    elif fault == 'forged_provider': p['provider'] = 'sina'
    elif fault == 'wrong_date': bars.pop()
    elif fault == 'conflict': bars[-1]['close'] *= 1.003; bars[-1]['high'] = max(bars[-1]['high'], bars[-1]['close'])
    elif fault == 'empty': bars.clear()
    elif fault == 'stale': bars.pop()
    elif fault == 'future': bars[-1]['date'] = '2099-01-01'
    elif fault == 'duplicate': bars[-1]['date'] = bars[-2]['date']
    elif fault == 'gap': bars.pop(-3)
    elif fault == 'clock': p['collected_at'] = (now - timedelta(hours=37)).isoformat()
    elif fault == 'qfq': p['basis'] = 'qfq'
    elif fault == 'cached': p['cached'] = True
    elif fault == 'mixed_series': bars[-1]['adapter'] = 'index_sina'
    r = evaluate_pair(*packets, calendar, now)
    assert r['numerical_price_status'] != 'PASS'
    assert not r['buy_permission'] and not r['research_permission']


@pytest.mark.parametrize('fault', ['volume_unit', 'volume_scale', 'currency_unit', 'currency_zero'])
def test_wrong_quantity_contracts_fail(pair, fault):
    p = pair[0][1]
    if fault == 'volume_unit': p['volume_unit'] = 'CNY'
    elif fault == 'volume_scale': p['bars'][-1]['volume_shares'] /= 100
    elif fault == 'currency_unit': p['amount_unit'] = 'shares'
    elif fault == 'currency_zero': p['bars'][-1]['amount_cny'] = 0
    assert quantity_check(p)['status'] == 'FAIL'


def test_different_and_unknown_scopes_are_never_compared(pair):
    packets, calendar, now = pair
    packets[0]['quantity_scope'] = 'exchange_wide'
    packets[1]['quantity_scope'] = 'index_constituents'
    r = evaluate_pair(*packets, calendar, now)
    assert r['quantity_pair_status'] == 'FAIL' and r['volume_comparison'] is None
    assert not r['buy_permission']
    packets[0]['quantity_scope'] = packets[1]['quantity_scope'] = 'unknown'
    r = evaluate_pair(*packets, calendar, now)
    assert r['quantity_pair_status'] == 'UNVERIFIED' and r['volume_comparison'] is None


def test_distinct_service_paths_do_not_prove_underlying_feed_independence(pair):
    r = evaluate_pair(*pair[0], pair[1], pair[2])
    assert r['service_path_independence'] == 'PASS'
    assert r['underlying_feed_independence'] == 'UNVERIFIED'
    assert r['object_quality_status'] != 'PASS'


def test_journal_distinguishes_sdk_calls_and_transport_attempts():
    events = [{'event': 'session_start'}, {'event': 'wire_start'}, {'event': 'wire_error'},
              {'event': 'wire_start'}, {'event': 'wire_end'}]
    r = journal_counts(events)
    assert r['sdk_http_calls'] == 1 and r['http_transport_attempts_started'] == 2
    assert r['transport_attempts_pending'] == 0


def test_timeout_keeps_partial_attempts_and_phase(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        spec = json.loads(kwargs['input'])
        from pathlib import Path
        Path(spec['trace']).write_text(json.dumps({'event': 'phase', 'phase': 'request_or_decode'})+'\n'+
                                       json.dumps({'event': 'session_start', 'host': '80.push2.eastmoney.com'})+'\n'+
                                       json.dumps({'event': 'wire_start'})+'\n')
        raise subprocess.TimeoutExpired('worker', 22)
    monkeypatch.setattr(subprocess, 'run', fail)
    # Runtime trace output is restricted to private even for offline injected faults.
    from poc.index_sources import ROOT
    p = ROOT/'private'/'beta03'/'offline-timeout.jsonl'
    r = probe('index_em', '399006', '2026-03-06', '2026-10-09', p)
    assert r['status'] == 'FAIL' and r['failure_stage'] == 'process_deadline'
    assert r['journal_counts']['http_transport_attempts_started'] == 1
    assert r['journal_counts']['transport_attempts_pending'] == 1
    assert r['application_attempts'] == 1 and r['explicit_retries'] == 0


@pytest.mark.parametrize('symbol', ['sz000001', 'sh399001', '600000', '000001', 'sh600000'])
def test_stock_or_ambiguous_symbol_rejected_before_network(symbol):
    from poc.index_sources import ROOT
    with pytest.raises(ValueError): probe('index_tx', symbol, '', '', ROOT/'private/beta03/rejected.jsonl')


def test_wrong_worker_nonce_cannot_reuse_cached_evidence(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: type('Result', (), {
        'returncode': 0, 'stdout': json.dumps({'status': 'PASS', 'run_nonce': 'old-cache'})})())
    from poc.index_sources import ROOT
    r = probe('index_tx', 'sh000001', '2026-03-06', '2026-10-09', ROOT/'private/beta03/nonce-test.jsonl')
    assert r['status'] == 'FAIL' and r['failure_stage'] == 'worker_protocol'
