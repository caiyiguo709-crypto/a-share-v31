"""Point-in-time snapshot replay; next-session execution and conservative missing-data handling."""
import copy
from datetime import datetime
from .engine import analyze, expected_date, TZ, valid_clock, validate_bars
from .paper import PaperBook


def snapshot_day(raw):
    stamp=datetime.fromisoformat(raw['collected_at'])
    if stamp.tzinfo is None:raise ValueError('历史快照时间必须含时区')
    day=expected_date(raw.get('calendar',[]),stamp.astimezone(TZ))
    if not day:raise ValueError('历史快照交易日历无效')
    for group in ('stocks','indices'):
        for bars in raw.get(group,{}).values():
            if any(b.get('date','9999')>day for b in bars):raise ValueError('历史快照含未来行情，拒绝回测')
    for code,meta in raw.get('metadata',{}).items():
        if meta.get('available_at'):
            available=datetime.fromisoformat(meta['available_at'])
            if available>stamp:raise ValueError('历史快照含尚未可知的证券元数据')
        if meta.get('effective_date') and meta['effective_date']>day:raise ValueError('未来行业归属禁止用于历史评分')
    for sector in raw.get('sectors',{}).values():
        if any(r.get('date','9999')>day for r in sector.get('bars',[])+sector.get('flows',[])):
            raise ValueError('历史快照含未来行业或资金流')
    if any(r.get('date','9999')>day for rows in raw.get('stock_flows',{}).values() for r in rows):
        raise ValueError('历史快照含未来个股资金流')
    return day,stamp


def execution_verified(raw,code,day):
    from .engine import eligible_code
    meta=raw.get('metadata',{}).get(code,{})
    adjustment=raw.get('adjustments',{}).get(code,{})
    bars=raw.get('stocks',{}).get(code,[])
    cross=raw.get('cross_checks',{}).get('stock:'+code,{})
    stamp=datetime.fromisoformat(raw['collected_at'])
    # This is ex-post validation of execution bars, never a same-day scoring signal.
    return bool(eligible_code(code) and meta.get('verified') and isinstance(meta.get('name'),str)
                and not any(x in meta['name'].upper() for x in ('ST','退'))
                and meta.get('effective_date')==day and adjustment.get('status')=='stable'
                and valid_clock(meta.get('available_at'),stamp)
                and adjustment.get('checked_date')==day
                and cross.get('status')=='passed'
                and not (raw.get('mode')=='live' and (cross.get('fixture_only') or adjustment.get('fixture_only')))
                and not validate_bars(bars,day,raw.get('calendar',[]),stamp))


def advance(book,signal_raw,execution_raw,*,allow_synthetic=False,slippage=.001):
    """One chronological session; uses ONLY previous snapshot to form orders."""
    signal_day,signal_stamp=snapshot_day(signal_raw)
    day,_=snapshot_day(execution_raw)
    calendar=execution_raw['calendar']
    if signal_day not in calendar or calendar.index(signal_day)+1>=len(calendar) or calendar[calendar.index(signal_day)+1]!=day:
        raise ValueError('回放快照不连续；必须提供信号后的下一交易日')
    if signal_stamp>=datetime.fromisoformat(day+'T09:30:00+08:00'):
        raise ValueError('信号快照在执行开盘前不可知，拒绝回测')
    if signal_raw['mode']!=execution_raw['mode']:raise ValueError('禁止混合合成与真实快照')
    if execution_raw['mode']=='demo' and not allow_synthetic:raise ValueError('合成模拟必须显式授权')
    if book.state.get('data_mode') not in (None,execution_raw['mode']):
        raise ValueError('禁止在同一模拟账本混合真实与合成行情')
    book._day(day,calendar)
    if day in book.state.get('processed_sessions',[]):return [{'filled':False,'reason':'该模拟交易日已处理，拒绝重复推进'}]
    snapshot=analyze(signal_raw,signal_stamp)
    book.state['data_mode']=execution_raw['mode']
    results=[]
    execution=execution_raw.get('stocks',{})
    previous=signal_raw.get('stocks',{})
    for code,p in list(book.state['positions'].items()):
        bars=execution.get(code,[]);prev=previous.get(code,[])
        if not bars or not prev:
            results.append({'code':code,'filled':False,'reason':'持仓执行行情缺失，保留持仓并标记估值过期'});continue
        bar=bars[-1];reference=None;reason=None
        if not execution_verified(execution_raw,code,day):
            results.append({'code':code,'filled':False,'reason':'持仓执行质量/公司行为未通过'});continue
        # Exits known before the open must precede an intraday stop condition.
        if snapshot['market']['cycle']=='C':reason='confirmed_market_C'
        elif calendar.index(day)-calendar.index(p['buy_date'])>=10:reason='max_10_sessions'
        elif bar['low']<=p['stop']:
            reference=min(bar['open'],p['stop']);reason='stop_or_gap'
        if reason:
            result=book.sell(code,bar,day,calendar,prev[-1]['close'],reason=reason,reference=reference,execution_verified=True,slippage=slippage)
            results.append({'code':code,**result})
    def candidate(row):
        if row['research_permission']:return True
        return allow_synthetic and snapshot['mode']=='demo' and set(row['blockers'])<={'合成演示数据禁止交易'} and (row['score'] or 0)>=80
    candidates=sorted((r for r in snapshot['stocks'] if candidate(r)),key=lambda r:(-(r['score'] or 0),r['code']))
    for row in candidates:
        code=row['code'];bars=execution.get(code,[]);prev=previous.get(code,[])
        if not bars or not prev:
            results.append({'code':code,'filled':False,'reason':'买入执行行情缺失'});continue
        result=book.buy(snapshot,code,bars[-1],day,calendar,prev[-1]['close'],execution_verified=execution_verified(execution_raw,code,day),allow_synthetic=allow_synthetic,slippage=slippage)
        results.append({'code':code,**result})
    # No same-day stop sale after buys: explicitly retain T+1 exposure.
    for code,p in book.state['positions'].items():
        bars=execution.get(code,[])
        if p['buy_date']==day and bars and bars[-1]['low']<=p['stop']:
            warning=f'{day}:{code}:买入当日触及止损但受T+1约束，不能卖出'
            if warning not in book.state['warnings']:book.state['warnings'].append(warning)
    book.state.setdefault('processed_sessions',[]).append(day)
    book.state['last_event_date']=day
    book.validate()
    return results


def run_backtest(dataset,*,allow_synthetic=False,slippage=.001):
    if dataset.get('schema_version')!='replay-2':raise ValueError('回测输入应为replay-2逐日快照格式')
    if dataset.get('mode')=='demo' and not allow_synthetic:raise ValueError('演示回测需要--allow-synthetic')
    if dataset.get('mode') not in ('demo','live'):raise ValueError('回测模式无效')
    if dataset.get('universe_policy') not in ('point_in_time_archive','fixed_watchlist_demo'):
        raise ValueError('必须明确股票池来源，禁止用当前股票池冒充历史全市场')
    if dataset.get('corporate_actions'):raise ValueError('输入含公司行为；当前引擎保守拒绝未经记账的复权/分红事件')
    raws=dataset.get('snapshots',[])
    if len(raws)<2:raise ValueError('回测至少需要两个不同交易日快照')
    days=[]
    for raw in raws:
        if raw.get('mode')!=dataset['mode']:raise ValueError('禁止混合回测模式')
        day,_=snapshot_day(raw);days.append(day)
    if days!=sorted(set(days)):raise ValueError('快照必须按日期升序且每交易日唯一')
    book=PaperBook();curve=[{'date':days[0],'equity_cents':500000,'stale_marks':[]}];events=[];complete=True
    for signal,execution in zip(raws,raws[1:]):
        day,_=snapshot_day(execution)
        results=advance(book,signal,execution,allow_synthetic=allow_synthetic,slippage=slippage)
        events += [{'date':day,**r} for r in results]
        prices={c:bars[-1]['close'] for c,bars in execution.get('stocks',{}).items() if execution_verified(execution,c,day)}
        mark=book.equity(prices)
        if mark['stale_marks'] or any('持仓执行质量' in r.get('reason','') for r in results):complete=False
        curve.append({'date':day,**mark})
    peak=500000;drawdown=0
    for r in curve:
        peak=max(peak,r['equity_cents']);drawdown=max(drawdown,1-r['equity_cents']/peak)
    sells=[t for t in book.state['trades'] if t['side']=='SELL']
    ending=curve[-1]['equity_cents']
    metrics={'initial_capital':5000,'ending_equity':ending/100,'net_return_pct':round((ending/500000-1)*100,4) if complete else None,
             'max_drawdown_pct':round(drawdown*100,4) if complete else None,'closed_trades':len(sells),
             'win_rate_pct':round(sum(t['pnl_cents']>0 for t in sells)/len(sells)*100,2) if sells and complete else None,
             'total_fees':sum(t['fee_cents'] for t in book.state['trades'])/100,'open_positions':len(book.state['positions']),
             'data_complete_for_held_positions':complete}
    return {'schema_version':'backtest-2','kind':'simulated_only','mode':dataset['mode'],'source':dataset.get('source','用户提供逐日快照'),
            'universe_policy':dataset['universe_policy'],'start_date':days[0],'end_date':days[-1],'slippage_bps':round(slippage*10000),
            'metrics':metrics,'equity_curve':curve,'trades':book.state['trades'],'events':events,'ending_book':book.state,
            'warnings':['合成演示仅测试流程，不能作为投资收益证据'] if dataset['mode']=='demo' else ['仅对提供的时点快照样本有效；未实现全市场历史股票池','未处理公司行为时不输出完整可信收益','开放持仓按收盘估值，未强制平仓及扣除未来卖出成本']}


def sensitivity(dataset,allow_synthetic=False):
    return [run_backtest(copy.deepcopy(dataset),allow_synthetic=allow_synthetic,slippage=bps/10000)['metrics']|{'slippage_bps':bps}
            for bps in (10,20,30)]
