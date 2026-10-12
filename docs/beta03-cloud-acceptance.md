# V3.1 Beta 0.3B 云端验收准备与审批材料

**当前结论：PARTIAL；本地准备已验证，云端真实验收 NOT_RUN，等待明确批准。**

本报告不能作为云端成功证据。没有公开Push、手动Actions运行、新Commit、main合并、Pages、定时任务、通知交易执行或真实下单。

## 基线与原项目连续性

工作分支 `poc/free-backup-sources`，当前HEAD仍为 `02cc0d335a00181139dadb20594630867eff3b7c`，与指定基准一致。完整阅读原项目AGENTS.md及三个Beta03成果；三份文件SHA256与上一阶段 `private/beta03/integrity.json` 完全一致。项目继续使用原目录，没有重新创建项目。

原正式引擎、交易参数、原179项测试、旧PoC、旧云端报告程序及requirements.lock保持基准字节内容。原32项指数测试也未修改。5000元、最多2只、100股、T+1、50元风险与P0/P1/P2门禁均不变。

## 拟上传八个文件与Diff

|文件|变更|目的|
|---|---|---|
|.github/workflows/collect.yml|修改|原手动诊断升级Beta03B；Ubuntu24.04/Python3.12；仅workflow_dispatch|
|.gitignore|修改|排除本地beta03-artifacts目录，避免结果混入提交|
|poc/index_sources.py|新增，上阶段原文件|隔离指数实验和超时事件记录|
|tests/test_index_beta03.py|新增，上阶段原文件|保持原32项指数回归测试|
|docs/beta03-index-source-report.md|新增，上阶段原文件|保留真实本地验收和未解决事项|
|poc/cloud_report_beta03.py|新增|五组测试、原21/新增6矩阵、质量核验、脱敏白名单报告|
|poc/requirements-beta03.txt|新增|固定Baostock0.9.4及官方wheel SHA256；隔离安装，不引入传递依赖|
|docs/beta03-cloud-acceptance.md|新增，本文件|审批材料；获批并实际云端运行后补充真实证据|

完整逐行差异：`private/beta03b/upload.diff`。文件SHA256、安全检查和保护文件对比：`private/beta03b/security-review.json`。公开提交应只包含上述八个文件；不包含private、本地Artifacts、原HTTP体、包解压目录、虚拟环境或完整网络日志。

## 本地重新执行结果

固定原Python环境执行 `python -m poc.cloud_report_beta03 tests`，内部五条明确文件清单的pytest命令及每组XML/日志均保存在 `private/beta03-cloud/`。摘要和退出码见该目录 `exit-codes.json`。不将离线故障注入当作真实网络证据。

|验收类别|本地PASS|FAIL|BLOCKED_DEPENDENCY|PENDING_SESSION|NOT_RUN|云端状态|
|---|---:|---:|---:|---:|---:|---|
|A 原功能103|103|0|0|0|0|NOT_RUN|
|B 原PoC38|38|0|0|0|0|NOT_RUN|
|C 原云端契约11|11|0|0|0|0|NOT_RUN|
|D 原解码27|27|0|0|0|0|NOT_RUN|
|E Beta03指数32|32|0|0|0|0|NOT_RUN|

合计211项通过，无删减、跳过或断言修改。首次新增工作流格式产生两项失败，修正后又发现原安全测试禁止工作流正文出现private路径；已将隔离包安装封装为诊断程序入口，工作流直接调用该入口，原断言保持不变。两轮失败日志完整保存在 `private/beta03b/first-test-attempt/`、`second-test-attempt/`，没有覆盖为成功。

原固定Python环境没有pip，首次本地包安装验证退出1（No module named pip）；此项不冒充成功。随后在private独立venv验证与云端相同的pip安装参数和新安装入口，实际退出0。安装固定官方wheel、启用require-hashes和no-deps，未更改原Python环境或正式依赖。Ubuntu setup-python安装能力仍须云端实证。

另外五项离线准备检查通过：手动触发/最小权限及Artifacts白名单；敏感字段注入脱敏与null额保留；日历失败仍保留全部27请求槽位；缺失网络结果不通过；五组明确211数量。证据 `private/beta03b/preflight-checks.json`，标明offline_fault_injection，均不计入云端接口成功率。

## 云端F–J执行方案

|类别|实际云端状态|获批后的执行与判定|
|---|---|---|
|F 原21项|21 NOT_RUN|日历1、原七股新浪/腾讯14、原三指数新浪/东方财富6；保留原分母、所有失败|
|G 新候选6项|6 NOT_RUN|腾讯sh000001/sz399001/sz399006；Baostock实际请求sh.000001/sz.399001/sz.399006|
|H 三指数交叉验证|3 NOT_RUN|核验证券身份、交易所、日历完整性、OHLC、未复权、原0.1%阈值；服务路径与底层独立性分开|
|I 量额与范围|NOT_RUN|腾讯手→股×100，历史amount_cny=null；Baostock股/元；统计范围unknown继续阻断市场量能|
|J 风控拦截|本地原安全/故障测试PASS；云端NOT_RUN|所有PoC buy_permission=false、research_permission=false；任何失败、缺失、缓存或冲突均不放行|

每个真实请求记录时间戳、耗时、返回/有效数量、日期、公开OHLC与量额、单位/统计范围、提供方、HTTP或协议异常类型、错误链、失败阶段和尝试计数。日历失败仍执行全部21+6槽位，但日历相关价格验收NOT_RUN。子进程22秒、HTTP诊断5/8秒；东方财富SDK分页自带15秒及最多3次尝试保持记录，应用新增重试0。Baostock匿名公共端口socket8秒，无VIP/APIkey，连接EOF拦截；不更换代理、不关闭TLS、不绕过限制。

创业板此前两个公开源成交量：腾讯19,123,480,100股、Baostock19,705,628,327股，约3.04%差异，保存在原本地报告。云端将另存新真实值，不覆盖历史值、不调整数值、不放宽阈值、不拼接。指数成分范围不足以证明feed成交量聚合范围；不能解释时仍UNVERIFIED。底层供应链独立性统一UNVERIFIED，价格相同也不宣布完整P0通过。

## 安全与Artifacts

工作流唯一触发为workflow_dispatch；permissions仅contents:read；checkout不保留凭据；无部署/通知/下单入口；保留15分钟任务上限。Baostock官方BSD包的SHA256固定为 `0bf71c6069ab5890ff3596632f9c3f8f1fbc6bfcac582c2f9d6a5c11ab2cfaf8`，仅公开指数、SDK默认匿名身份，无私人账户输入、收费权限或Tushare调用。

Artifacts严格八项白名单：acceptance.json、acceptance.md、original-tests.json、backup-tests.json、cloud_contracts-tests.json、decoder_beta02-tests.json、index_beta03-tests.json、sha256.json；保留3天。测试摘要移除失败消息/traceback/stdout；网络输出只保留公开数值、身份、单位、时间和异常类型，不导出raw_http/raw_records/凭据/头部/异常文本/完整private目录。初始化与失败后finalize均生成证据；运行被GitHub基础设施硬终止时Artifacts仍可能不可用，必须如实记录。

## 真正通过、失败与未验证

真正通过：本地五组211测试、原成果文件一致性、隔离依赖安装验证、五项脱敏/失败准备检查。早期本地失败已记录并修正；最终本地测试无失败。

云端Commit SHA：尚无新增Commit；基准如上。Actions URL：尚未运行，不存在本阶段URL。云端真实成功/失败数量：未执行，不可沿用本地9/21或6/6填充。连续交易日稳定性PENDING_SESSION，持续云端观察条件尚未验证。

影响后续策略：新浪/东方财富网络可能持续失败；腾讯未认证人民币成交额，候选统计范围和底层独立性未知，创业板数量差异未解释。因此仍禁止依赖市场量能放行，不改变核心门禁。

## 待明确批准

申请仅将上述八文件提交并Push到原PoC独立分支一次，随后仅手动执行Beta03B诊断一次；使用该实际Commit SHA，失败也不自动重跑。获批后读取实际Actions日志与白名单Artifacts，更新本文件的真实Commit、运行URL、A–J结果及问题清单；这次Push不自动包含运行后本地报告更新，不再第二次Push。

风险：Ubuntu公共TCP出口及免费接口稳定性未实证；Baostock匿名公共TCP不加密，仅查询公开指数；一次成功不证明长期稳定或完整质量；全程保留PARTIAL/UNVERIFIED边界和关闭许可。不得合并main、发布、开启Pages/定时或接入正式策略。

依据本次用户指令第十三条以及AGENTS.md第七条，**此处暂停，等待用户明确批准公开Push一次和手动诊断一次。**
