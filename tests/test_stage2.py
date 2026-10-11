import copy
import json
from datetime import datetime,timedelta
from pathlib import Path
import pytest
from ashare.engine import analyze,flow_stats,TZ
from ashare.fixtures import synthetic_dataset
from ashare.paper import PaperBook,charge_cents,model_for_day
from ashare.provider import cross_check,adjustment_check,normalize
from ashare.replay import advance,run_backtest,snapshot_day,sensitivity

@pytest.fixture
def dataset():return synthetic_dataset(20)

@pytest.fixture
def live_raw(dataset):
    # Offline positive-path test data; never published as real market quotes.
    raw=copy.deepcopy(dataset['snapshots'][-1]);raw['mode']='live'
    for c in raw['cross_checks'].values():c.pop('fixture_only',None)
    for a in raw['adjustments'].values():a.pop('fixture_only',None)
    return raw


def at(raw):return datetime.fromisoformat(raw['collected_at'])


def test_full_five_dimension_score_but_no_public_account_permission(live_raw):
    snapshot=analyze(live_raw,at(live_raw))
    assert snapshot['market']['cycle']=='A'
    good=[s for s in snapshot['stocks'] if s['research_permission']]
    assert good and all(80<=s['score']<=100 and not s['blockers'] for s in good)
    assert all(s['buy_permission'] is False for s in snapshot['stocks'])
    assert all(s['score']==sum(s['scores'].values()) for s in snapshot['stocks'])

@pytest.mark.parametrize('failure',['index_conflict','stock_conflict','metadata_future','industry_future','flow_old','flow_nan','adjustment','mode_demo','fixture_promotion','index_fixture_promotion','adjustment_fixture_promotion'])
def test_every_permission_gate_fails_closed(live_raw,failure):
    code=live_raw['watchlist'][0]
    if failure=='index_conflict':live_raw['cross_checks']['index:000001']['status']='conflict'
    elif failure=='stock_conflict':live_raw['cross_checks']['stock:'+code]['status']='conflict'
    elif failure=='metadata_future':live_raw['metadata'][code]['available_at']='2099-01-01T16:00:00+08:00'
    elif failure=='industry_future':live_raw['sectors']['合成示例行业']['flows'][-1]['date']='2099-01-01'
    elif failure=='flow_old':live_raw['stock_flows'][code].pop()
    elif failure=='flow_nan':live_raw['stock_flows'][code][-1]['net']=float('nan')
    elif failure=='adjustment':live_raw['adjustments'][code]['status']='corporate_action'
    elif failure=='mode_demo':live_raw['mode']='demo'
    elif failure=='fixture_promotion':live_raw['cross_checks']['stock:'+code]['fixture_only']=True
    elif failure=='index_fixture_promotion':live_raw['cross_checks']['index:000001']['fixture_only']=True
    elif failure=='adjustment_fixture_promotion':live_raw['adjustments'][code]['fixture_only']=True
    row=analyze(live_raw,at(live_raw))['stocks'][0]
    assert row['research_permission'] is False and row['blockers']


def test_cross_source_conflict_and_no_silent_splicing(live_raw):
    bars=live_raw['stocks'][live_raw['watchlist'][0]];day=bars[-1]['date'];now=at(live_raw)
    other=copy.deepcopy(bars)
    assert cross_check(bars,other,day,live_raw['calendar'],now)['status']=='passed'
    other[-1]['close']*=1.003;other[-1]['high']=max(other[-1]['high'],other[-1]['close'])
    assert cross_check(bars,other,day,live_raw['calendar'],now)['status']=='conflict'
    assert cross_check(bars,[],day,live_raw['calendar'],now)['status']=='unverified'


def test_share_volume_normalization():
    record={'日期':'2026-10-09','开盘':10,'收盘':10,'最高':11,'最低':9,'成交量':12}
    assert normalize([record],volume_multiplier=100)[0]['volume']==1200
    tx={'date':'2026-10-09','open':10,'close':10,'high':11,'low':9,'amount':12}
    assert normalize([tx],index_tx=True,volume_multiplier=100)[0]['volume']==1200


def test_corporate_action_factor_change_is_blocked(live_raw):
    bars=live_raw['stocks'][live_raw['watchlist'][0]];adjusted=copy.deepcopy(bars)
    assert adjustment_check(bars,adjusted,bars[-1]['date'])['status']=='stable'
    adjusted[-1]['close']*=.97
    assert adjustment_check(bars,adjusted,bars[-1]['date'])['status']=='corporate_action'
    assert adjustment_check(bars,[],bars[-1]['date'])['status']=='unverified'


def filled_book(dataset):
    book=PaperBook()
    for a,b in zip(dataset['snapshots'],dataset['snapshots'][1:]):
        advance(book,a,b,allow_synthetic=True)
        if book.state['positions']:return book,b
    raise AssertionError('fixture should exercise buys')


def test_next_day_execution_whole_lots_cash_risk(dataset):
    report=run_backtest(dataset,allow_synthetic=True)
    assert report['trades']
    for trade in report['trades']:
        assert trade['shares']%100==0 and trade['fee_cents']>0
        if trade['side']=='BUY':
            assert trade['date']>trade['signal_date']
            assert trade['planned_risk_cents']<=5000
            assert trade['notional_cents']+trade['fee_cents']<=250000
        else:assert trade['date']>trade['buy_date']
    assert report['ending_book']['cash_cents']>=0
    assert len(report['ending_book']['positions'])<=2
    assert report['metrics']['total_fees']>0
    PaperBook(report['ending_book']).validate()


def test_t1_rejects_same_day_sell(dataset):
    book,raw=filled_book(dataset);code=next(iter(book.state['positions']));day=book.state['positions'][code]['buy_date']
    bars=raw['stocks'][code]
    result=book.sell(code,bars[-1],day,raw['calendar'],bars[-2]['close'],execution_verified=True)
    assert not result['filled'] and 'T+1' in result['reason']


def test_duplicate_session_is_idempotent(dataset):
    book=PaperBook();a,b=dataset['snapshots'][:2]
    advance(book,a,b,allow_synthetic=True);before=copy.deepcopy(book.state)
    events=advance(book,a,b,allow_synthetic=True)
    assert book.state==before and '已处理' in events[0]['reason']


def test_third_stock_cannot_use_reserved_cash(dataset):
    report=run_backtest(dataset,allow_synthetic=True)
    assert any('2只' in e.get('reason','') for e in report['events'])
    cash=500000;open_codes=set()
    for t in report['trades']:
        if t['side']=='BUY':
            cash-=t['notional_cents']+t['fee_cents'];open_codes.add(t['code'])
        else:cash+=t['notional_cents']-t['fee_cents'];open_codes.remove(t['code'])
        assert cash>=0 and len(open_codes)<=2


def test_limit_down_does_not_create_fake_stop_fill(dataset):
    book,raw=filled_book(dataset);code=next(iter(book.state['positions']));position=book.state['positions'][code]
    i=raw['calendar'].index(position['buy_date']);day=raw['calendar'][i+1]
    previous=raw['stocks'][code][-1]['close'];price=round(previous*.9,2)
    bar={'date':day,'open':price,'close':price,'high':price,'low':price,'volume':1000}
    before=copy.deepcopy(book.state)
    result=book.sell(code,bar,day,raw['calendar'],previous,execution_verified=True)
    assert not result['filled'] and '跌停' in result['reason'] and book.state==before


def test_gap_stop_can_exceed_planned_50_yuan(dataset):
    book,raw=filled_book(dataset);code=next(iter(book.state['positions']));p=book.state['positions'][code]
    day=raw['calendar'][raw['calendar'].index(p['buy_date'])+1]
    previous=raw['stocks'][code][-1]['close'];opening=round(previous*.93,2)
    bar={'date':day,'open':opening,'close':opening,'high':round(previous*.98,2),'low':round(previous*.91,2),'volume':1000}
    result=book.sell(code,bar,day,raw['calendar'],previous,execution_verified=True,reference=opening,reason='stop_or_gap')
    assert result['filled'] and result['trade']['pnl_cents'] < -5000


def test_unverified_execution_is_rejected(dataset):
    book,raw=filled_book(dataset);code=next(iter(book.state['positions']));day=raw['calendar'][raw['calendar'].index(raw['stocks'][code][-1]['date'])+1]
    bar=copy.deepcopy(raw['stocks'][code][-1]);bar['date']=day
    result=book.sell(code,bar,day,raw['calendar'],bar['close'])
    assert not result['filled'] and '核验' in result['reason']

@pytest.mark.parametrize('future',['stock','sector','fund','metadata'])
def test_future_information_is_rejected(dataset,future):
    raw=dataset['snapshots'][0];code=raw['watchlist'][0]
    if future=='stock':raw['stocks'][code][-1]['date']='2099-01-01'
    elif future=='sector':raw['sectors']['合成示例行业']['bars'][-1]['date']='2099-01-01'
    elif future=='fund':raw['stock_flows'][code][-1]['date']='2099-01-01'
    else:raw['metadata'][code]['available_at']='2099-01-01T16:00:00+08:00'
    with pytest.raises(ValueError):run_backtest(dataset,allow_synthetic=True)


def test_late_snapshot_cannot_trade_at_past_open(dataset):
    a,b=dataset['snapshots'][:2]
    a['collected_at']=b['stocks'][b['watchlist'][0]][-1]['date']+'T10:00:00+08:00'
    with pytest.raises(ValueError,match='开盘前不可知'):advance(PaperBook(),a,b,allow_synthetic=True)


def test_missing_session_is_not_hidden(dataset):
    dataset['snapshots'].pop(2)
    with pytest.raises(ValueError,match='不连续'):run_backtest(dataset,allow_synthetic=True)


def test_synthetic_replay_requires_explicit_opt_in(dataset):
    with pytest.raises(ValueError,match='allow-synthetic'):run_backtest(dataset)
    report=run_backtest(dataset,allow_synthetic=True)
    assert report['mode']=='demo' and '不能作为' in report['warnings'][0]


def test_fees_change_on_tax_effective_date():
    assert model_for_day('2023-08-25').stamp_tax_sell==.001
    assert model_for_day('2023-08-28').stamp_tax_sell==.0005
    assert charge_cents(100000,'2023-08-25',True)>charge_cents(100000,'2023-08-28',True)
    with pytest.raises(ValueError):model_for_day('2020-01-01')


def test_corrupt_book_never_resets_to_5000(dataset):
    book,_=filled_book(dataset);state=copy.deepcopy(book.state);state['cash_cents']+=1
    with pytest.raises(ValueError,match='守恒'):PaperBook(state)
    state=copy.deepcopy(book.state);state['trades'][0]['shares']+=100
    with pytest.raises(ValueError):PaperBook(state)


def test_slippage_sensitivity_is_explicit(dataset):
    variants=sensitivity(dataset,True)
    assert [r['slippage_bps'] for r in variants]==[10,20,30]
    assert all(r['total_fees']>=0 for r in variants)


def test_private_storage_rejects_site_path(tmp_path,monkeypatch):
    from ashare import storage
    monkeypatch.setattr(storage,'PRIVATE',tmp_path/'private')
    with pytest.raises(ValueError,match='private'):storage.atomic_json(tmp_path/'site/data/ledger.json',{})
    path=tmp_path/'private/book.json'
    storage.transact(path,lambda book:None)
    assert json.loads(path.read_text())['kind']=='simulated_only'
    path.write_text('broken')
    with pytest.raises(json.JSONDecodeError):storage.transact(path,lambda book:None)
    assert path.read_text()=='broken'


def test_later_snapshots_cannot_change_earlier_fills(dataset):
    prefix=copy.deepcopy(dataset);prefix['snapshots']=prefix['snapshots'][:8]
    short=run_backtest(prefix,allow_synthetic=True)
    full=run_backtest(dataset,allow_synthetic=True)
    assert short['trades']==[t for t in full['trades'] if t['date']<=short['end_date']]
    assert short['equity_curve']==full['equity_curve'][:8]


def test_missing_held_quote_suppresses_performance(dataset):
    book,raw=filled_book(dataset)
    index=next(i for i,r in enumerate(dataset['snapshots']) if r['collected_at']==raw['collected_at'])
    code=next(iter(book.state['positions']))
    del dataset['snapshots'][index+1]['stocks'][code]
    report=run_backtest(dataset,allow_synthetic=True)
    assert any(code in p['stale_marks'] for p in report['equity_curve'])
    assert report['metrics']['net_return_pct'] is None
    assert report['metrics']['max_drawdown_pct'] is None


def test_open_exit_does_not_use_later_intraday_stop(dataset):
    book,signal=filled_book(dataset)
    index=next(i for i,r in enumerate(dataset['snapshots']) if r['collected_at']==signal['collected_at'])
    execution=copy.deepcopy(dataset['snapshots'][index+1]);signal=copy.deepcopy(signal)
    for bars in signal['indices'].values():
        for i,b in enumerate(bars):
            price=round(3000*(1-.002*i),2)
            b.update(open=price,close=price,high=price*1.01,low=price*.99)
    assert analyze(signal,at(signal))['market']['cycle']=='C'
    code=next(iter(book.state['positions']));p=book.state['positions'][code]
    bar=execution['stocks'][code][-1];opening=signal['stocks'][code][-1]['close']
    bar.update(open=opening,close=opening,high=opening*1.015,low=p['stop']*.98)
    events=advance(book,signal,execution,allow_synthetic=True)
    sell=next(e['trade'] for e in events if e.get('trade',{}).get('code')==code and e['trade']['side']=='SELL')
    assert sell['reason']=='confirmed_market_C'
    import math
    assert sell['price']==math.floor(opening*.999*100+1e-8)/100


def test_concurrent_ledger_updates_are_not_lost(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from ashare import storage
    monkeypatch.setattr(storage,'PRIVATE',tmp_path/'private')
    path=tmp_path/'private/book.json'
    def update(i):storage.transact(path,lambda book:book.state['warnings'].append(str(i)))
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(update,range(12)))
    state=json.loads(path.read_text())
    assert sorted(map(int,state['warnings']))==list(range(12))
    PaperBook(state).validate()


def test_public_artifact_contains_no_private_book():
    root=Path(__file__).resolve().parents[1]
    for path in (root/'site/data').glob('*.json'):
        data=json.loads(path.read_text())
        assert not {'cash_cents','positions','ending_book','used_signals'}&set(data)
        if data.get('schema_version')=='backtest-2':
            assert data['mode']=='demo' and data['kind']=='simulated_only'
            assert all(t.get('synthetic') is True for t in data['trades'] if t['side']=='BUY')


def test_paper_book_cannot_mix_synthetic_and_live(dataset):
    book=PaperBook();a,b,c=copy.deepcopy(dataset['snapshots'][:3])
    advance(book,a,b,allow_synthetic=True)
    b['mode']=c['mode']='live'
    before=copy.deepcopy(book.state)
    with pytest.raises(ValueError,match='同一模拟账本'):advance(book,b,c)
    assert book.state==before


@pytest.mark.parametrize('failure',['broken_bar','stale_metadata','live_fixture'])
def test_execution_quality_is_checked_independently(dataset,failure):
    from ashare.replay import execution_verified
    raw=dataset['snapshots'][-1];code=raw['watchlist'][0];day=raw['stocks'][code][-1]['date']
    assert execution_verified(raw,code,day)
    if failure=='broken_bar':raw['stocks'][code][-1]['close']=float('nan')
    elif failure=='stale_metadata':raw['metadata'][code]['available_at']=(at(raw)-timedelta(days=3)).isoformat()
    else:raw['mode']='live'
    assert not execution_verified(raw,code,day)
