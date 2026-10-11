"""Complete synthetic fixtures for scoring and execution QA; never historical returns."""
import copy
import math
from datetime import datetime,timedelta
from .config import WATCHLIST,INDICES
from .stage1 import TZ


def synthetic_dataset(sessions=45):
    now=datetime.now(TZ)
    end=now.date()
    dates=[(end+timedelta(days=i)).isoformat() for i in range(-260,25) if (end+timedelta(days=i)).weekday()<5]
    history=[d for d in dates if d<=end.isoformat()]
    # Artificial weekday calendar deliberately includes exchange holidays.
    stock_series={};index_series={}
    for code in INDICES:
        base=3000 if code=='000001' else 10000 if code=='399001' else 2000
        index_series[code]=[{'date':d,'open':round(base*(1+.0015*i)*.999,2),'close':round(base*(1+.0015*i),2),
                            'high':round(base*(1+.0015*i)*1.01,2),'low':round(base*(1+.0015*i)*.99,2),'volume':1000000+i*100} for i,d in enumerate(history)]
    for k,code in enumerate(WATCHLIST):
        base=4+k*.6
        bars=[]
        for i,d in enumerate(history):
            price=round(base*(1+.0018*i+.012*math.sin(i/4+k/3)),2)
            bars.append({'date':d,'open':price,'close':price,'high':round(price*1.015,2),'low':round(price*.99,2),'volume':10000})
        stock_series[code]=bars
    snapshots=[]
    for i in range(len(history)-sessions,len(history)):
        day=history[i];stamp=day+'T16:17:00+08:00';industry='合成示例行业'
        def trim(bars):return copy.deepcopy(bars[max(0,i-99):i+1])
        sectorbars=trim(index_series['000001'])
        # Sector stronger than SH, positive dated flows.
        for j,b in enumerate(sectorbars):
            scale=1+j*.0003
            for f in ('open','close','high','low'):b[f]=round(b[f]*scale,2)
        flows=[{'date':d,'net':1000000,'pct':3.0} for d in history[max(0,i-19):i+1]]
        raw={'mode':'demo','schema_version':'raw-2','source':'本地合成演示：人工价格、资金流、名称、日历及行业；非真实行情',
             'collected_at':stamp,'calendar':dates,'watchlist':list(WATCHLIST),
             'indices':{c:trim(b) for c,b in index_series.items()},'stocks':{c:trim(b) for c,b in stock_series.items()},
             'metadata':{c:{'name':'合成标的','industry':industry,'verified':True,'effective_date':day,'available_at':stamp,'source':'synthetic_fixture'} for c in WATCHLIST},
             'stock_flows':{c:copy.deepcopy(flows) for c in WATCHLIST},'sectors':{industry:{'bars':sectorbars,'flows':copy.deepcopy(flows),'source':'synthetic_fixture'}},
             'cross_checks':{f'{kind}:{c}':{'status':'passed','sources_valid':2,'fixture_only':True} for kind,codes in [('index',INDICES),('stock',WATCHLIST)] for c in codes},
             'adjustments':{c:{'status':'stable','basis':'raw','checked_date':day,'fixture_only':True} for c in WATCHLIST},
             'provenance':{f'stock:{c}':{'selected_source':'合成示例','checked_sources':['合成示例A','合成示例B'],'fixture_only':True} for c in WATCHLIST},'interface_report':[]}
        snapshots.append(raw)
    return {'schema_version':'replay-2','mode':'demo','universe_policy':'fixed_watchlist_demo','source':'合成演示，非真实历史行情',
            'corporate_actions':[],'snapshots':snapshots}


def synthetic_current():
    dataset=synthetic_dataset(2)
    raw=dataset['snapshots'][-1]
    # A Sunday view has Friday market data; regenerate collected/meta timestamps for UI only.
    now=datetime.now(TZ).isoformat(timespec='seconds')
    raw['collected_at']=now
    for meta in raw['metadata'].values():meta['available_at']=now
    return raw
