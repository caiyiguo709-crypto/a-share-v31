"""Local, deterministic simulated execution. No broker/API order capability."""
import copy
import math
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from .config import MODEL
from .stage1 import eligible_code, size_plan


def cents(value):
    if not math.isfinite(float(value)):
        raise ValueError('金额非有限值')
    return int((Decimal(str(value))*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))


def model_for_day(day,slippage=None):
    if day<'2022-04-29':
        raise ValueError('费用政策仅支持2022-04-29及以后；不能套用当前费率到更早年份')
    model=replace(MODEL,stamp_tax_sell=.001 if day<'2023-08-28' else .0005)
    if slippage is not None:
        if not math.isfinite(slippage) or not 0<=slippage<=.03:raise ValueError('滑点须为0–3%')
        model=replace(model,slippage=slippage)
    return model


def charge_cents(notional_cents,day,sell=False,slippage=None):
    model=model_for_day(day,slippage)
    amount=Decimal(notional_cents)/100
    commission=max(Decimal(str(model.min_commission)),amount*Decimal(str(model.commission_rate)))
    extra=amount*(Decimal(str(model.transfer_rate))+ (Decimal(str(model.stamp_tax_sell)) if sell else Decimal(0)))
    return cents(commission+extra)


def quote_valid(bar,day,previous_close):
    try:
        if bar['date']!=day or previous_close<=0 or not math.isfinite(previous_close):return False
        o,h,l,c,v=(float(bar[k]) for k in ('open','high','low','close','volume'))
        return all(math.isfinite(x) and x>0 for x in (o,h,l,c,v)) and l<=min(o,c)<=max(o,c)<=h
    except (TypeError,KeyError,ValueError):
        return False


def limits(previous_close):
    # Normal mature main-board stocks only. ST/IPO/corporate actions are excluded before execution.
    return cents(Decimal(str(previous_close))*Decimal('1.10'))/100,cents(Decimal(str(previous_close))*Decimal('.90'))/100


def executable(bar,day,previous_close,side):
    if not quote_valid(bar,day,previous_close):return '执行行情异常/缺失/停牌'
    upper,lower=limits(previous_close)
    if bar['high']>upper+.011 or bar['low']<lower-.011:return '涨跌幅范围异常/公司行为待核验'
    if side=='buy' and bar['open']>=upper-.005:return '涨停价开盘，保守拒绝模拟买入'
    if side=='sell' and bar['open']<=lower+.005:return '跌停价开盘，保守拒绝模拟卖出'
    if abs(bar['high']-bar['low'])<.005:return '一字行情，无可确认流动性'
    return None


class PaperBook:
    def __init__(self,state=None):
        self.state=copy.deepcopy(state) if state is not None else {'schema_version':'paper-2','kind':'simulated_only',
                'initial_cash_cents':500000,'cash_cents':500000,'positions':{},'trades':[],'used_signals':[],
                'last_event_date':None,'warnings':[],'processed_sessions':[],'data_mode':None}
        self.validate()

    def validate(self):
        s=self.state
        if s.get('schema_version')!='paper-2' or s.get('kind')!='simulated_only':raise ValueError('非模拟账本或版本错误')
        if s.get('data_mode') not in (None,'demo','live'):raise ValueError('模拟账本数据模式异常')
        if type(s.get('cash_cents')) is not int or s['cash_cents']<0:raise ValueError('现金账本异常')
        if type(s.get('initial_cash_cents')) is not int or s['initial_cash_cents']!=500000:raise ValueError('模型本金应为5000元')
        if len(s.get('positions',{}))>2:raise ValueError('持仓超过2只')
        for code,p in s.get('positions',{}).items():
            if not eligible_code(code) or type(p.get('shares')) is not int or p['shares']<=0 or p['shares']%100:
                raise ValueError('持仓代码/股数异常')
            if not p.get('buy_date') or p.get('cost_cents',0)<=0 or not math.isfinite(p.get('stop',0)) or p['stop']<=0:
                raise ValueError('持仓成本/日期异常')
        if not isinstance(s.get('trades'),list) or not isinstance(s.get('used_signals'),list) or not isinstance(s.get('processed_sessions',[]),list):raise ValueError('交易记录异常')
        # Reconcile cash and positions from immutable execution events; reject edited/partial ledgers.
        cash=s['initial_cash_cents'];quantity={}
        for trade in s['trades']:
            if trade.get('side') not in ('BUY','SELL') or type(trade.get('shares')) is not int or trade['shares']<=0:
                raise ValueError('成交事件异常')
            code=trade['code']
            if not eligible_code(code) or type(trade.get('notional_cents')) is not int or type(trade.get('fee_cents')) is not int or trade['notional_cents']<=0 or trade['fee_cents']<0 or not math.isfinite(trade.get('price',0)) or trade['price']<=0:
                raise ValueError('成交金额/费用异常')
            if trade['notional_cents']!=trade['shares']*cents(trade['price']):raise ValueError('成交数量价格不一致')
            datetime.strptime(trade['date'],'%Y-%m-%d')
            if trade['side']=='BUY':
                cash-=trade['notional_cents']+trade['fee_cents'];quantity[code]=quantity.get(code,0)+trade['shares']
            else:
                cash+=trade['notional_cents']-trade['fee_cents'];quantity[code]=quantity.get(code,0)-trade['shares']
            if cash<0 or quantity.get(code,0)<0:raise ValueError('现金/持仓历史不守恒')
        if cash!=s['cash_cents'] or {c:q for c,q in quantity.items() if q} != {c:p['shares'] for c,p in s['positions'].items()}:
            raise ValueError('账本与成交记录不守恒')

    def _day(self,day,calendar):
        if calendar!=sorted(set(calendar)) or day not in calendar:raise ValueError('非有效交易日')
        if self.state['last_event_date'] and day<self.state['last_event_date']:raise ValueError('事件日期不能倒退')
        model_for_day(day)

    def buy(self,snapshot,code,bar,day,calendar,previous_close,*,execution_verified=False,allow_synthetic=False,slippage=None):
        self._day(day,calendar)
        row=next((r for r in snapshot.get('stocks',[]) if r['code']==code),None)
        if not row or not eligible_code(code):return {'filled':False,'reason':'标的未通过范围核验'}
        try:
            if datetime.fromisoformat(snapshot['valid_until'])<=datetime.fromisoformat(day+'T09:30:00+08:00'):
                return {'filled':False,'reason':'研究快照已过期'}
        except (KeyError,ValueError,TypeError):return {'filled':False,'reason':'研究快照失效时间异常'}
        signal_day=row.get('quote_date')
        if signal_day not in calendar or calendar.index(signal_day)+1>=len(calendar) or calendar[calendar.index(signal_day)+1]!=day:
            return {'filled':False,'reason':'只能在信号后的下一交易日买入'}
        signal_key=f'{signal_day}:{code}'
        if signal_key in self.state['used_signals']:return {'filled':False,'reason':'信号已使用，拒绝重复记账'}
        permit=row.get('research_permission') is True and snapshot.get('mode')=='live'
        if allow_synthetic and snapshot.get('mode')=='demo':
            permit=set(row.get('blockers',[])) <= {'合成演示数据禁止交易'} and row.get('score',0)>=80
        if not permit:return {'filled':False,'reason':'研究条件未全部通过/合成数据未经显式演示授权'}
        if self.state.get('data_mode') not in (None,snapshot.get('mode')):
            return {'filled':False,'reason':'禁止在同一模拟账本混合真实与合成行情'}
        if not execution_verified:return {'filled':False,'reason':'执行日行情/证券状态未核验'}
        if code in self.state['positions'] or len(self.state['positions'])>=2:return {'filled':False,'reason':'已持有该股或持仓达到2只'}
        reason=executable(bar,day,previous_close,'buy')
        if reason:return {'filled':False,'reason':reason}
        if abs(previous_close-row['indicators']['close'])>.011:return {'filled':False,'reason':'前收不一致/公司行为待核验'}
        if bar['open']>previous_close*1.02:return {'filled':False,'reason':'开盘跳空超过2%，取消追价'}
        stop=row['model_plan']['stop']
        model=model_for_day(day,slippage)
        plan=size_plan(bar['open'],stop,self.state['cash_cents']/100,len(self.state['positions']),model)
        if not plan['shares']:return {'filled':False,'reason':'执行价重算后整手/现金/50元风险不满足'}
        price=plan['estimated_buy']
        upper,_=limits(previous_close)
        if price>bar['high']+.005 or price>=upper-.005:return {'filled':False,'reason':'含滑点价格无法在可确认区间成交'}
        qty=plan['shares'];notional=qty*cents(price);fees=charge_cents(notional,day,slippage=slippage)
        if notional+fees>min(self.state['cash_cents'],250000):return {'filled':False,'reason':'预算不足'}
        stop_notional=qty*cents(plan['estimated_stop_fill'])
        risk=notional+fees-(stop_notional-charge_cents(stop_notional,day,True,slippage))
        if risk>5000:return {'filled':False,'reason':'含费用计划风险超过50元'}
        trade={'side':'BUY','date':day,'signal_date':signal_day,'code':code,'shares':qty,'price':price,'notional_cents':notional,'fee_cents':fees,
               'reason':'next_session_open','signal_key':signal_key,'synthetic':snapshot.get('mode')=='demo','planned_risk_cents':risk}
        self.state['cash_cents']-=notional+fees
        self.state['data_mode']=snapshot['mode']
        self.state['positions'][code]={'shares':qty,'buy_date':day,'cost_cents':notional+fees,'stop':stop,'entry_price':price,'last_mark':price}
        self.state['trades'].append(trade);self.state['used_signals'].append(signal_key);self.state['last_event_date']=day
        self.validate()
        return {'filled':True,'trade':trade}

    def sell(self,code,bar,day,calendar,previous_close,*,reason='manual_simulated_exit',reference=None,execution_verified=False,slippage=None):
        self._day(day,calendar)
        p=self.state['positions'].get(code)
        if not p:return {'filled':False,'reason':'无可卖模拟持仓'}
        if day<=p['buy_date']:return {'filled':False,'reason':'T+1：买入当日不可卖出'}
        if not execution_verified:return {'filled':False,'reason':'卖出行情/证券状态未核验'}
        error=executable(bar,day,previous_close,'sell')
        if error:return {'filled':False,'reason':error}
        ref=bar['open'] if reference is None else reference
        if not math.isfinite(ref) or not bar['low']-.005<=ref<=bar['high']+.005:return {'filled':False,'reason':'卖出参考价不在日内区间'}
        model=model_for_day(day,slippage)
        price=math.floor(ref*(1-model.slippage)*100+1e-8)/100
        _,lower=limits(previous_close)
        if price<=lower+.005 or price<bar['low']-.005:return {'filled':False,'reason':'滑点后卖出价无法确认成交/接近跌停'}
        qty=p['shares'];notional=qty*cents(price);fees=charge_cents(notional,day,True,slippage)
        trade={'side':'SELL','date':day,'code':code,'shares':qty,'price':price,'notional_cents':notional,'fee_cents':fees,
               'reason':reason,'pnl_cents':notional-fees-p['cost_cents'],'buy_date':p['buy_date']}
        self.state['cash_cents']+=notional-fees
        del self.state['positions'][code]
        self.state['trades'].append(trade);self.state['last_event_date']=day
        self.validate()
        return {'filled':True,'trade':trade}

    def equity(self,prices):
        total=self.state['cash_cents'];stale=[]
        for code,p in self.state['positions'].items():
            price=prices.get(code)
            if price is None or not math.isfinite(price) or price<=0:
                price=p['last_mark'];stale.append(code)
            else:p['last_mark']=price
            total+=p['shares']*cents(price)
        return {'equity_cents':total,'stale_marks':stale}
