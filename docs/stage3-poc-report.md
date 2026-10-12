# V3.1 最小备用源 PoC：本地实施与验收记录

时间：2026-10-12，北京时间。总体状态：**PARTIAL**。

用户批准开始实施后，按技术审计建议实施独立备用源 PoC，保留 V3.1 核心。完成本地代码、离线回归、真实网络取数及证据保存；云端与连续交易日验收尚未完成。此次不构成从 V3.1 迁移到 Daily Stock Analysis 的架构决定。

## 1. 完成内容与修改清单

在本地隔离目录复制公开仓库，分支 `poc/free-backup-sources`，基准提交 `63befb3168a129e910ae73979c97676a0b29286b`。只新增：

| 文件 | 用途 |
|---|---|
| `poc/backup_sources.py` | 新浪原始不复权日线、腾讯固定版本 SDK 日线的独立观察工具 |
| `poc/README.md` | 单位、数据处理、隐私、失败与运行契约 |
| `tests/test_backup_poc.py` | 38 项离线故障注入及真实故障回归 |
| `docs/stage3-poc-report.md` | 本报告 |

原有所有 Git 跟踪文件逐字节比对 HEAD，均未改变。正式 `ashare/`、原测试、依赖文件、网页、工作流和 synced sources 均未修改。新工具输出限制在 `private/`，拒绝目录穿越和符号链接逃逸，禁止研究与买入许可。

## 2. 实际验收矩阵

不同类别不混算成功率。表内只列实际数量，其余状态为 0，待执行项目另列。

| 类别 | PASS | FAIL | BLOCKED_DEPENDENCY | PENDING_SESSION | NOT_RUN | 结论 |
|---|---:|---:|---:|---:|---:|---|
| A 原有功能测试（修改前） | 103 | 0 | 0 | 0 | 0 | 基线保存 |
| A 原有功能测试（修改后） | 103 | 0 | 0 | 0 | 0 | 原测试全部保留 |
| B 新增离线测试（最终） | 38 | 0 | 0 | 0 | 0 | 与原测试合计 141 项通过 |
| C 原固定 42 项真实接口复测 | 19 | 23 | 0 | 0 | 0 | 原网络问题仍复现 |
| C 新 PoC 真实请求（最终 r3） | 15 | 0 | 0 | 0 | 0 | 日历 1、股票日线 14；仅本次本地请求 |
| D 本次股票价格双源核验 | 7 | 0 | 0 | 0 | 0 | 两源未复权 OHLC，复用原校验 |
| D 本次单位合理性检查 | 14 | 0 | 0 | 0 | 0 | 不是单位供应链认证 |
| D 五个不同交易日持续采样 | 0 | 0 | 0 | 1 | 0 | 本日多次重跑不算多日样本 |
| E 交易安全与故障拦截 | — | — | 0 | 0 | 0 | 包含在 A/B，不能重复计数；许可始终 false |
| F GitHub Actions 真实运行 | 0 | 0 | 0 | 0 | 1 | 未新增工作流、未触发云端 |

新增测试覆盖空数据、过期、未来日期、重复、缺口、NaN、零量、价格冲突、同源伪装、证券身份不一致、复权口径、模拟数据、请求失败、无效/过旧采集时间、日历缺失、进程超时、金额缺失/异常、单位错误、公开目录及符号链接输出阻断。离线样本绝不计为真实请求。

真实成功窗口：2026-10-12 03:42:43—03:42:55。预期最近完整日线为 2026-10-09；不是 10 月 12 日盘中实时行情。七只股票每源 145 根日线，2026-03-06—2026-10-09；近 20 日 OHLC 最大两源偏差为 0。新浪/腾讯是两个实际服务路径，不承诺底层行情供应链完全独立。

## 3. 实际故障与修复过程

**原系统复测：**原固定 42 项接口在 03:34:45—03:36:26 执行，19 成功、23 失败。失败项含 ProxyError、MaxRetryError、RemoteDisconnected；证券信息 7/7 和资金流 7/7 失败。日历和三项新浪指数成功，腾讯七只原始及七只前复权请求成功，东方财富仅一只原始日线成功。未更换代理、关闭 TLS、改重试配置或删除失败项。相对旧 18/42 的单次增加不能证明稳定性改善。

**新浪响应解析：**第一次真实 PoC 中新浪七项失败、腾讯七项成功，退出码 2。发现响应变量实际为 `KLC_K2_证券代码`；增加回归，修复前 1 failed/29 passed，修正后测试通过。保留第一次记录。没有把失败包装成行情源通过。

**腾讯成交量单位：**第二次价格核验七组通过，但后续检查发现两个 `000` 股票存在 100 倍单位错误。第二次旧退出码只检查价格，不能用于完整质量验收；其腾讯单位复查结果为 5 PASS、2 FAIL。现已加入独立单位状态和退出码检查。

| 股票 | 2026-10-09 SDK 原成交量 | PoC 转为股后 | 新浪成交量（股） | 腾讯成交额（元） |
|---|---:|---:|---:|---:|
| 000983 | 740,894 | 74,089,400 | 74,089,403 | 490,175,700 |
| 000066 | 803,718 | 80,371,800 | 80,371,762 | 1,100,909,300 |

固定 AKShare 1.19.1 的 `stock_feature/stock_hist_tx.py` 第 113 行用 `sz000` 前缀排除成交量转换，把这些股票误当作指数。SDK 原值保留；独立 PoC 根据明确的主板股票身份纠正固定版本单位，记录乘数，不根据行情拟合转换。对应回归修复前 2 failed/36 passed；修复后全部 38 项通过，真实 r3 七组价格和 14 组单位合理性检查通过。

SDK 文件 SHA256：`aa9199665d356ba3a054297004e349d3926fa83026abfd2d50c4b7f832226d84`。正式 V3.1 使用同一 SDK 的路径也可能受此缺陷影响；本次没有改正式接口，须单独批准并验证后接入。

## 4. 证据、命令与退出状态

执行环境：Python 3.12.14、AKShare 1.19.1，复用既有环境；未安装新依赖。下列 `python` 为该既有环境的解释器。命令均从项目根目录执行，前置 `PYTHONDONTWRITEBYTECODE=1`。

| 实际命令 | 退出码 | 本地证据 |
|---|---:|---|
| `python -m pytest -q -p no:cacheprovider --junitxml=private/poc/baseline.xml` | 0 | `private/poc/baseline.log`、`baseline.xml`，103 passed |
| `python -m pytest -q -p no:cacheprovider tests/test_backup_poc.py`（工具未实现） | 2 | `private/poc/new-tests-before.log`，缺模块 |
| `python docs/stage3-2a/network_probe.py --output poc-original42-20261012.json` | 0 | `docs/stage3-2a/poc-original42-20261012.json`、`private/poc/original42.log`；退出 0 仅表示诊断报告生成 |
| `python -m poc.backup_sources --output private/poc/backup-sources-20261012.json` | 2 | 同名 JSON、`.log`，首轮新浪失败 |
| `python -m pytest -q -p no:cacheprovider tests/test_backup_poc.py`（新浪回归修复前） | 1 | `private/poc/sina-regression-before.log` |
| `python -m poc.backup_sources --output private/poc/backup-sources-20261012-r2.json` | 0 | 同名 JSON、`.log`；旧价格范围退出码，单位有两项错误 |
| `python -m pytest -q -p no:cacheprovider tests/test_backup_poc.py`（单位回归修复前） | 1 | `private/poc/units-regression-before.log`，2 failed/36 passed |
| `python -m pytest -q -p no:cacheprovider --junitxml=private/poc/regression.xml` | 0 | `private/poc/regression.log`、`regression.xml`，141 passed in 5.07s |
| `python -m poc.backup_sources --output private/poc/backup-sources-20261012-r3.json` | 0 | 同名 JSON、`.log`；价格和单位检查同时通过 |

`private/poc/integrity.json` 保存原文件比对、分支、远端 main 和 SDK 源码摘要。异常链只保存类名；诊断证据被忽略，不上传公共仓库。源码与测试未提交，可逐文件查看新增差异；没有新 Commit SHA。

## 5. 安全与未解决项

5000 元本金、最多两只、100 股、T+1、含费用单笔 50 元风险预算和 P0/P1/P2 门禁的正式代码均未修改。P0 缺失/过期/冲突阻断新开仓；P1 缺失阻断依赖策略；P2 缺失须显示并减分。PoC 始终拒绝研究和买入许可，当前不能用于买入决策。

尚未完成证券状态、除权除息、行业、资金流、两源指数契约、五日连续观察、云端运行与历史回测。日期和采集时钟检查通过不代表上述信息通过。腾讯 SDK 内部排序/去重未被原始响应审计覆盖；单位诊断只检查金额/量与当日价格范围，不能取代正式流动性验收。备用源均通过同一 SDK 环境及本机网络，仍有共享软件和网络故障风险。

## 6. GitHub 状态与下一步审批

远端 main 仍为基准提交；未 commit、push、合并、启用 Actions/Pages/通知/付费接口/下单/定时任务。现阶段仅本地可审阅。

建议下一步：先审阅本报告和新增源码，再批准手动云端诊断方案；方案须只产生私有诊断、无 Secrets、无公开行情/持仓、无定时触发，测试失败必须让运行失败。首次公开 push 须另行明确批准。随后收集五个不同交易日样本，再决定是否把备用适配器与腾讯单位修正接入正式核心。不能因本次七组价格通过就切换主数据源。

暂停点依据仓库 `AGENTS.md` 第七节：“首次公开 Git Push”“修改核心行情数据架构”“更换主要行情提供方”等需用户确认。因此本次不跨入这些动作。
