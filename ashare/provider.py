"""Bounded, independently checked public AKShare data; no stale/fake fallback."""
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from .config import WATCHLIST, INDICES
from .stage1 import TZ, iso_now, expected_date, validate_bars


def request_api(function, kwargs, timeout=18):
    started = time.monotonic()
    report = {"interface": function, "parameters": kwargs, "collected_at": iso_now()}
    try:
        proc = subprocess.run([sys.executable, "-m", "ashare.worker"], input=json.dumps({"function": function, "kwargs": kwargs}),
                              text=True, capture_output=True, timeout=timeout, check=False)
        result = json.loads(proc.stdout) if proc.returncode == 0 else {"error": "worker_failed"}
        records = result.get("records", [])
        report.update(status="ok" if records else "error", error=result.get("error"), rows=len(records),
                      fields=list(records[0]) if records else [], akshare_version=result.get("version"))
    except subprocess.TimeoutExpired:
        report.update(status="timeout", error="request_timeout")
        records = []
    except (OSError, ValueError):
        report.update(status="error", error="invalid_worker_response")
        records = []
    report["duration_seconds"] = round(time.monotonic() - started, 2)
    return records, report


def normalize(records, *, volume_multiplier=1, index_tx=False, start=None, end=None):
    aliases = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume"}
    out = []
    for record in records:
        row = {aliases.get(k, k): v for k, v in record.items()}
        day = str(row.get("date", "invalid"))[:10]
        if (start and day < start) or (end and day > end):
            continue
        if index_tx:
            row["volume"] = row.get("amount") # This API's amount is volume in lots, NOT turnover in yuan.
        try:
            bar = {"date": day, **{k: float(row[k]) for k in ("open", "high", "low", "close", "volume")}}
            bar["volume"] *= volume_multiplier
            out.append(bar)
        except (ValueError, TypeError, KeyError):
            out.append({"date": day, "open": None})
    return out


def cross_check(a, b, expected, calendar, now):
    errors_a, errors_b = validate_bars(a, expected, calendar, now), validate_bars(b, expected, calendar, now)
    result = {"status": "unverified", "sources_valid": int(not errors_a) + int(not errors_b), "max_price_deviation": None}
    if errors_a or errors_b:
        result["reason"] = "需两个独立来源同时通过完整性核验"
        return result
    left, right = {r['date']: r for r in a[-20:]}, {r['date']: r for r in b[-20:]}
    if set(left) != set(right):
        result.update(status="conflict", reason="交叉检查日期不一致")
        return result
    deviation = max(abs(left[d][k] / right[d][k] - 1) for d in left for k in ('open','high','low','close'))
    result["max_price_deviation"] = round(deviation, 6)
    result.update(status="passed" if deviation <= 0.001 else "conflict", reason="近20日OHLC偏差≤0.1%" if deviation <= 0.001 else "独立来源价格冲突")
    return result


def adjustment_check(raw, adjusted, expected):
    """Use only raw OHLC for signals/execution; reject corporate-action windows."""
    import math
    left, right = {b['date']: b for b in raw[-60:]}, {b['date']: b for b in adjusted[-60:]}
    result = {"status": "unverified", "basis": "raw", "checked_date": expected}
    try:
        if len(left) < 60 or set(left) != set(right) or max(left) != expected:
            return result
        ratios = [float(right[d]['close']) / float(left[d]['close']) for d in left]
        if not all(math.isfinite(x) and x > 0 for x in ratios):
            return result
        # A constant factor causes no trend discontinuity. Never use today's qfq price for past fills.
        deviation = max(ratios) / min(ratios) - 1
        result.update(status="stable" if deviation <= 0.005 else "corporate_action", factor_deviation=round(deviation,6))
    except (TypeError, KeyError, ValueError, ZeroDivisionError):
        pass
    return result


def normalize_flows(records):
    out=[]
    for r in records:
        try:
            out.append({"date":str(r['日期'])[:10],"net":float(r['主力净流入-净额']),"pct":float(r['主力净流入-净占比'])})
        except (ValueError,TypeError,KeyError):
            out.append({"date":str(r.get('日期','invalid'))[:10],"net":None,"pct":None})
    return out


def collect(timeout=18, codes=WATCHLIST):
    from .stage1 import eligible_code
    if not codes or len(codes)>100 or any(not eligible_code(c) for c in codes) or len(set(codes))!=len(codes):
        raise ValueError('股票范围须为1–100只符合前缀的不同主板代码')
    now = datetime.now(TZ)
    reports=[]
    def run(task):
        key, function, kwargs = task
        records, report=request_api(function,kwargs,timeout)
        history=[dict(report)]
        if report['status']!='ok':
            records,report=request_api(function,kwargs,timeout)
            history.append(dict(report))
        report.update(key=key,attempt_count=len(history),attempt_history=history)
        return key,records,report
    _,records,report=run(('calendar','tool_trade_date_hist_sina',{})); reports.append(report)
    calendar=sorted(set(str(r['trade_date'])[:10] for r in records if 'trade_date' in r))
    expected=expected_date(calendar,now)
    start=(now-timedelta(days=220)).date().isoformat()
    end=expected or now.date().isoformat()
    dates={'start_date':start.replace('-',''),'end_date':end.replace('-','')}
    tasks=[]
    for c in INDICES:
        symbol=('sh' if c=='000001' else 'sz')+c
        tasks += [(f'index:{c}:em','index_zh_a_hist',{'symbol':c,'period':'daily',**dates}),
                  (f'index:{c}:sina','stock_zh_index_daily',{'symbol':symbol})]
    for c in codes:
        symbol=('sh' if c.startswith('6') else 'sz')+c
        tasks += [(f'stock:{c}:em','stock_zh_a_hist',{'symbol':c,'period':'daily','adjust':'',**dates}),
                  (f'stock:{c}:tx','stock_zh_a_hist_tx',{'symbol':symbol,'adjust':'',**dates}),
                  (f'stock:{c}:adjusted','stock_zh_a_hist_tx',{'symbol':symbol,'adjust':'qfq',**dates}),
                  (f'meta:{c}','stock_individual_info_em',{'symbol':c,'timeout':timeout}),
                  (f'flow:{c}','stock_individual_fund_flow',{'stock':c,'market':symbol[:2]})]
    data={}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for key,records,report in pool.map(run,tasks):
            data[key]=records;reports.append(report)
    # Index fallback to Tencent if Sina is unavailable; keep one whole source series, never splice.
    fallback=[(f'index:{c}:tx','stock_zh_index_daily_tx',{'symbol':('sh' if c=='000001' else 'sz')+c,**dates})
              for c in INDICES if not data.get(f'index:{c}:sina')]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for key,records,report in pool.map(run,fallback):
            data[key]=records; reports.append(report)
    raw={'mode':'live','schema_version':'raw-2','source':'AKShare / 东方财富、腾讯、新浪（逐项来源与交叉验证见详情）',
         'calendar':calendar,'watchlist':list(codes),'indices':{},'stocks':{},'metadata':{},'stock_flows':{},'sectors':{},
         'provenance':{},'cross_checks':{},'adjustments':{},'interface_report':reports}
    for kind,items in [('index',INDICES),('stock',codes)]:
        for c in items:
            left=normalize(data.get(f'{kind}:{c}:em',[]),volume_multiplier=100,start=start,end=end)
            other='sina' if kind=='index' and data.get(f'index:{c}:sina') else 'tx'
            right=normalize(data.get(f'{kind}:{c}:{other}',[]),volume_multiplier=100 if kind=='index' and other=='tx' else 1,index_tx=kind=='index' and other=='tx',start=start,end=end)
            check=cross_check(left,right,expected,calendar,now)
            raw['cross_checks'][f'{kind}:{c}']=check
            chosen,source=(left,'东方财富') if left and not validate_bars(left,expected,calendar,now) else (right,{'sina':'新浪','tx':'腾讯'}[other])
            raw['indices' if kind=='index' else 'stocks'][c]=chosen
            raw['provenance'][f'{kind}:{c}']={'selected_source':source,'checked_sources':['东方财富',{'sina':'新浪','tx':'腾讯'}[other]],'quote_date':chosen[-1]['date'] if chosen else None,'cross_check':check}
            if kind=='stock':
                adjusted=normalize(data.get(f'stock:{c}:adjusted',[]),start=start,end=end)
                raw['adjustments'][c]=adjustment_check(chosen,adjusted,expected)
                meta={r.get('item'):r.get('value') for r in data.get(f'meta:{c}',[])}
                raw['metadata'][c]={'name':meta.get('股票简称'),'industry':meta.get('行业'),'listed_date':str(meta.get('上市时间','')),
                                    'effective_date':expected,'available_at':iso_now(),'source':'东方财富 stock_individual_info_em',
                                    'verified':str(meta.get('股票代码','')).zfill(6)==c and bool(meta.get('股票简称'))}
                raw['stock_flows'][c]=normalize_flows(data.get(f'flow:{c}',[]))
    industries=sorted(set(m['industry'] for m in raw['metadata'].values() if isinstance(m.get('industry'),str) and m['industry']))
    tasks=[]
    for industry in industries:
        tasks += [(f'sector:{industry}:bars','stock_board_industry_hist_em',{'symbol':industry,'period':'日k','adjust':'',**dates}),
                  (f'sector:{industry}:flows','stock_sector_fund_flow_hist',{'symbol':industry})]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for key,records,report in pool.map(run,tasks):
            data[key]=records;reports.append(report)
    for industry in industries:
        raw['sectors'][industry]={'bars':normalize(data.get(f'sector:{industry}:bars',[]),volume_multiplier=100,start=start,end=end),
                                   'flows':normalize_flows(data.get(f'sector:{industry}:flows',[])),
                                   'source':'东方财富 行业日线及带日期的历史资金流'}
    raw['collected_at']=iso_now()
    return raw
