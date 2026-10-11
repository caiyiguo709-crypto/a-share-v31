"""Stage 2 research scoring. Public output never claims to know a private account."""
import math
from datetime import datetime, timedelta
from .stage1 import TZ, iso_now, eligible_code, expected_date, validate_bars, indicators, buy_point, fee, size_plan
from .config import WATCHLIST, INDICES


def valid_clock(value, now, hours=36):
    try:
        stamp=datetime.fromisoformat(value)
        return -60 <= (now-stamp).total_seconds() <= hours*3600
    except (ValueError,TypeError):
        return False


def flow_stats(rows, expected, calendar):
    errors=[]
    days=[]
    if expected is None or len(rows)<5:
        return None,['带日期资金流不足5个交易日/日历不可用']
    for row in rows:
        try:
            day=row['date']; datetime.strptime(day,'%Y-%m-%d'); days.append(day)
            net,pct=float(row['net']),float(row['pct'])
            if day not in calendar or day>expected or not math.isfinite(net) or not math.isfinite(pct) or abs(pct)>100:
                raise ValueError('invalid flow')
        except (ValueError,TypeError,KeyError):
            errors.append('资金流日期或数值异常')
    if days!=sorted(set(days)):
        errors.append('资金流日期重复或未升序')
    required=[d for d in calendar if d<=expected][-5:]
    if days[-5:]!=required:
        errors.append('资金流过期或近5日缺口')
    if errors:
        return None,sorted(set(errors))
    last=rows[-5:]
    return {'date':expected,'net_today':float(last[-1]['net']),'net_5d':sum(float(r['net']) for r in last),
            'pct_today':float(last[-1]['pct']),'positive_days':sum(float(r['net'])>0 for r in last)},[]


def sector_analysis(raw,expected,calendar,now,benchmark=None):
    result=[]
    for name,sector in raw.get('sectors',{}).items():
        bars=sector.get('bars',[])
        errors=validate_bars(bars,expected,calendar,now)
        flow,flow_errors=flow_stats(sector.get('flows',[]),expected,calendar)
        errors+=flow_errors
        if errors:
            result.append({'name':name,'quality':sorted(set(errors)),'score':None,'hot_phase':'待核验','mainline':False,'source':sector.get('source','未知')})
            continue
        ind=indicators(bars)
        r5=(bars[-1]['close']/bars[-6]['close']-1)*100
        prior5=(bars[-6]['close']/bars[-11]['close']-1)*100
        distance=ind['close']/ind['ma20']-1
        if distance>.10 or r5>12:
            phase='过热'
        elif r5>0 and flow['net_5d']>0 and flow['net_today']>0:
            phase='启动' if prior5<=0 else '延续'
        elif flow['net_5d']<=0 and r5<0:
            phase='退潮'
        else:
            phase='分化'
        score=8*(ind['close']>ind['ma20'])+6*(r5>0)+6*(benchmark is not None and r5>benchmark)
        result.append({'name':name,'quality':[],'score':score,'hot_phase':phase,'return_5d_pct':round(r5,3),
                       'flow':flow,'mainline':score>=14 and flow['positive_days']>=3 and phase in ('启动','延续'),
                       'source':sector.get('source','未知'),'quote_date':expected})
    result.sort(key=lambda x:(x['score'] is not None,x['score'] or 0),reverse=True)
    return result


def analyze(raw,now=None):
    now=(now or datetime.now(TZ)).astimezone(TZ)
    calendar=raw.get('calendar',[])
    expected=expected_date(calendar,now)
    stale=not valid_clock(raw.get('collected_at'),now)
    demo=raw.get('mode')=='demo'
    indices=[]
    for code,name in INDICES.items():
        bars=raw.get('indices',{}).get(code,[])
        structural=validate_bars(bars,expected,calendar,now)
        quality=list(structural)
        check=raw.get('cross_checks',{}).get('index:'+code,{})
        if check.get('status')!='passed':quality.append('指数独立来源交叉核验未通过')
        if raw.get('mode')=='live' and check.get('fixture_only'):quality.append('演示指数校验不能用于真实研究许可')
        if stale:quality.append('采集时间缺失、过期或异常')
        ind=indicators(bars) if not structural and not stale else None
        indices.append({'code':code,'name':name,'quality':quality,'indicators':ind,
                        'quote_date':bars[-1].get('date') if bars else None,'provenance':raw.get('provenance',{}).get('index:'+code,{})})
    market_good=all(not r['quality'] for r in indices)
    bullish=sum(bool(r['indicators'] and r['indicators']['close']>r['indicators']['ma20'] and r['indicators']['macd']>0) for r in indices)
    cycle=('A' if bullish==3 else 'B' if bullish>=1 else 'C') if market_good else 'UNKNOWN'
    benchmark=None
    sh=raw.get('indices',{}).get('000001',[])
    if len(sh)>=6 and indices[0]['indicators']:
        benchmark=(sh[-1]['close']/sh[-6]['close']-1)*100
    sectors=sector_analysis(raw,expected,calendar,now,benchmark)
    sectors_by_name={s['name']:s for s in sectors}
    stocks=[]
    for code in raw.get('watchlist',WATCHLIST):
        bars=raw.get('stocks',{}).get(code,[])
        structural=validate_bars(bars,expected,calendar,now)
        quality=list(structural)
        meta=raw.get('metadata',{}).get(code,{})
        name=meta.get('name')
        if not isinstance(name,str) or not name:
            quality.append('当期名称/ST/退市状态未核验');name='名称待核验'
        if any(x in name.upper() for x in ('ST','退')):quality.append('风险警示/退市股票禁止开仓')
        if not eligible_code(code):quality.append('不在初始沪深主板范围')
        if stale:quality.append('采集时间缺失、过期或异常')
        if not meta.get('verified') or meta.get('effective_date')!=expected or not valid_clock(meta.get('available_at'),now):
            quality.append('证券元数据时点/来源未核验')
        if raw.get('mode')=='live' and (raw.get('cross_checks',{}).get('stock:'+code,{}).get('fixture_only') or raw.get('adjustments',{}).get(code,{}).get('fixture_only')):
            quality.append('演示校验标记不能用于真实研究许可')
        if raw.get('cross_checks',{}).get('stock:'+code,{}).get('status')!='passed':
            quality.append('个股独立来源交叉核验未通过')
        if raw.get('adjustments',{}).get(code,{}).get('status')!='stable' or raw.get('adjustments',{}).get(code,{}).get('checked_date')!=expected:
            quality.append('复权一致性/公司行为未通过')
        # Technical observations may be shown for valid bars; permissions still require all quality gates.
        ind=indicators(bars) if not structural and not stale else None
        point=buy_point(bars,ind) if ind else '待数据核验'
        stop=round(min(float(b['low']) for b in bars[-5:])*.99,2) if ind else None
        plan=size_plan(ind['close'],stop) if ind else None
        industry=sectors_by_name.get(meta.get('industry'))
        flows,flow_errors=flow_stats(raw.get('stock_flows',{}).get(code,[]),expected,calendar)
        industry_score=industry['score'] if industry and not industry['quality'] else None
        fund_score=(6*(flows['net_today']>0)+8*(flows['net_5d']>0)+6*(flows['pct_today']>0)) if flows else None
        trend=(10*(ind['close']>ind['ma20'])+10*(ind['ma5']>=ind['ma10']>=ind['ma20'])+5*(ind['macd']>0)) if ind else None
        entry=20 if '候选' in point else 0 if ind else None
        account=15 if plan and plan['shares']>=100 else 0 if ind else None
        scores={'industry':industry_score,'funds':fund_score,'trend':trend,'entry':entry,'account':account}
        score=sum(scores.values()) if all(v is not None for v in scores.values()) else None
        blockers=list(quality)+flow_errors
        if not market_good:blockers.append('指数数据质量未通过')
        if cycle!='A':blockers.append('市场条件未通过')
        if industry_score is None:blockers.append('行业维度数据未通过')
        if not industry or not industry.get('mainline'):blockers.append('行业未形成观察主线/热点阶段不适合')
        if fund_score is None or fund_score<14:blockers.append('个股资金条件未通过')
        if trend is None or trend<20:blockers.append('趋势条件未通过')
        if '候选' not in point:blockers.append('买点未确认')
        if not plan or plan['shares']==0:blockers.append('100股/50元计划风险/2500元预算约束未通过')
        if score is None or score<80:blockers.append('五维总分未达到80/100或数据缺项')
        if raw.get('mode')!='live':blockers.append('合成演示数据禁止交易')
        research=not blockers
        stocks.append({'code':code,'name':name,'quote_date':bars[-1].get('date') if bars else None,'quality':sorted(set(quality)),
                       'indicators':ind,'scores':scores,'score':score,'technical_subtotal':sum(v for k,v in scores.items() if k in ('trend','entry','account') and v is not None) if ind else None,
                       'industry':meta.get('industry'),'hot_phase':industry.get('hot_phase') if industry else '待核验','flow':flows,
                       'buy_point':point,'model_plan':plan,'research_permission':research,'buy_permission':False,
                       'permission_scope':'仅研究条件；模拟账户须在本地重新核验现金、持仓、T+1与执行价格',
                       'blockers':sorted(set(blockers)),'chart':[b['close'] for b in bars[-30:]] if ind else [],
                       'provenance':raw.get('provenance',{}).get('stock:'+code,{})})
    deadline=now+timedelta(hours=36)
    if expected:
        future=[d for d in calendar if d>expected]
        if future:deadline=min(deadline,datetime.fromisoformat(future[0]+'T16:00:00+08:00'))
    return {'schema_version':'2.0','stage':2,'version':'V3.1 · Stage 2','generated_at':now.isoformat(timespec='seconds'),
            'collected_at':raw.get('collected_at'),'expected_quote_date':expected,'mode':raw.get('mode','unavailable'),'source':raw.get('source','未知'),
            'latency':'完整日线收盘分析，非实时；下一交易日执行前须重新核验','stale_collection':stale,'valid_until':deadline.isoformat(timespec='seconds'),
            'market':{'cycle':cycle,'bullish_indices':bullish,'quality_pass':market_good,'description':{'A':'三指数趋势共振（实验规则）','B':'指数分化，暂停新开仓','C':'弱势防御，暂停新开仓','UNKNOWN':'数据未通过，无法判断'}[cycle]},
            'indices':indices,'stocks':stocks,'sector':{'status':'ready' if sectors and all(not s['quality'] for s in sectors) else 'blocked',
                'description':'自选行业范围的相对强度与带日期资金流；不代表全市场资金排名','items':sectors},
            'model_constraints':{'capital':5000,'positions_max':2,'risk_per_trade':50,'position_budget':2500,'label':'策略模型假设，非真实账户'},
            'warnings':['公开研究条件不等于实际买入许可；模拟账本仅在本地','50元为计划风险，T+1、跳空及跌停可能导致超额损失','原始价格用于趋势与成交；公司行为窗口未处理时禁止新开仓','所有演示及演示回测使用合成数据，不能作为收益证据'],
            'interface_report':raw.get('interface_report',[])}
