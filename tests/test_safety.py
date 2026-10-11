import copy
import math
from datetime import datetime, timedelta
import pytest
from ashare.config import WATCHLIST
from ashare.demo import demo_raw
from ashare.engine import TZ, analyze, expected_date, eligible_code, fee, indicators, size_plan, validate_bars
from ashare.provider import normalize, request_api

@pytest.fixture
def raw():
    data = demo_raw()
    data['mode'] = 'live'
    return data

@pytest.mark.parametrize('code', ['600000','601001','603001','000983','001001','002312'])
def test_allowed_codes(code):
    assert eligible_code(code)

@pytest.mark.parametrize('code', ['300001','688001','830001','605001','60000','600001x','sh600001','０００００１'])
def test_disallowed_codes(code):
    assert not eligible_code(code)

@pytest.mark.parametrize('entry,stop', [(4,3.9),(8,7.8),(24,23.7),(26,25.5),(60,59),(8,7),(2,1.99)])
def test_whole_lots_budget_and_all_costs(entry, stop):
    plan=size_plan(entry,stop)
    assert plan['shares'] % 100 == 0
    assert plan['cost'] <= 2500
    assert plan['planned_loss'] <= 50
    if plan['shares']:
        qty=plan['shares']
        cost=qty*plan['estimated_buy']+fee(qty*plan['estimated_buy'])
        proceeds=qty*plan['estimated_stop_fill']-fee(qty*plan['estimated_stop_fill'],True)
        assert math.isclose(plan['cost'],round(cost,2))
        assert math.isclose(plan['planned_loss'],round(cost-proceeds,2))
        assert cost<=2500 and cost-proceeds<=50

@pytest.mark.parametrize('entry,stop,cash,positions',[(10,10,5000,0),(10,11,5000,0),(10,9,50,0),(10,9,5000,2),(float('nan'),9,5000,0),(10,0,5000,0)])
def test_infeasible_plan_is_zero(entry,stop,cash,positions):
    assert size_plan(entry,stop,cash,positions)['shares']==0

@pytest.mark.parametrize('change', ['empty','old','future','nan','duplicate','ohlc','zero_volume','missing_day','jump'])
def test_bad_quotes_block(raw,change):
    bars=raw['stocks'][WATCHLIST[0]]
    if change=='empty': bars.clear()
    elif change=='old': bars.pop()
    elif change=='future': bars[-1]['date']='2099-01-01'
    elif change=='nan': bars[-1]['close']=float('nan')
    elif change=='duplicate': bars[-1]['date']=bars[-2]['date']
    elif change=='ohlc': bars[-1]['high']=0.01
    elif change=='zero_volume': bars[-1]['volume']=0
    elif change=='missing_day': bars.pop(-4)
    elif change=='jump': bars[-1].update(open=100,close=100,high=101,low=99)
    row=analyze(raw)['stocks'][0]
    assert row['quality']
    assert row['indicators'] is None
    assert not row['buy_permission']

@pytest.mark.parametrize('calendar',[[],['2025-01-01'],['nonsense'],['2027-01-01','2026-01-01']])
def test_unknown_calendar_blocks(raw,calendar):
    raw['calendar']=calendar
    report=analyze(raw)
    assert report['expected_quote_date'] is None
    assert not report['market']['quality_pass']
    assert all(not s['buy_permission'] and s['quality'] for s in report['stocks'])

def test_holidays_and_close_cutoff():
    calendar=['2026-09-30','2026-10-08','2026-10-09','2026-10-12']
    assert expected_date(calendar,datetime(2026,10,8,15,59,tzinfo=TZ))=='2026-09-30'
    assert expected_date(calendar,datetime(2026,10,8,16,0,tzinfo=TZ))=='2026-10-08'
    assert expected_date(calendar,datetime(2026,10,11,19,tzinfo=TZ))=='2026-10-09'

@pytest.mark.parametrize('stamp',[None,'invalid','2020-01-01T00:00:00+08:00','2099-01-01T00:00:00+08:00','2026-10-11T00:00:00'])
def test_bad_collection_clock_blocks(raw,stamp):
    raw['collected_at']=stamp
    report=analyze(raw)
    assert report['stale_collection']
    assert not report['market']['quality_pass']
    assert all(s['quality'] and not s['buy_permission'] for s in report['stocks'])

@pytest.mark.parametrize('name',[None,'*ST样本','退市样本','ST样本'])
def test_metadata_and_risk_warning_blocks(raw,name):
    raw['metadata'][WATCHLIST[0]]={'name':name}
    row=analyze(raw)['stocks'][0]
    assert row['quality'] and not row['buy_permission']

def test_missing_index_blocks_market(raw):
    raw['indices'].pop('399006')
    report=analyze(raw)
    assert report['market']['cycle']=='UNKNOWN'
    assert all('指数数据质量未通过' in s['blockers'] for s in report['stocks'])

def test_no_manufactured_full_score_or_permission(raw):
    report=analyze(raw)
    assert all(s['score'] is None and s['scores']['industry'] is None and s['scores']['funds'] is None and s['buy_permission'] is False for s in report['stocks'])
    demo=analyze(demo_raw())
    assert all('合成演示数据禁止交易' in s['blockers'] for s in demo['stocks'])

def test_constant_series_indicators(raw):
    bars=copy.deepcopy(raw['stocks'][WATCHLIST[0]])
    for b in bars: b.update(open=10,close=10,high=11,low=9,volume=100)
    ind=indicators(bars)
    assert all(ind[f'ma{n}']==10 for n in (5,10,20,30))
    assert ind['dif']==ind['dea']==ind['macd']==0
    assert ind['volume_ratio']==1

def test_bad_normalization_preserves_blocking_sentinel():
    assert normalize([{'日期':'2026-10-09','收盘':'oops'}])[0]['open'] is None

def test_request_timeout_is_reported(monkeypatch):
    import subprocess
    def timeout(*args,**kwargs): raise subprocess.TimeoutExpired('worker',1)
    monkeypatch.setattr(subprocess,'run',timeout)
    records,report=request_api('stock_zh_a_hist',{},1)
    assert records==[] and report['status']=='timeout'

def test_public_json_has_no_actual_account(raw):
    report=analyze(raw)
    assert not {'positions','cash','trades','account_id','holdings','personal_info'}.intersection(report)
    assert all(not s['buy_permission'] for s in report['stocks'])

def test_expiry_at_next_session_close(raw):
    now=datetime.now(TZ)
    report=analyze(raw,now)
    next_day=next(d for d in raw['calendar'] if d>report['expected_quote_date'])
    close=datetime.fromisoformat(next_day+'T16:00:00+08:00')
    assert datetime.fromisoformat(report['valid_until'])<=close
    assert datetime.fromisoformat(report['valid_until'])<=now+timedelta(hours=36)

def test_collect_retries_once_and_keeps_history(monkeypatch):
    from ashare import provider
    calls={}
    def fake(function,kwargs,timeout):
        key=(function,str(kwargs))
        calls[key]=calls.get(key,0)+1
        return [],{'interface':function,'status':'timeout','duration_seconds':timeout}
    monkeypatch.setattr(provider,'request_api',fake)
    raw=provider.collect(timeout=1)
    assert len(raw['interface_report'])==45
    assert all(count==2 for count in calls.values())
    assert all(r['attempt_count']==2 and len(r['attempt_history'])==2 for r in raw['interface_report'])
    assert analyze(raw)['market']['cycle']=='UNKNOWN'

def test_breakout_pullback_uses_only_prior_resistance(raw):
    from ashare.engine import buy_point
    bars=copy.deepcopy(raw['stocks'][WATCHLIST[0]])
    for b in bars: b.update(open=10,close=10,high=10.1,low=9.9,volume=100)
    bars[-3].update(close=10.25,high=10.3)
    bars[-1].update(close=10.2,low=10.12,high=10.3)
    ind={'ma5':10.1,'ma10':9.9,'ma20':9.8,'ma30':9.7,'close':10.2,'volume_ratio':2}
    assert buy_point(bars,ind)=='突破回踩（候选）'
    bars[-1]['high']=100
    assert buy_point(bars,ind)=='突破回踩（候选）'
    for b in bars[-23:-3]: b['high']=11
    assert buy_point(bars,ind)=='无确认买点'

def test_trend_pullback_requires_volume_and_alignment(raw):
    from ashare.engine import buy_point
    bars=copy.deepcopy(raw['stocks'][WATCHLIST[0]])
    for b in bars: b.update(open=10,close=10,high=11,low=9.9,volume=100)
    ind={'ma5':10.1,'ma10':10,'ma20':9.9,'ma30':9.8,'close':10,'volume_ratio':0.8}
    assert buy_point(bars,ind)=='趋势回踩（候选）'
    ind['volume_ratio']=1.1
    assert buy_point(bars,ind)=='无确认买点'

def test_no_active_publish_or_schedule_workflow():
    from pathlib import Path
    import yaml
    root=Path(__file__).resolve().parents[1]
    for path in (root/'.github/workflows').glob('*.yml'):
        data=yaml.load(path.read_text(),Loader=yaml.BaseLoader)
        assert 'schedule' not in data['on']
        assert 'pages' not in data.get('permissions',{})
        assert 'deploy-pages' not in path.read_text()
        assert 'private/' not in path.read_text()
    assert (root/'docs/pages-workflow.yml.disabled').exists()
