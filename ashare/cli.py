import argparse
import csv
import io
import json
import os
from pathlib import Path
from .engine import analyze
from .storage import PRIVATE, private_path, atomic_json, transact


def write_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    os.replace(temp,path)


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def archive_dataset(directory):
    from .replay import snapshot_day
    selected={}
    for path in sorted(private_path(directory).glob('*.json')):
        raw=load_json(path)
        day,stamp=snapshot_day(raw)
        calendar=raw['calendar'];ix=calendar.index(day)
        from datetime import datetime
        if ix+1<len(calendar) and stamp>=datetime.fromisoformat(calendar[ix+1]+'T09:30:00+08:00'):
            continue # A late downloaded past snapshot was not known before next open.
        if day not in selected or stamp>selected[day][0]:selected[day]=(stamp,raw)
    return {'schema_version':'replay-2','mode':'live','universe_policy':'point_in_time_archive','source':'本地逐日实际采集快照',
            'corporate_actions':[],'snapshots':[selected[d][1] for d in sorted(selected)]}


def main():
    parser=argparse.ArgumentParser(description='第二阶段A股研究/本地模拟工具，无真实下单')
    subs=parser.add_subparsers(dest='command',required=True)
    build=subs.add_parser('build');build.add_argument('--mode',choices=['demo','live'],required=True)
    build.add_argument('--input');build.add_argument('--output',default='site/data/analysis.json')
    build.add_argument('--report',default='docs/interface-report.json');build.add_argument('--timeout',type=int,default=18)
    build.add_argument('--codes',help='逗号分隔，默认7只自选，最多100只');build.add_argument('--archive',action='store_true')
    back=subs.add_parser('backtest');group=back.add_mutually_exclusive_group(required=True)
    group.add_argument('--input');group.add_argument('--archive');group.add_argument('--demo',action='store_true')
    back.add_argument('--allow-synthetic',action='store_true');back.add_argument('--output',default=str(PRIVATE/'backtest.json'))
    back.add_argument('--sensitivity',action='store_true')
    for command in ('paper-init','paper-status','paper-run'):
        p=subs.add_parser(command);p.add_argument('--ledger',default=str(PRIVATE/'paper-ledger.json'))
        if command=='paper-run':
            p.add_argument('--signal-raw',required=True);p.add_argument('--execution-raw',required=True)
            p.add_argument('--allow-synthetic',action='store_true')
    export=subs.add_parser('export-trades');export.add_argument('--ledger',default=str(PRIVATE/'paper-ledger.json'))
    export.add_argument('--output',default=str(PRIVATE/'trades.csv'))
    v41=subs.add_parser('v41-scan',help='V4.1全主板收盘快照扫描；只生成研究候选')
    v41.add_argument('--snapshot',required=True);v41.add_argument('--state',default=str(PRIVATE/'v41-state.json'))
    v41replay=subs.add_parser('v41-replay',help='回放逐日时点快照；不计算交易收益')
    v41replay.add_argument('--input',required=True);v41replay.add_argument('--output',default=str(PRIVATE/'v41-replay.json'))
    v41collect=subs.add_parser('v41-collect',help='采集当前沪深主板全市场研究快照；不赋予交易许可')
    v41collect.add_argument('--timeout',type=int,default=18)
    v41collect.add_argument('--output')
    args=parser.parse_args()
    if args.command=='v41-scan':
        from .v41 import scan_file
        result=scan_file(args.snapshot,args.state)
        print(json.dumps({'asof':result['asof'],'universe_count':result['universe_count'],
                          'coverage_complete':result['coverage_complete'],'candidates':len(result['candidates']),
                          'buy_permission':False,'state':args.state},ensure_ascii=False))
        return
    if args.command=='v41-replay':
        from .v41 import replay
        result=replay(load_json(args.input)['snapshots'])
        atomic_json(args.output,result)
        print(json.dumps({'asof':result['asof'],'candidates':len(result['candidates']),
                          'metrics':result['replay_metrics'],
                          'buy_permission':False,'output':args.output},ensure_ascii=False))
        return
    if args.command=='v41-collect':
        from .v41 import collect_daily
        result=collect_daily(args.timeout)
        stamp=result['captured_at'].replace(':','').replace('+','_')
        output=args.output or str(PRIVATE/'v41-snapshots'/(stamp+'.json'))
        if Path(output).exists():raise ValueError('当日快照已存在，不覆盖时点档案')
        atomic_json(output,result)
        print(json.dumps({'asof':result['asof'],'universe_count':len(result['universe']),
                          'coverage_complete':result['universe_complete'],
                          'collection_failures':len(result['collection_failures']),
                          'buy_permission':False,'output':output},ensure_ascii=False))
        return
    if args.command=='build':
        if args.input:
            raw=load_json(args.input)
            if raw.get('mode')!=args.mode:raise ValueError('输入模式不一致；禁止把合成数据冒充真实数据')
        elif args.mode=='demo':
            from .fixtures import synthetic_current
            raw=synthetic_current()
        else:
            from .provider import collect
            from .config import WATCHLIST
            raw=collect(args.timeout,tuple(args.codes.split(',')) if args.codes else WATCHLIST)
        snapshot=analyze(raw)
        # No ledger state is accepted by analyze or serialized into the site.
        write_json(Path(args.output),snapshot)
        if args.mode=='live':
            write_json(Path(args.report),{'collected_at':raw['collected_at'],'source':raw['source'],
                'calendar_end':max(raw['calendar']) if raw['calendar'] else None,'requests':raw.get('interface_report',[]),
                'cross_checks':raw.get('cross_checks',{}),'provenance':raw.get('provenance',{}),'adjustments':raw.get('adjustments',{}),
                'data_quality':{'market_pass':snapshot['market']['quality_pass'],'stocks_pass':sum(not r['quality'] for r in snapshot['stocks']),
                                'expected_quote_date':snapshot['expected_quote_date'],'research_permits':sum(r['research_permission'] for r in snapshot['stocks'])}})
            if args.archive:
                day=snapshot['expected_quote_date']
                if day:
                    timestamp=raw['collected_at'].replace(':','').replace('+','_')
                    atomic_json(PRIVATE/'archive'/f'{day}_{timestamp}.json',raw)
        print(json.dumps({'mode':snapshot['mode'],'output':args.output,'market_cycle':snapshot['market']['cycle'],
                          'research_permissions':sum(r['research_permission'] for r in snapshot['stocks']),'buy_permissions':0},ensure_ascii=False))
    elif args.command=='backtest':
        from .fixtures import synthetic_dataset
        from .replay import run_backtest,sensitivity
        dataset=synthetic_dataset() if args.demo else archive_dataset(args.archive) if args.archive else load_json(args.input)
        result=run_backtest(dataset,allow_synthetic=args.allow_synthetic)
        if args.sensitivity:result['sensitivity']=sensitivity(dataset,args.allow_synthetic)
        atomic_json(args.output,result)
        print(json.dumps({'output':str(private_path(args.output)),'mode':result['mode'],'metrics':result['metrics']},ensure_ascii=False))
    elif args.command in ('paper-init','paper-status'):
        result=transact(args.ledger,lambda book:{'kind':'simulated_only','cash':book.state['cash_cents']/100,
                           'positions':len(book.state['positions']),'trades':len(book.state['trades']),'data_mode':book.state.get('data_mode')})
        print(json.dumps(result,ensure_ascii=False))
    elif args.command=='paper-run':
        from .replay import advance
        signal,execution=load_json(args.signal_raw),load_json(args.execution_raw)
        # This command advances historical or collected sessions, never transmits orders.
        result=transact(args.ledger,lambda book:advance(book,signal,execution,allow_synthetic=args.allow_synthetic))
        print(json.dumps({'kind':'simulated_only','events':result},ensure_ascii=False))
    elif args.command=='export-trades':
        state=transact(args.ledger,lambda book:book.state)
        output=private_path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
        fields=['date','side','code','shares','price','fee_cents','pnl_cents','reason']
        buffer=io.StringIO();writer=csv.DictWriter(buffer,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(state['trades'])
        output.write_text(buffer.getvalue(),encoding='utf-8-sig');os.chmod(output,0o600)
        print(json.dumps({'output':str(output),'records':len(state['trades']),'kind':'simulated_only'},ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,OSError,json.JSONDecodeError) as error:
        raise SystemExit('操作已拒绝：'+str(error))
