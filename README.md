# 5000元A股智能选股系统 V3.1

**第二阶段本地研究与模拟版本；第三阶段3.1诊断已完成，核心行情重构待确认。网站未发布。**

已实现五维100分规则、行业资金适配、时点快照回测和本地模拟账本。真实接口取得7只股票及三指数日线，但独立来源、证券状态、行业和资金链路尚未完整通过，当前真实研究许可为0。合成演示验证流程，不代表真实策略收益。

无券商接口、真实下单、付费API或账户上传。公开 `research_permission` 仅表示研究条件；`buy_permission` 始终为false。本地模拟另外核验现金、持仓、T+1、执行价格和质量。

## 快速启动

建议Python 3.12，在项目目录运行。文件锁支持macOS/Linux；Windows请用WSL。

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pytest -q
python -m ashare.cli build --mode demo --output site/data/demo.json
python -m http.server 8765 --bind 127.0.0.1 --directory site
```

浏览器打开 http://127.0.0.1:8765/ 。默认真实采集，手动选择合成演示。页面“读取”按钮重读JSON，不触发Python采集。不能双击HTML代替HTTP服务器。

```sh
python -m ashare.cli build --mode live --timeout 18 --archive
# 可选自定义观察池，最多100只符合主板前缀的不同代码
python -m ashare.cli build --mode live --codes 601001,000983 --archive
```

每接口进程级超时、最多重试一次、最多3个并发请求（行业2个）。成功返回不等于数据合格；失败输出新阻断快照，不保留旧许可，不切换演示。退出0只代表报告已生成。

公开结果 `site/data/analysis.json`，接口报告 `docs/interface-report.json`；`--archive` 原始快照留在 `private/archive/`。采集超36小时或到下一交易日16:00（较早者）失效。跨周末/长假旧信号通常过期，需下一次开盘前重新采集完整日线，不默认延长许可。

## 五维评分与许可

| 维度 | 满分 | 规则 |
| --- | --- | --- |
| 行业 | 20 | 收盘>MA20得8；5日上涨得6；强于上证得6 |
| 资金 | 20 | 个股当日净流入正得6；5日合计正得8；当日净占比正得6 |
| 趋势 | 25 | 收盘>MA20得10；MA5≥MA10≥MA20得10；MACD柱正得5 |
| 买点 | 20 | 趋势回踩或突破回踩候选符合得20 |
| 模型账户适配 | 15 | 整手、2500元预算、含成本计划风险≤50元得15 |

缺失或异常维度为null，总分也为null，仅显示技术小计/60。热点阶段为启动、延续、过热、分化、退潮；只比较自选股所属行业，不能称为全市场资金排名。

研究条件同时要求live、市场A、行业主线（≥14、5日至少3日正流入、启动/延续）、资金≥14、趋势≥20、候选、至少100股风险测算、总分≥80与所有质量/时效通过。演示始终无真实研究许可。规则仍是实验规则，尚无真实长样本效果验证。

## 本地模拟与交易记录

```sh
python -m ashare.cli paper-init
python -m ashare.cli paper-status
python -m ashare.cli paper-run --signal-raw private/signal.json --execution-raw private/execution.json
python -m ashare.cli export-trades
```

路径示例替换为相邻交易日的原始快照。执行快照通常收盘后取得，用于事后审计模拟成交；系统不会在现实开盘时自动交易。信号必须在执行日09:30前采集，且仅含当时已结束的行情和可知元数据。

初始5000元空仓、最多2只、不加仓、100股整数倍。逐笔扣现金；下一交易日开盘含滑点重算预算和50元风险。开盘跳涨>2%、涨停开盘、一字行情、行情缺失或公司行为未通过拒绝买入。卖出检查T+1；跌停/一字/无法确认成交价格则保留持仓。前日确认C或10交易日到期先在开盘退出，其他持仓按止损/跳空模型处理。

默认 `private/paper-ledger.json` 与 `private/trades.csv`。文件锁、原子替换、金额按分、现金/股数守恒；损坏报错不重置，同日推进不重复成交。真实和演示不能混入同一模拟账本；演示另建账本并显式增加 `--allow-synthetic`。

## 回测

```sh
# 虚构数据，只验证流程
python -m ashare.cli backtest --demo --allow-synthetic --sensitivity
# 足够连续时点档案积累后
python -m ashare.cli backtest --archive private/archive --sensitivity
# 或用户自备符合 replay-2 契约的时点数据集
python -m ashare.cli backtest --input private/dataset.json
```

输出强制留在 `private/`，默认 `private/backtest.json`。报告含模拟成交、权益、费用、胜率、样本净变化与回撤、10/20/30基点滑点敏感性。网页本地选文件用File API读取，不上传；公开 `replay-demo.json` 只有虚构演示，不含期末账本。

至少2个不同且连续交易日的快照；拒绝未来行情/元数据/资金、晚到信号与交易日缺口。归档只选下一交易日开盘前已采集结果。今天下载的历史价格不能补造过去可知的行业资金。当前只有一个真实目标交易日归档，**不能输出真实历史收益结论**。

支持2022-04-29之后的费用模型，印花税按2023-08-28分段。公司行为未实现分红/股数记账，事件或因子变化保守拒绝。持仓缺报价或执行质量不足时收益/回撤留空；开放持仓收盘估值，不强制平仓、不预扣未来卖出费。日线无法证明盘口与排队成交，仍是模拟模型。

## 费用假设与风险

佣金万分之三、单边最低5元、双边过户费0.001%、双边默认0.1%不利滑点，均为 `ashare/config.py` 模型参数，需按实际收费核验。卖出印花税2023-08-28前0.1%、之后0.05%。50元是计划风险；T+1、停牌、跳空和跌停可能使实际损失更大，测试覆盖此情况。

参考[上交所2026交易规则3.3.8/3.1.4](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)、[深交所2026交易规则](https://docs.static.szse.cn/www/lawrules/rule/trade/W020260424690713155663.pdf)、[财政部税务总局2023年第39号公告](https://shanxi.chinatax.gov.cn/web/detail/sx-11400-545-1780448)。成熟非ST主板按10%限价模拟；特殊IPO窗口、公司行为和异常状态不作可交易样本。

## 目录与验收

| 路径 | 用途 |
| --- | --- |
| `ashare/provider.py`、`worker.py` | 免费采集、单位标准化、独立来源检查 |
| `ashare/engine.py`、`stage1.py` | 指标、评分、条件与风险 |
| `ashare/paper.py`、`replay.py`、`storage.py` | 模拟执行、时点回放与本地账本 |
| `ashare/fixtures.py` | 明确标记的虚构测试数据 |
| `ashare/cli.py` | 采集/归档/模拟/回测/CSV命令 |
| `site/` | 唯一可发布目录；公开研究及虚构演示 |
| `private/` | 原始档案、账本与回测报告；忽略且交付包排除 |
| `docs/architecture.md` | 规则、格式与实现边界 |
| `docs/stage2-report.md` | 本阶段实测、测试、限制及下一步 |
| `docs/interface-report-stage2.json` | 真实接口证据 |
| `docs/stage1-report.md` | 保留的第一阶段历史报告 |

## 免费部署与阶段审批

[公开仓库的GitHub标准运行器免费](https://docs.github.com/en/actions/concepts/billing-and-usage)，[GitHub Free公开仓库支持Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)。免费上游可能限流或失效，Actions网络仍需实测。

CI和collect均仅允许手动触发；首次Push不会自动启动测试、采集或部署。CI只做离线测试/演示回放；手动collect只上传 `site/` 和公开接口报告。无定时触发、无Pages写权限；停用模板 `docs/pages-workflow.yml.disabled` 不能自动运行。未来采集可设北京时间16:17，真实交易日历再过滤；[定时可能延迟且60天无活动可停用](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)，不是实时交易时钟。

用户已创建空的公开仓库 [caiyiguo709-crypto/a-share-v31](https://github.com/caiyiguo709-crypto/a-share-v31)，当前本地项目已设置该仓库为origin。**首次Push尚未执行，须通过安全检查并取得用户确认；没有启用定时或发布网站，Pages发布需要另行明确确认。** 第三阶段诊断成果保留在本地 `docs/stage3-1/`；原诊断源脚本纳入Git，但含本机环境/路径信息的报告、数据及 `docs/github-link-audit/` 仅留本地。不得上传 `private/`、真实账户、持仓或个人信息；网页无外部脚本或遥测。
