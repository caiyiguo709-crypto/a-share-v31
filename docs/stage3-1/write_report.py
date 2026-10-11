"""Generate the Stage 3.1 audit from immutable Stage 2 evidence."""
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def link(label, relative):
    return f"[{label}]({ROOT / relative})"


def main():
    original_path = ROOT / 'docs/interface-report-stage2.json'
    original = json.loads(original_path.read_text())
    probes = json.loads((OUT / 'representative-live-probes.json').read_text())
    baseline = json.loads((OUT / 'baseline-verification.json').read_text())
    definitions = {
        'index_zh_a_hist': {'family': '指数历史日线', 'group': 'R1', 'priority': '最高',
            'endpoint': '80.push2.eastmoney.com/api/qt/clist/get → push2his.eastmoney.com/api/qt/stock/kline/get',
            'impact': '三指数缺东方财富独立价格，市场质量门禁失败',
            'repair': '先诊断指数代码映射预请求，再恢复独立指数源；保留证券类型/市场，双源完整核验后才接受'},
        'stock_zh_a_hist': {'family': '股票历史日线', 'group': 'R2', 'priority': '最高',
            'endpoint': 'push2his.eastmoney.com/api/qt/stock/kline/get',
            'impact': '仅剩腾讯单源，个股交叉核验不能通过',
            'repair': '恢复或验证独立股票日线源；明确不复权、股/元单位、交易日、代码市场，不允许单源替代双源门禁'},
        'stock_individual_info_em': {'family': '股票元数据', 'group': 'R3', 'priority': '最高',
            'endpoint': 'push2.eastmoney.com/api/qt/stock/get',
            'impact': '名称/风险状态未核验；行业分类缺失，行业任务未运行',
            'repair': '拆分证券身份/风险状态与行业分类；验证免费独立证券资料源及版本化行业映射，未知状态继续阻断'},
        'stock_individual_fund_flow': {'family': '个股主力资金流', 'group': 'R4', 'priority': '次级',
            'endpoint': 'push2his.eastmoney.com/api/qt/stock/fflow/daykline/get',
            'impact': '主力资金缺失，当前策略资金门禁失败',
            'repair': '主筛改用真实成交额/RVOL/量价结构；第三方资金单独作P2验证，缺失留空并限制可得分，不填0或满分'},
    }
    cases = []
    for number, request in enumerate(original['requests'], 1):
        if request['status'] == 'ok':
            continue
        definition = definitions[request['interface']]
        cases.append({
            'original_case_number': number, 'key': request['key'], 'interface': request['interface'],
            'parameters': request['parameters'], 'error': request['error'], 'rows': request['rows'], 'fields': request['fields'],
            'attempt_count': request['attempt_count'], 'attempt_history': request['attempt_history'],
            'environment': {'runner': 'local macOS, not verified GitHub Actions', 'akshare_version': request['akshare_version'],
                            'python_version_evidence': '3.12.14 stated in docs/stage2-report.md',
                            'timezone': 'Asia/Shanghai', 'session': 'Sunday non-trading hours', 'target_completed_trade_date': '2026-10-09'},
            'category': 1, 'shared_diagnostic_defect_category': 6, 'repair_group': definition['group'],
            'root_cause': 'Original evidence proves ProxyError at the request transport layer; specific proxy/egress/upstream cause remains unresolved.',
            'root_cause_confidence': 'exception class confirmed for each case; detailed chain only reproduced for one representative per interface family',
            'http_status': None, 'historical_dns_status': 'not recorded', 'historical_proxy_configuration': 'not recorded',
            'endpoint_from_installed_sdk': definition['endpoint'], 'impact': definition['impact'],
            'implementation_priority': definition['priority'], 'repair_plan': definition['repair'],
            'representative_probe_evidence': 'representative-live-probes.json (family-level corroboration, not replay of every case)',
        })
    assert len(cases) == 24
    assert all(c['attempt_count'] == 2 and all(a['error'] == 'ProxyError' for a in c['attempt_history']) for c in cases)
    repository = {
        'local_git_metadata_found': False, 'local_git_remote_available': False, 'attached_artifacts': [],
        'github_connector_repository_list': [], 'github_connector_search_result': 'API 422: invalid resources, nonexistent or inaccessible',
        'actions_run_url': None, 'actions_logs_reviewed': False,
        'pending_input': 'Existing repository URL and related Actions run URL or exported logs',
        'local_stage2_evidence': {'remote_repository_created': False, 'site_published': False},
        'note': 'No claim that an external repository does not exist. It cannot be located with current local/connector evidence. Workflow YAML was reviewed; it is not a run log.',
    }
    audit = {
        'stage': '3.1 diagnostic only', 'generated_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),
        'original_report': {'path': str(original_path), 'sha256': hashlib.sha256(original_path.read_bytes()).hexdigest(),
                            'collected_at': original['collected_at'], 'cases': 42, 'ok': 18, 'failed': 24,
                            'application_attempts': sum(r['attempt_count'] for r in original['requests']),
                            'failed_application_attempts': sum(c['attempt_count'] for c in cases)},
        'failures_by_interface': dict(Counter(c['interface'] for c in cases)),
        'observed_primary_classification': {'1_network_or_proxy_transport': 24, '2_confirmed_http_restriction': 0,
            '3_confirmed_upstream_schema': 0, '4_confirmed_session_or_update': 0, '5_confirmed_format_unit_adjustment_request_error': 0,
            '6_confirmed_dependency_or_program_request_error': 0},
        'classification_note': 'Zero means no confirmed primary cause in the original 24, not proof that a cause is impossible. Category 6 has a shared observability defect, not 24 additional request failures.',
        'repository_log_audit': repository, 'cases': cases,
        'new_probe_summary': {'real_calls': 6, 'ok': 2, 'failed': 4, 'purpose': 'representative failure diagnosis, not full acceptance'},
        'functional_baseline': baseline['functional_tests'], 'protected_files_unchanged': baseline['all_protected_files_unchanged'],
        'next_action_requires_user_confirmation': True,
    }
    (OUT / 'failure-diagnosis.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
    rows = []
    for c in cases:
        symbol = c['parameters'].get('symbol', c['parameters'].get('stock'))
        hours = ' / '.join(a['collected_at'][11:19] for a in c['attempt_history'])
        rows.append(f"| {c['original_case_number']} | `{c['interface']}` / `{symbol}` | `{c['key']}` | {hours} | `ProxyError` ×2；0行 | E1 | ①链路代理连接异常；⑥详情丢失 | {c['repair_group']} / {c['implementation_priority']} | {c['repair_plan']} |")
    table = '\n'.join(rows)
    probe_rows = '\n'.join(f"| `{r['interface']}` / `{r['parameters'].get('symbol', r['parameters'].get('stock'))}` | {r['started_at'][11:19]}–{r['finished_at'][11:19]} | {'成功，HTTP 200，'+str(r['rows'])+'行' if r['status']=='ok' else 'ProxyError → MaxRetryError → ProxyError → RemoteDisconnected；无HTTP响应状态'} |" for r in probes['results'])
    report = f"""# 《5000元A股智能选股系统 V3.1》阶段3.1诊断报告

日期：2026-10-11，Asia/Shanghai。范围：现有第二阶段代码与失败记录审计、只读真实请求诊断、原测试回归、修复实施计划。

**24项失败的共同故障层已定位到东方财富请求链路的代理连接异常；代理、出口网络和上游服务之间的最终责任点尚不能判定。没有证据把这些失败归为403/429、字段变化或非交易时段。** 本次仅新增诊断文档和独立诊断脚本；核心行情架构、评分、测试、依赖及工作流均未修改。

**GitHub Actions日志核验仍待补充。** 本地没有Git元数据/remote，附件列表为空，当前GitHub连接的可访问仓库列表为空，仓库搜索返回422。第二阶段记录也写明未创建/推送远程仓库。已请求提供现有仓库和Actions运行URL；这不证明用户在其他位置没有仓库。本报告以当前本地交付代码为依据，不能替代对远程提交及运行日志的核验。

## 1. 已读取的证据与完整性

| 证据 | 核验内容 |
| --- | --- |
| {link('第二阶段42项接口记录', 'docs/interface-report-stage2.json')} | 全部请求参数、结果、重试历史、字段、时长、质量/复权/交叉核验记录 |
| {link('当前接口报告', 'docs/interface-report.json')} | 同次20:26:37采集；不能当作第二次独立测试 |
| {link('第一轮报告', 'docs/interface-report-first.json')}、{link('第一阶段末报告', 'docs/interface-report-stage1-final.json')} | 早期14项结果，与当前42项范围不同，不合并通过率 |
| {link('第二阶段验收说明', 'docs/stage2-report.md')}、{link('验证摘要', 'docs/stage2-validation.json')} | 103项功能测试、18/42请求、无远程发布、真实链路限制 |
| {link('provider.py', 'ashare/provider.py')}、{link('worker.py', 'ashare/worker.py')}、{link('cli.py', 'ashare/cli.py')} | 全部真实采集任务矩阵、子进程协议、重试、接口报告生成与退出逻辑 |
| {link('engine.py', 'ashare/engine.py')}、{link('stage1.py', 'ashare/stage1.py')} | 评分、行业/资金依赖、时间、单位、完整性、买入门禁 |
| {link('安全测试', 'tests/test_safety.py')}、{link('阶段二测试', 'tests/test_stage2.py')} | 离线功能测试、monkeypatch和合成夹具的使用边界；不是42项在线接口验收程序 |
| {link('collect工作流', '.github/workflows/collect.yml')}、{link('CI工作流', '.github/workflows/ci.yml')}、依赖锁 | 静态配置已读；Ubuntu运行、安装、网络、HTTP错误的实际日志尚未取得 |
| 实际原始行情快照与单日归档 | 仅本地读取公开行情部分，无账户/持仓披露；本报告不复制私人账本 |
| 已安装AKShare 1.19.1源码 | 指数映射、股票日线、元数据、资金流、行业与腾讯单位转换，及SDK内层重试 |

原报告完整采集时间为 **20:26:37**，目标完整交易日 **2026-10-09**。42项是采集器的42个逻辑请求用例，包含同一股票的不复权和前复权请求，并非42个独立接口函数。共66次应用层尝试，其中48次失败尝试全部记录为`ProxyError`；SDK内部的实际HTTP次数未在旧报告中记录，不能把66视为网络请求总次数。

## 2. 六类原因分类及证据强度

| 用户要求的分类 | 24项中的已确认主因数 | 判断、证据与仍需验证的问题 |
| --- | ---: | --- |
| ①网络连接或DNS问题 | 24 | 精确到代理连接异常层；旧记录仅有ProxyError。新增4类代表请求均得到RemoteDisconnected链，支持选择性链路失败。没有证据确认为DNS；4个已探测域名当前可解析，不代表故障时DNS必然正常，也未覆盖所有子域名。 |
| ②403、429或访问限制 | 0已证实 | 失败请求没有HTTP响应状态。上游/网关拒绝连接仍是可能性，但不能把ProxyError直接标成403/429。 |
| ③上游变化或字段缺失 | 0已证实 | 失败请求尚未得到可解析行情；没有KeyError/缺列证据。函数与参数在当前SDK存在；恢复连接后仍需独立契约测试。 |
| ④非交易时段/尚未更新 | 0已证实为请求主因 | 周日查询的是已完成历史日线；同期新浪和腾讯成功。周日不能验证盘中更新和收盘发布时延，这些项目标为待交易时段复测。 |
| ⑤格式、日期、单位、复权差异 | 0已证实为24项请求主因 | 另有3只复权因子变化、10组双源未核验、成交额被归一化丢弃等质量问题，单列，不冒充这24项请求根因。 |
| ⑥Python依赖/逻辑错误 | 0已证实为请求主因 | 共享程序缺陷已确认：异常只保存类名，根因链/HTTP状态/目标地址丢失；行业依赖导致测试覆盖空洞。103项离线通过不能证明线上依赖完全兼容。 |

上述计数采用互斥的**观测主故障层**，不是最终网络责任归因；“0已证实”不表示排除了可能性。⑥的共享诊断缺陷适用于全部失败，不额外计算24次。

## 3. 24项逐项诊断

**E1发生环境：** 原本地macOS采集，Python 3.12.14（第二阶段说明）、AKShare 1.19.1，Asia/Shanghai，2026-10-11周日非交易时段。每项两次尝试都失败、0行、空字段；不是GitHub Actions运行记录。时间列为第一次/第二次尝试的开始时间，均为北京时间；完整参数、逐次时长见 {link('逐项结构化诊断', 'docs/stage3-1/failure-diagnosis.json')}。

**共同原因限定：** 每一项均已证实ProxyError，尚未记录当时的底层异常链、HTTP状态、DNS和代理配置。下面方案针对相应依赖和故障层；新增代表性请求不能替代对其他代码的逐项复测。

| 原42项编号 | 接口 / 标的 | 用例键 | 首次 / 再次时间 | 原始错误 | 环境 | 原因分类 | 修复组 / 实施优先级 | 逐项修复方案 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
{table}

R1、R2共同参数：日线，20260305–20261009；R2 `adjust=''`，明确不复权。R3原请求timeout=18；R4沪市代码market=sh，深市代码market=sz。保留原六位代码，不能将上证指数000001误当深市股票000001。

| 修复组 | SDK实际请求路径与关键依赖 | 影响和处理 |
| --- | --- | --- |
| R1 指数3项 | 先`80.push2.eastmoney.com/api/qt/clist/get`查询映射，再`push2his.eastmoney.com/api/qt/stock/kline/get` | 新探测在第一步映射请求即失败，尚未到K线。指数、股票同代码必须使用证券类型+交易所键；不能全部误诊为K线字段故障。 |
| R2 股票7项 | `push2his.eastmoney.com/api/qt/stock/kline/get` | 双源核验断裂；腾讯成功仅可支持观察，不能把另一别名/同上游当独立源。 |
| R3 元数据7项 | `push2.eastmoney.com/api/qt/stock/get` | 安全身份状态是P0；行业归属是P1。拆模型，避免单一调用失败同时吞掉两类状态；恢复后检查代码/名称/风险标签/归属时点。 |
| R4 个股资金7项 | `push2his.eastmoney.com/api/qt/stock/fflow/daykline/get` | 按用户新规则作为P2辅助，后续优先验证主筛成交额与量价指标；请求失败仍如实保留，不能从42项分母中删除。 |

## 4. 本次新增真实诊断请求

执行：**{probes['started_at']} 至 {probes['finished_at']}**，本地macOS 26.0 arm64、Python 3.12.14、AKShare 1.19.1；6项串行真实请求，不写生产行情、不更新网页。请求未设置timeout时诊断脚本补8秒，已有SDK timeout保留；每个子进程22秒上限，无诊断脚本显式重试，不改网络配置。

| 真实调用 | 时间 | 实际结果 |
| --- | --- | --- |
{probe_rows}

4次失败的共同消息为`Unable to connect to proxy`，最内层为`Remote end closed connection without response`。这定位到**未获得可供判断的HTTP响应的连接故障**，并不证明远端就是东方财富服务器。代理配置来源、出口策略、上游是否选择性断连仍待核验。当前常见代理环境变量均未设置；不能因此推断Requests没有使用系统代理或运行环境网络转发。

指数SDK的`request_with_retry`自身已做3次HTTP尝试与指数退避；旧采集器又整体重试2次并用新子进程重建SDK缓存，放大无效映射请求。其余接口并不统一使用该机制。因此应统一计数与总时间预算，不能简单再叠一层重试，也不能宣称SDK完全没有退避。

这是**诊断样本2成功/6调用**，不是第三阶段完整42项通过率。原第二阶段18/42（42.86%）仍保留，不用新增样本替换、不合并选择性样本。详情：{link('真实探测证据', 'docs/stage3-1/representative-live-probes.json')}；复现脚本：{link('diagnostic_probe.py', 'docs/stage3-1/diagnostic_probe.py')}。没有模拟行情参与这6项请求。

## 5. 连接恢复之外的架构风险

| 已确认缺口 | 代码/实际证据 | 后续修复要求 |
| --- | --- | --- |
| 错误链被截断 | worker只输出异常类名；非零子进程stderr未保留 | 结构化、脱敏异常链，记录接口/阶段/请求主机路径/HTTP状态/每次时间；不泄露代理认证、查询令牌或账户 |
| 缺统一网络预算与限流 | 外层立即重试；3并发；SDK内层策略不一 | 一个总预算管理层，统计实际HTTP尝试；指数退避+jitter、源级限流、熔断、Retry-After；403不盲重试，429有限等待 |
| 源切换只覆盖部分路径 | 指数Sina为空才调用Tencent；非空但无效不触发；股票元数据单源 | 源候选必须先通过同样完整性检验才参与排序/切换；两个合格独立源仍为关键价格门禁 |
| 文件归档不等于请求缓存 | private/archive只有单日原始快照，无请求键/TTL/缓存命中策略 | 缓存键包含代码+市场+类型+日期+频率+单位+复权+schema；旧数据不改时间、不生成新许可 |
| 时间语义混合 | collected_at为请求/批次采集时刻；metadata.effective_date直接指定为预期交易日 | 分离请求起止、上游行情时刻、交易日、数据可用时刻、分类有效区间；取不到上游timestamp标null，不伪造成实时 |
| 成交额丢失 | 腾讯真实返回amount和turnover；normalize只保留date/OHLC/volume | 按SDK已转换后的股/元保留amount；换手率保留小数与单位描述，不能重复×100/×10000 |
| 交叉检查不完整 | 只比近20日OHLC和日期，偏差0.1%；不比身份、量额、时点、复权标签 | 按用户七项检查扩展；缺项单列，价格冲突继续阻断；同源raw/qfq不能算两个独立价格源 |
| 行业测试覆盖空洞 | 7个meta未验证，industries为空，sector任务0个 | 固定预期依赖矩阵；每股归属未知须显式blocked_dependency；行业任务未执行不算成功，也不凭空猜行业 |
| 分类、表现、资金混在一起 | sector_analysis把flow_errors合并价格errors | 三模型独立质量、时点、来源；行业主要按RS/成交额/RVOL/量价结构，资金仅辅助 |
| 主力流目前是硬门禁 | 个股funds<14或缺失必阻断；行业主线亦依赖资金 | 新策略版本改P2辅助，主筛必需流动性指标列P1；保留旧策略/原测试语义作为回归对照 |
| 公司行为需要进一步证据 | 601001因子偏差3.4056%、000983为1.4950%、002602为0.6557%，超过原0.5%阈值 | 保留阻断；这是算法“因子变化”标签，不是已查证分红/拆股事实。取得时点因子与事件证据前，不提高阈值消除失败 |
| 工作流绿色不代表真实验收 | build生成阻断结果仍退出0；collect只执行pytest+build，无固定在线断言 | 单独在线质量验收任务和退出状态；失败也保存脱敏诊断（always），保留安全阻断网页；先核验实际Actions日志 |

行业链路的实际依赖如下，断点发生在分类阶段：

```mermaid
flowchart LR
  M[股票元数据7项 ProxyError] --> C[行业归属全部未知]
  C --> T[行业行情和资金任务未触发]
  T --> S[行业评分缺失]
  P[东方财富价格失败] --> X[10组双源核验未通过]
  S --> G[研究许可0 买入许可0]
  X --> G
```

行业历史SDK还使用`http://7.push2his.eastmoney.com`，行业资金有分类映射预请求；这些路径本次未运行，不属于24项失败，后续必须独立检查HTTP状态/协议和分类依赖，不能直接认定也失败或已修好。

## 6. 拟实施的数据质量与行业模型

以下均为待确认后的设计，不是已落地功能。**实施优先级“最高/高/次级”与质量等级P0/P1/P2是两个维度。**

| 等级 | 拟纳入数据 | 失效处理 |
| --- | --- | --- |
| P0安全关键 | 有效交易日历；股票/指数身份及市场；股票风险/上市状态；有效同日期同口径价格、时间戳、复权/公司行为及独立源价格核验 | 市场P0失败全局禁止新开仓；个股P0失败禁止该股；研究/模拟入口均独立检查。公开真实buy_permission持续false |
| P1策略必需 | MA/MACD所需连续窗口、成交量/额、行业归属/表现、行业相对强度/RVOL、买点依赖数据 | 禁止依赖此指标的策略产生研究买入许可；不能用另一策略或缓存绕过相同缺项 |
| P2辅助 | 第三方个股/行业主力流及其验证结论 | null+缺失原因+时点/来源+覆盖率；取消相关可得奖励分、降低可得分上限与置信度；不得将数据写成0或补满分 |

统一适配层建议返回`instrument_id`（证券类型+交易所+六位代码）、source、schema、trade_date、quote_timestamp及精度、fetched_at/available_at、OHLC、volume_shares、amount_cny、adjustment_basis与时点因子、各字段质量、异常与缓存状态。日线只有日期时明确“日期精度/非实时”，不能以fetched_at冒充上游报价时刻；只有该策略允许的时间精度经过验证才算有效。

数据源候选先以真实可用性、字段完整性和独立性验证，再配置优先级。当前成功的新浪指数/腾讯股票可优先用于观察，但未通过双源核验不能用于许可；东方财富恢复后可作独立比较。证券安全元数据备用来源和行业分类来源须先实际测试，不在计划里宣称已可用。失败403不绕过限制，429按明确预算处理；禁止关闭TLS校验、绕过代理访问策略或轮换身份伪装。

缓存可用于展示已标注日期/延迟的观察值；用于策略时必须有真实available_at并在原有效期内，通过同样P0/P1验证。跨源不静默拼接缺口；保存完整候选序列和选择理由。旧缓存不刷新成当前时间，演示数据独立命名空间，Actions只上传白名单公开分析和脱敏日志。

行业分类模型保存规范industry_id、分类标准/版本、成员与有效区间、平台代码到规范ID的显式映射。行业表现模型保存同一成员口径的价格指数、成交额/成交量与覆盖率；行业资金模型单独保存第三方定义、日期、单位与覆盖率。不把名称相近的不同粒度板块直接等同，也不将平台资金数据与另一套成员指数直接混算。无法证明映射一致时，该行业策略P1阻断；自建行业篮子必须有足够同口径成员及覆盖率，7只自选不能冒充全行业。

主筛指标定义建议：行业5/20日收益相对同区间市场基准；真实成交额及其历史排名/均值比；RVOL=当日量÷前20个完整交易日平均量；量价结构比较当期/历史的价格位置、突破回踩及量额变化。量价指标来自真实日线，不称为“真实主力买入”。第三方主力净流不再决定主线和买点的成立。

五维100分仍保持行业、资金/流动性、趋势、买点、账户适配。拟将20分资金维拆成15分真实量额结构+5分辅助验证，其他维度保持20/25/20/15；最终细分规则和阈值必须随新策略版本明确记录。P2缺失时辅助子分null，不把权重重分配给已有指标，不缩小100分分母；界面显示已证实得分、缺失5分、可得上限95/100和覆盖率。P1缺失则策略总分不可用于许可。旧策略保留并继续跑全部103项测试，新版本新增独立验证，不改旧断言来制造通过。

价格跨源0.1%、因子稳定0.5%、连续窗口、异常价格/日期拦截及资金风险门禁在此次诊断中完全不改。成交量/额偏差阈值必须在同单位、同交易日、同统计范围下用真实证据制定，不能为通过率调宽；异常数值、负额、身份错配、未核验单位都必须标错。

## 7. 修复优先级与实施顺序

| 顺序 | 待确认后的工作包 | 具体交付与验收退出条件 |
| --- | --- | --- |
| 3.2a 最高 | 补齐远程和网络证据 | 对齐仓库commit、Actions run/job/日志与失败报告；加入脱敏完整错误链、请求阶段、实际HTTP尝试和状态。比较本地/macOS与Ubuntu出口，先证实代理/出口/上游责任点；未恢复仍如实失败 |
| 3.2b 最高 | P0数据源与统一适配层 | 代码/市场/证券类型明确；R1映射依赖、R2日线、R3安全状态优先；统一超时预算、有限重试、退避、限流/熔断、缓存、字段单位/复权。缺任意关键验证不能新开仓 |
| 3.3 高 | 七项交叉验证与P0/P1/P2 | 同日、同单位、同复权、有效时戳/身份、合理价格；多源冲突警告及阻断；P1策略依赖清单、P2缺失展示；不降低原硬门禁 |
| 3.4 高 | 行业与资金解耦 | 规范分类/时点成员映射；表现、资金独立；RS、额、RVOL、量价主筛；R4继续真实测试但作为辅助；新策略版本验证，原103项不删不跳 |
| 3.5 验收 | 固定真实矩阵+本地/Actions对照 | 运行原功能套件、新质量套件、全部原42项真实用例及新增行业/源测试；显式fail/blocked_dependency/pending_session；分别报告七类指标和异常拦截，不以任务成功替代质量成功 |

每个重要工作包完成都交付实际测试、风险与下一步，并按用户要求暂停确认。首次获准的范围应以3.2为限；不一次性推进全部阶段，也不启用正式发布、真实下单或公开私人数据。

## 8. 基线测试、实际质量状态及后续验收口径

原功能套件实际执行命令：

```text
PYTHONDONTWRITEBYTECODE=1 ../../work/.venv/bin/python -m pytest -q -p no:cacheprovider --junitxml=docs/stage3-1/functional-baseline.xml
```

**103/103通过，100%，0失败、0错误、0跳过，6.94秒。** 这是离线功能回归，不是在线行情通过率。日志：{link('功能回归日志', 'docs/stage3-1/functional-baseline.log')}；{link('JUnit结果', 'docs/stage3-1/functional-baseline.xml')}。18个受保护文件SHA-256对比全部一致，包含全部ashare代码、原两份测试、两份工作流和依赖配置：{link('完整性与拦截核验', 'docs/stage3-1/baseline-verification.json')}。

| 指标 | 现有实际证据 | 重新验收必须采用的口径 |
| --- | --- | --- |
| 功能测试通过率 | 本次103/103=100%，0跳过 | 原103项+新增用例分组报告；合成夹具只用于功能测试 |
| 全部真实接口请求通过率 | 第二阶段18/42=42.86%；本次仅另做6项诊断，未全量复测 | 冻结原42项，一项不删；新增行业/备用源单列。通过需契约和时点质量通过，另报纯传输成功率 |
| P0核心接口/数据可用率 | 尚未正式分级。按拟议日历+6指数请求+14股票raw请求+7qfq+7元数据，共35请求，取回数据18/35=51.43%；这是追溯统计，非P0验收通过率。关键价格对象双源0/10、证券状态0/7 | 同时报端点请求率与对象级完整P0率，不能把单源成功当可开仓；固定分母、独立元数据字段核验 |
| 行业指标可用率 | 已核验股票归属0/7；行业任务未触发，真实RS/量额/RVOL结果未建立 | 分类覆盖7股、表现覆盖预期规范行业、完整主筛指标分别报告；未知行业分母待确定，不能0/0=100% |
| 资金指标可用率 | 第三方个股主力流0/7；行业流未运行。腾讯7/7原日线请求含amount，但归一化均未保存成交额，不能称量额主筛已可用 | 分开报P1量额/结构与P2第三方个股/行业流；缺失null、不填0、不补满分 |
| 交叉验证通过率 | 第二阶段价格对象0/10；10组均unverified、每组仅1个有效源 | 七项检查逐项状态+对象整体通过；因子raw/qfq同源检查不计为独立源成功 |
| 异常买入拦截 | 本次读取实际旧行情快照，在原采集时刻与当前诊断时刻重新分析，7/7禁止研究及买入，市场UNKNOWN；无真实订单 | P0缺失、P1缺失、P2降级、跨源冲突、过期/未来时刻、单位错误等分别验证；离线故障注入明确标测试，不冒充在线异常 |

这次档案分析不更新公开网页，不是重新获取的实时行情，不代表现在已具备交易许可。只有一个真实日期快照，无法验证真实历史回测表现。

**待交易时段复测：** 盘中数据时间戳与实际延迟、交易日收盘后最新日线发布/日期推进、限流在真实更新窗口的表现、停牌和公司行为时点覆盖、跨下一交易日的真实T+1模拟。周日历史查询成功可以作为历史接口证据，不能把上述功能计为通过。尚无Actions实测指标，不能把本地成功率照搬为云端成功率。

固定矩阵中保留PASS/FAIL/BLOCKED_DEPENDENCY/PENDING_SESSION及每项理由，pending不得计入通过；同时报告pass/预期全部、pass/已执行和待复测数量。原42项接口没有通过的新轮运行证据前，不能宣称修复通过率已提高。免费上游或云端网络若仍不可用，安全结果就是持续禁止开仓。

## 9. 参考口径与暂停点

接口签名、腾讯日线股/元单位与复权语义核对了[AKShare官方股票文档](https://akshare.akfamily.xyz/data/stock/stock.html)及本地1.19.1源码；指数依赖核对了[AKShare官方指数文档](https://akshare.akfamily.xyz/data/index/index.html)及index_zh_em.py。异常结构核对了[Requests官方异常源码](https://requests.readthedocs.io/en/latest/_modules/requests/exceptions/)。网络责任判断以本次实际异常链为证据，不以文档推断服务可用。

**本地24项逐项诊断、优先级和实施计划已交付；精确网络责任归因及GitHub Actions日志审计仍待补充证据。** 当前暂停，等待用户确认后才修改核心行情架构。需补充现有仓库URL及对应Actions运行URL/导出日志，以完成远程证据闭环；如仓库尚未建立，也请明确说明，不会自行重建项目或发布网站。仍禁止实盘开仓、自动真实下单和未经确认的公开发布。
"""
    (OUT / 'report.md').write_text(report, encoding='utf-8')
    print(json.dumps({'cases': len(cases), 'report': str(OUT / 'report.md'), 'protected_files_unchanged': audit['protected_files_unchanged']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
