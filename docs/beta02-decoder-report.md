# V3.1 Beta 0.2 新浪解码修复与云端复验准备

日期：2026-10-12，北京时间。阶段状态：**PARTIAL**。本地修复、隔离兼容性验证、179 项测试及七只股票的真实双源验收通过；Beta 0.2 尚未公开推送、尚未执行云端复验。停在用户指定的审批点。

## 1. 开发基线与读取记录

基线 Commit：`7a2f5bd1348a577c951da4de800d5a0574e236e3`。
工作分支：`poc/free-backup-sources`。开始时 Git 工作区干净。未创建新提交，当前 HEAD 仍为基线。

完整读取 AGENTS.md、docs/beta01-cloud-plan.md、poc/README.md、本项目本地 `implementation/beta01-acceptance.md`，并重新通过 GitHub 读取 [历史运行 38191565222](https://github.com/caiyiguo709-crypto/a-share-v31/actions/runs/38191565222) 的 Job 114625234278 与实际日志。历史结论为 failure：103/38/11 项测试通过、真实请求 11 成功/10 失败，其中新浪股票七项 HTTP 200 后 decode 阶段 TypeError。历史结果仅作故障证据，不冒充本次验收。

修改前重新执行 152 项基线：**152 passed in 5.21s**，退出码 0，保存 `private/beta02/baseline-152.log` 与 XML。原 152 项测试文件和断言均未改动。

## 2. 根因复现与确认范围

确认的缺陷：PoC 使用 `with module.py_mini_racer.MiniRacer()`，错误假设 AKShare 实际导入的所有 MiniRacer 都实现上下文管理协议。旧版类没有 `__enter__` / `__exit__`，在进入解码前就会失败。

本次在独立 Python 3.12.14 venv 中，使用官方 PyPI 的历史 `akshare==1.19.1`、`py-mini-racer==0.6.0`、`akracer==0.0.14` wheel 的隔离副本；其余现有依赖只读复用。没有向项目环境安装新依赖、修改锁文件或更新 SDK。AKShare 新浪模块与当前本机 SDK 源文件逐字节一致，SHA256 为 `aa60721af3f7bdec29e1e5f59ff6770b54fc919fc6867706d16d36b3a7ee07a2`。

隔离 venv 为 macOS arm64，不是 Ubuntu。它验证实际旧版 V8 解码器与同版本 Python 的兼容性缺陷；Ubuntu 重验须等批准。没有使用 Mock 类证明根因。

| 检查项 | 旧版隔离环境 | 现代本机环境 |
|---|---|---|
| SDK 解码器导入 | AKShare 源码实际 `import py_mini_racer` | 相同 |
| 实际类模块 | `py_mini_racer.py_mini_racer` | `py_mini_racer._mini_racer` |
| 实际类源文件 | 隔离 `akracer-isolated/py_mini_racer/py_mini_racer.py` | `site-packages/py_mini_racer/_mini_racer.py` |
| `__enter__` / `__exit__` | 无 | 有 |
| `close` 方法 | 无；类使用 `__del__` 释放 V8 上下文 | 有 |
| 元数据版本 | AKShare 1.19.1、mini-racer 0.14.1、py-mini-racer 0.6.0、akracer 0.0.14 | AKShare 1.19.1、mini-racer 0.14.1；其余两包未安装 |

完整实际导入路径和类来源记录在私有 `isolated-imports.json`，不会作为绝对机器路径公开输出。新诊断元数据只输出模块名、源文件名、协议能力及分发包版本。

历史安装日志明确三个解码器分发包共存。官方 wheel 中不仅 mini-racer 与 py-mini-racer，共用命名空间的 akracer 也提供 `py_mini_racer/__init__.py` 和旧版类；不能只检查 `mini-racer==0.14.1` 的分发包版本，就推断实际导入类一定是现代实现。历史日志的安装列表包含最后的 akracer 覆盖风险。

准确复现消息（来自本次隔离运行）：

```text
TypeError: 'MiniRacer' object does not support the context manager protocol
```

先运行实际旧类初始化和原 with 语句，退出码 1，保存 `context-reproduction.log`。再在同一隔离 SDK 环境执行**未修复的 PoC**，2026-10-12 09:41:36 请求新浪 601001：HTTP 200、真实证券响应 61,518 字符，逻辑状态 FAIL、failure_stage=decode、error_chain=[TypeError]。证据 `live-before-fix.json`。这是新请求，不是 Beta 0.1 缓存。

随后对这一份响应做明确标为 **OFFLINE_REPLAY** 的修复前后比较：原 with 语句仍 TypeError；修复后实际旧解码器返回 4,745 条原始记录，目标窗口 145 条。响应 SHA256：`8509e7f934a5dde463331724d7c5a05f732bf094032b1729556b8081a8d69859`。回放只用于控制变量证明，不计入真实接口通过数量。

历史云端日志没有保存当时实际模块路径和完整 TypeError 消息，因此不能事后直接测量它当时究竟加载了哪一个覆盖后的文件；确认的是可实际复现的上下文协议不兼容缺陷，隔离 PoC 与历史失败类型/阶段一致。新云端诊断会记录实际类来源，补齐这一证据缺口。

## 3. 最小修复与修改清单

保留 AKShare 原 `hk_js_decode` 和 `d` 调用，保留原始响应、身份验证及固定 SDK 版本门槛。改为普通实例初始化与 try/finally：现代类显式 close；无 close 的旧类按其原对象析构方式释放。没有切换 SDK 解码器或引入第三方新依赖。

新增非空记录列表、字段齐全及日期合法性检查：无效日期、日期尾部垃圾字符、未来日期不能因窗口过滤而消失；正常的当天未收盘记录可排除。没有填补、排序、去重或改写原始历史数据。数值、交易日缺口、日期排序、OHLC 双源与单位质量沿用既有检查。价格偏差阈值仍为 0.1%。

| 文件 | 本次变化 |
|---|---|
| `poc/backup_sources.py` | 解码器生命周期兼容、加载元数据、结果和日期/字段检查 |
| `tests/test_decoder_beta02.py` | 新增 27 项离线回归 |
| `poc/cloud_report.py` | 单独统计四组测试，导出脱敏解码器元数据，隔离 Beta 0.2 证据目录 |
| `.github/workflows/collect.yml` | 准备 Beta 0.2 名称、产物路径和新测试结果白名单；仍仅手动触发 |
| `.gitignore` | 忽略 Beta 0.2 脱敏产物目录 |
| `poc/README.md` | 兼容性、证据与审批说明 |
| `docs/beta02-decoder-report.md` | 本报告 |

`ashare/`、requirements.lock、原测试、腾讯取数与转换逻辑、价格阈值、评分和风险计算不改。没有改变 5000 元本金/仓位限制、100 股、T+1、单笔 50 元风险预算或 P0/P1/P2 门禁。研究与买入许可继续为 false。

## 4. 本地测试：修改前后

| 测试组 | PASS | FAIL | BLOCKED_DEPENDENCY | PENDING_SESSION | NOT_RUN | 证据/退出码 |
|---|---:|---:|---:|---:|---:|---|
| 原 152 项修改前基线 | 152 | 0 | 0 | 0 | 0 | baseline-152.log/XML，0 |
| 原 103 项功能测试 | 103 | 0 | 0 | 0 | 0 | original-103.log/XML，0 |
| 原 38 项 PoC 测试 | 38 | 0 | 0 | 0 | 0 | backup-38.log/XML，0 |
| 原 11 项云端契约测试 | 11 | 0 | 0 | 0 | 0 | cloud-11.log/XML，0 |
| Beta 0.2 新增回归 | 27 | 0 | 0 | 0 | 0 | decoder-27.log/XML，0 |
| 最终全量回归 | 179 | 0 | 0 | 0 | 0 | final-179.log/XML，179 passed in 7.66s，0 |
| Beta 0.2 云端运行 | 0 | 0 | 0 | 0 | 1 | 未触发，待用户批准 |
| 五个不同交易日持续采样 | 0 | 0 | 0 | 1 | 0 | 单日两种运行环境不算两日 |

四组测试分别执行，最终全量 XML 再核对为原 test_safety 61 + test_stage2 42、PoC 38、云端契约 11、新回归 27。云端准备中原组的 ignore 仅用来分类；每个被分类的文件均由其独立组执行，没有跳过测试。

新测试覆盖真实 SDK 加载来源/版本元数据、旧/新协议兼容、现代资源释放和异常释放、响应证券身份、解码失败、非列表/空值/错误记录类型、缺失字段、无效/未来日期、未完整交易日窗口、重复/缺口/缺失价格的权限拦截。合成故障输入只用于离线测试，不计为真实行情。

## 5. 新真实网络验收

最终代码追加日期边界回归后，重新分别发起两组真实取数。没有用旧请求、回放或缓存替代：

- 旧版隔离环境：09:52:13—09:52:24，15 个逻辑请求 **15 PASS / 0 FAIL**（日历 1、股票新浪 7、腾讯 7）。
- 现代环境：09:52:13—09:52:22，15 个逻辑请求 **15 PASS / 0 FAIL**。
- 每一环境独立报告，不把两种环境合并成连续运行稳定性成功率。
- 最近完整交易日均为 **2026-10-09**。10 月 12 日尚未收盘，当天未完整日线没有参与验收。

以下每行在**两个环境都得到相同的通过结论**；单源每只 145 根，窗口 2026-03-06—2026-10-09。

| 股票 | 新浪连接 | 新浪解码 | 新浪完整性 | 腾讯完整性 | 近20日 OHLC 最大偏差 | 双源 | 新浪/腾讯量额单位质量 | 最新有效交易日 |
|---|---|---|---|---|---:|---|---|---|
| 601001 | HTTP 200 | PASS | PASS，145 根 | PASS，145 根 | 0 | PASS | PASS / PASS | 2026-10-09 |
| 000983 | HTTP 200 | PASS | PASS，145 根 | PASS，145 根 | 0 | PASS | PASS / PASS | 2026-10-09 |
| 600267 | HTTP 200 | PASS | PASS，145 根 | PASS，145 根 | 0 | PASS | PASS / PASS | 2026-10-09 |
| 002312 | HTTP 200 | PASS | PASS，145 根 | PASS，145 根 | 0 | PASS | PASS / PASS | 2026-10-09 |
| 002602 | HTTP 200 | PASS | PASS，145 根 | PASS，145 根 | 0 | PASS | PASS / PASS | 2026-10-09 |
| 002354 | HTTP 200 | PASS | PASS，145 根 | PASS，145 根 | 0 | PASS | PASS / PASS | 2026-10-09 |
| 000066 | HTTP 200 | PASS | PASS，145 根 | PASS，145 根 | 0 | PASS | PASS / PASS | 2026-10-09 |

完整性调用原 validate_bars，使用本轮真实交易日历；包含不少于 60 根、日期有效/升序/唯一、最近完整交易日、无交易日缺口、OHLC/正量及异常跳变检查。HTTP 200 只记连接成功；必须解码、完整性、双源和单位分别通过，才能报告本轮观察质量 PASS。

单位保留既有股/人民币元契约和固定 SDK `000` 股票手转股修正；每个源分别检查金额/量与当日高低价范围。该检查是单位合理性，不是全部数据供应链认证。腾讯 SDK 内部排序/去重限制仍存在，未把它当作原始 HTTP 证据。

证据：`legacy-network-final.json/.log`、`modern-network-final.json/.log`、`live-quality-final.json`。每个源记录请求时刻、采集时刻、HTTP、解码器能力/版本、原始/归一化日线。原始证据全部保存在 Git 忽略的 private/beta02，不上传公共代码分支。

## 6. 完整失败清单与风险拦截

| 失败记录 | 是否预期/处理结果 |
|---|---|
| 旧类 Python 3.12 原 with 语句 TypeError，退出 1 | 真实隔离复现，根因证据；修复后旧类可解码 |
| 未修复 PoC 的本轮新浪 601001，HTTP 200 后 decode TypeError | 真实请求 FAIL，未计成功；保留原始证据 |
| 早期 Rosetta Python 3.9 同一无协议旧类报 AttributeError `__enter__` | 兼容性探索，不是云端同版本复现；后续用实际 Python 3.12 精确复现 TypeError |
| 新测试首次收集报 helper 不存在，退出 2 | 修复实现前的失败证据 new-tests-before.log；不是原 152 项回归失败 |
| 同响应 OFFLINE_REPLAY 的原 with 语句 TypeError | 预期控制变量失败，不计真实接口 |
| 修复后的初次两组新网络检查 | 各 15 PASS / 0 FAIL；保留 after 文件，非最终代码验收替代 |
| 最终两组新网络检查 | 各 15 PASS / 0 FAIL，无隐藏失败 |
| 最终 179 项测试 | 0 FAIL / 0 SKIP；所有原断言保留 |
| 历史东方财富三大指数连接失败与新浪指数量/额契约不足 | 本次未修复，本地股票任务不重新验收指数；云端准备保留全部旧失败接口，不删除或跳过 |

异常、身份不符、缺失、未来/重复/断档数据继续拒绝通过；每一环境七只股票研究/买入许可全部 false。旧版真实解码失败时无有效 bars，双源质量无法通过。原门禁、T+1、整手、预算与风险测试保持通过。

## 7. 安全检查与差异

待审阅完整差异保存到 `private/beta02/changes.diff`（含新增测试和本报告），文件摘要、原 152 项测试逐字节比对、核心/依赖未改和腾讯 AST 比对记录在 `safety-review.json`。`git diff --check` 及新增文件空白检查通过。

仅上述七个文件参与交付。扫描 Token、密码 URL、私钥、账号/交易文件及 private/产物路径，无真实敏感内容。所有历史 wheel、隔离环境、完整机器路径、原始响应、日志和回放都留在忽略目录。新导出解码器元数据无绝对路径。

本次没有 git add/commit/push、合并、创建 PR、配置 Secrets、启用 Pages/定时任务、自动下单或触发 Actions。项目没有新增依赖；仅提取并使用已经出现在历史云端安装记录中的版本进行隔离复现。

## 8. 云端复验计划与待确认事项

已准备可审阅代码，**不执行**以下动作直到用户确认：

1. 将七个已检查文件提交并公开推送到现有 `poc/free-backup-sources`，禁止修改 main。
2. 仅手动触发一次 `collect.yml`，Ubuntu 24.04、Python 3.12、原 requirements.lock 不变。
3. 分组新跑 103/38/11/27 项测试，再执行原 21 个真实请求：日历、七只股票新浪/腾讯、三大指数新浪/东方财富。不能删除指数失败项来提高成功率。
4. 记录云端实际解码器模块/类、分发版本、上下文和 close 能力；验证七只股票的解码、完整性、双源 OHLC、量/额、日期及禁止买入。
5. 无论失败与否，用新目录生成脱敏报告和摘要，按七个产物文件白名单保存 Artifact；失败必须非零退出，保留三天。
6. 完成一次运行后停止，不连续重跑。若指数连接/单位仍未验证，即使股票恢复，也必须维持整体 PARTIAL。

本次不提议更换或新增依赖。如果后续需要更改锁文件或引入新依赖，另行请求确认。尚有单日持续性、Linux 实际加载、指数、证券信息/除权/行业/资金流与正式核心接入的待验证项；不因本地恢复就开放交易许可。

审批依据为用户本次第七条：“涉及新的依赖、公开Push或触发GitHub Actions前，暂停请求用户确认”，并符合 AGENTS.md 第七节。**等待批准独立分支公开推送及一次手动云端复验。**

## 9. 实际执行证据索引

命令均从仓库根目录执行；python 为既有 Python 3.12.14 解释器，隔离命令使用 `private/beta02/repro-env/bin/python` 及已记录的隔离 PYTHONPATH。

| 执行 | 退出码 | 证据（private/beta02） |
|---|---:|---|
| `python -m pytest -q -p no:cacheprovider`，修改前 | 0 | baseline-152.log/XML |
| 隔离旧类实际原 with 语句 | 1 | context-reproduction.log、isolated-imports.json |
| 隔离未修复 `python -m poc.backup_sources --child`，601001 | 0（协议完成，内部行情 FAIL） | live-before-fix.json/.stderr |
| 原103 `python -m pytest tests --ignore=tests/test_backup_poc.py --ignore=tests/test_cloud_poc.py --ignore=tests/test_decoder_beta02.py` | 0 | original-103.log/XML |
| `python -m pytest tests/test_backup_poc.py` | 0 | backup-38.log/XML |
| `python -m pytest tests/test_cloud_poc.py` | 0 | cloud-11.log/XML |
| `python -m pytest tests/test_decoder_beta02.py` | 0 | decoder-27.log/XML |
| 最终 `python -m pytest -q -p no:cacheprovider` | 0 | final-179.log/XML、test-counts-final.json |
| 两环境各一次最终 `python -m poc.backup_sources --output private/beta02/<环境>-network-final.json` | 各 0 | legacy/modern-network-final.json/.log、live-quality-final.json |
| 同响应修复前后离线比较 | 0（预期旧块失败、新块成功） | same-response-comparison.json/.log |
| Git diff、保护文件/腾讯 AST、敏感扫描、证据摘要 | 0 | changes.diff、safety-review.json |

所有网络复验均实际访问新浪/腾讯；离线回归、隔离复现和回放证据单独标注，未合并为接口成功率。当前没有 Beta 0.2 Commit SHA、Actions 运行 URL 或云端 Artifact，因为相关动作尚未批准和执行。
