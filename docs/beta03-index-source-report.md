# V3.1 Beta 0.3 指数备用源验收报告

**结论：PARTIAL。本地隔离实验完成；不具备正式行情接入或交易许可。**

## 基线与变更

当前 HEAD 与指定基准一致：`02cc0d335a00181139dadb20594630867eff3b7c`；分支 `poc/free-backup-sources`。开始时工作区干净，没有无法解释的差异。未产生新 Commit，未 Push，未运行云端 Actions。
实际新增文件仅 `poc/index_sources.py`、`tests/test_index_beta03.py`、本报告。原179项测试文件、原 PoC、正式引擎、依赖清单和工作流逐字保持基准内容，Git 对比退出0。5000元、最多2只、100股、T+1、50元风险预算及P0/P1/P2门禁不变。
执行前完整读取 AGENTS.md、Beta01/Beta02本地验收报告、PoC代码、原测试和手动工作流。历史云端 Beta02 [运行38194318152](https://github.com/caiyiguo709-crypto/a-share-v31/actions/runs/38194318152) 的18/21仅作历史比较；以下均为本次新网络结果。

## 本地测试与证据

首次基线179 PASS，5.75秒。新增测试最初因新模块尚不存在而收集失败（退出2），实现后32 PASS。总集成运行211 PASS，6.12秒；最终变更后五类分别重新执行，均退出0。离线故障注入只证明拦截逻辑，不计入真实接口分母。

|类别|PASS|FAIL|BLOCKED_DEPENDENCY|PENDING_SESSION|NOT_RUN|
|---|---:|---:|---:|---:|---:|
|原功能|103|0|0|0|0|
|原PoC|38|0|0|0|0|
|原云端契约（本地）|11|0|0|0|0|
|原解码|27|0|0|0|0|
|Beta03新增|32|0|0|0|0|

回归覆盖：指数/股票混淆、交易所、同来源/伪造来源、日期错配/重复/缺口/未来/过期、OHLC冲突、复权、量单位及倍率、额单位/假零、范围混用/未知、空数据、缺额、进程超时保留中间事件、混合源序列、旧nonce/缓存和关闭买入/研究许可。价格阈值沿用原 `cross_check` 的0.1%，没有改动。
命令证据：固定Python执行 `-m pytest -q -p no:cacheprovider`；基线及总集成另加 `--junitxml=private/beta03/...xml`；分组显式指定原测试文件。完整命令、输出、退出码见 `private/beta03/group-tests.json`；日志/XML与基线位于同目录。

## SDK、来源与单位审计

固定运行环境：Python3.12.14 / AKShare1.19.1。源码通过实际导入对象 `inspect.getsource` 保存，未根据字段名称推定单位。

|接口|真实提供路径/代码|返回与单位|重试/时间边界|
|---|---|---|---|
|stock_zh_index_daily|finance.sina.com.cn；sh000001/sz399001/sz399006|date/OHLC/volume；股/手合同未确认；无成交额|签名仅symbol；SDK无timeout；诊断缺省5/8秒，外层22秒|
|index_zh_a_hist|东方财富80.push2代码映射后push2his日线；000001/399001/399006|日期/OHLC/成交量手/成交额元，转换×100；fqt=0|映射分页15秒，最多3次尝试和指数退避；日线路径另有市场回退；外层22秒|
|stock_zh_index_daily_tx|proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get；完整带交易所代码|date/open/close/high/low/amount；此amount是手，只生成volume_shares×100，amount_cny=null|签名symbol,start_date,end_date；按年请求，无内置timeout；诊断5/8秒，外层22秒|
|Baostock0.9.4隔离实验|public-api.baostock.com:10030；sh.000001/sz.399001/sz.399006|date/code/OHLC/volume股/amount元/adjustflag=3|匿名公共端点，无项目依赖安装；socket8秒，外层22秒；EOF立即失败|

依据：[AKShare指数文档](https://akshare.akfamily.xyz/data/index/index.html)、[AKShare SDK源码](https://github.com/akfamily/akshare/blob/main/akshare/index/index_stock_zh.py)、本机固定1.19.1源码；[Baostock指数字段文档](https://www.baostock.com/mainContent?file=indexData.md)、[K线字段文档](https://www.baostock.com/mainContent?file=stockKData.md)。动态站点文档保存了本次公共接口读取快照。
腾讯原响应实际包含每个准确代码的 `day`（不是qfqday），为未复权价格；额外原始槽位没有指数专用单位合同，未解释成成交额。SDK自身drop_duplicates已审计，新增实验拒绝原响应窗口出现重复或与SDK条数不一致；最终三个腾讯请求再次执行并保存独立证据，不覆盖初次结果。
Baostock免费性依据[官方首页](https://www.baostock.com/mainContent?file=home.md)与[官方PyPI](https://pypi.org/project/baostock/)；使用0.9.4官方轮子校验SHA256 `0bf71c6069ab5890ff3596632f9c3f8f1fbc6bfcac582c2f9d6a5c11ab2cfaf8`，只在private独立目录解压、worker中加载，未改固定环境或依赖。匿名登录和查询各返回error_code=0，无APIkey、VIP、注册、积分或收费权限；Tushare未执行。公共TCP协议不加密，实验仅发送公开指数查询及SDK匿名身份，不含私人账号；不得直接用于账户业务。
统计范围：所有候选量额 `quantity_scope=unknown`。指数价格样本范围与成交量统计范围是两件事；中证/国证指数成分说明不能代替腾讯/Baostock feed 的量额聚合合同。未跨未知/不同范围比较量额、未计算市场量能。Sina/Tencent/Baostock服务路径不同可证实；底层feed供应链独立性无法证实，统一UNVERIFIED，不宣称完整独立双源。

## 原21项真实接口矩阵

执行时间：2026-10-12T10:38:41.272028+08:00 至 2026-10-12T10:39:26.948994+08:00。请求窗口2026-03-06至2026-10-09；新浪真实交易日历确认最新应有交易日2026-10-09。

|接口|对象|状态|秒|错误链/阶段|
|---|---|---|---:|---|
|calendar_sina|calendar|PASS|0.847|无 / 无|
|sina_raw|601001|FAIL|5.441|ReadTimeout → ReadTimeoutError → TimeoutError / request_or_decode|
|tencent_sdk|601001|PASS|0.993|无 / 无|
|sina_raw|000983|FAIL|5.404|ReadTimeout → ReadTimeoutError → TimeoutError / request_or_decode|
|tencent_sdk|000983|PASS|0.901|无 / 无|
|sina_raw|600267|FAIL|5.446|SSLError → MaxRetryError → SSLError → SSLEOFError / request_or_decode|
|tencent_sdk|600267|PASS|1.127|无 / 无|
|sina_raw|002312|FAIL|5.467|SSLError → MaxRetryError → SSLError → SSLEOFError / request_or_decode|
|tencent_sdk|002312|PASS|0.947|无 / 无|
|sina_raw|002602|FAIL|5.447|SSLError → MaxRetryError → SSLError → SSLEOFError / request_or_decode|
|tencent_sdk|002602|PASS|0.926|无 / 无|
|sina_raw|002354|FAIL|5.375|SSLError → MaxRetryError → SSLError → SSLEOFError / request_or_decode|
|tencent_sdk|002354|PASS|0.941|无 / 无|
|sina_raw|000066|FAIL|5.395|ReadTimeout → ReadTimeoutError → TimeoutError / request_or_decode|
|tencent_sdk|000066|PASS|0.971|无 / 无|
|index_sina|000001|FAIL|5.368|SSLError → MaxRetryError → SSLError → SSLEOFError / request_or_decode|
|index_em|000001|FAIL|9.144|ProxyError → MaxRetryError → ProxyError → RemoteDisconnected / request_or_decode|
|index_sina|399001|FAIL|5.376|ReadTimeout → ReadTimeoutError → TimeoutError / request_or_decode|
|index_em|399001|FAIL|6.198|ProxyError → MaxRetryError → ProxyError → RemoteDisconnected / request_or_decode|
|index_sina|399006|PASS|0.685|无 / 无|
|index_em|399006|FAIL|6.082|ProxyError → MaxRetryError → ProxyError → RemoteDisconnected / request_or_decode|

原矩阵：9/21 PASS（42.86%），12 FAIL，BLOCKED_DEPENDENCY/PENDING_SESSION/NOT_RUN均0。失败保持FAIL，新增候选不进入21项分母。HTTP200不自动通过：原接口PASS须解析出数据，双源/单位另行验收。股票双源0/7通过（新浪7个连接均失败），腾讯股票7个取得数据；这是新网络情况，不覆盖Beta02的历史7/7。

## 新增候选与三大指数

|来源|指数|请求|条数/最新日期|O/H/L/C|成交量原值→股|人民币成交额|单位验收|
|---|---|---|---|---|---|---|---|
|index_tx|sh000001|PASS|145/2026-10-09|3804.09 / 3824.71 / 3754.14 / 3813.79|535149921.0 → 53514992100.0|None|量PASS / 额UNVERIFIED|
|index_tx|sz399001|PASS|145/2026-10-09|12557.02 / 12679.63 / 12278.78 / 12641.86|632657827.0 → 63265782700.0|None|量PASS / 额UNVERIFIED|
|index_tx|sz399006|PASS|145/2026-10-09|3021.33 / 3053.38 / 2932.8 / 3043.33|191234801.0 → 19123480100.0|None|量PASS / 额UNVERIFIED|
|index_bs|sh000001|PASS|145/2026-10-09|3804.0896 / 3824.7091 / 3754.1353 / 3813.7913|53514992100.0 → 53514992100.0|888561198263.9|量PASS / 额PASS|
|index_bs|sz399001|PASS|145/2026-10-09|12557.0151 / 12679.6324 / 12278.7844 / 12641.8584|63265782674.0 → 63265782674.0|1011994216302.37|量PASS / 额PASS|
|index_bs|sz399006|PASS|145/2026-10-09|3021.33 / 3053.3768 / 2932.7956 / 3043.3346|19705628327.0 → 19705628327.0|494823547448.06|量PASS / 额PASS|

候选首次请求6/6 PASS（腾讯3/3、Baostock3/3）；FAIL/BLOCKED_DEPENDENCY/PENDING_SESSION/NOT_RUN均0。最终腾讯代码复验另计3/3，不混入候选6项分母。六个序列均145条，日历完整性、重复、缺口、OHLC和最新日期通过原校验；未使用缓存。

|指数|新浪↔腾讯价格|新浪↔Baostock价格|腾讯↔Baostock价格/近20日最大偏差|完整双源质量|
|---|---|---|---|---|
|sh000001|FAIL|FAIL|PASS / 1e-06|UNVERIFIED|
|sz399001|FAIL|FAIL|PASS / 0.0|UNVERIFIED|
|sz399006|PASS|PASS|PASS / 2e-06|UNVERIFIED|

新浪↔腾讯、新浪↔Baostock价格各1/3（33.33%）；上证/深成因为新浪连接失败而FAIL。腾讯↔Baostock数值价格3/3（100%），仅说明不同服务路径公开价格一致。**指数对象完整双源质量0/3通过，3/3 UNVERIFIED**（供应链独立性、范围等未确认）。
候选成交量单位6/6 PASS；候选成交额单位3/6 PASS，3/6 UNVERIFIED（腾讯明确null）；量额同时单位通过3/6（Baostock）。范围通过0/6，因此市场量能可用0/6；这些分母不与接口成功率混合。
创业板最新成交量：腾讯规范化19,123,480,100股，Baostock19,705,628,327股，数值差异约3.04%。范围未证实，既不直接判同口径冲突，也不计算行情量能或平均修补；报告保留差异。上证与深成数量接近也不能据此推断范围已确认。指数点位不是股票成交均价，未用amount/volume与指数OHLC比值冒充单位证明。

## 东方财富超时与网络错误链

Beta02创业板22.03秒超时是历史事实，但旧日志没有中间尝试数，不能反推准确次数。本轮未复现22秒超时；三个仍失败，均停在80.push2.eastmoney.com代码映射分页，未进入push2his历史行情阶段。

|指数|SDK Session请求数|进入HTTP transport数|未完成transport|应用尝试/新增显式重试|耗时|
|---|---:|---:|---:|---|---:|
|sh000001|5|5|0|1 / 0|9.144|
|sz399001|3|3|0|1 / 0|6.198|
|sz399006|3|3|0|1 / 0|6.082|

000001先有两次分页HTTP200，下一页三次断连；399001/399006第一页三次断连。实际链ProxyError→MaxRetryError→ProxyError→RemoteDisconnected。SDK分页request_with_retry最多3次（含首次，即最多2次重试），15秒单次超时与退避总和可能超出22秒外层期限；这是源码可解释的机制，不能据本轮宣称历史超时根因已被实证。
事件按请求前/后及transport前/后落盘，外层杀进程也保留已开始次数、未结束次数和最后公开路由；新增离线超时测试验证该记录保留。transport计数只覆盖进入_make_request；TLS/代理握手阶段失败可能Session=1、transport=0，并不表示没发起连接。未启用应用重试、未改变代理、未绕过限制、未关闭TLS。新浪新失败分为ReadTimeout或SSLError→MaxRetryError→SSLError→SSLEOFError，均在请求阶段，不能归咎于解码器。

## 安全、失败清单与交付证据

本次完整失败：新浪股票7项；新浪上证/深成2项；东方财富3项。对应矩阵、异常链和时间见上表与private JSON。新浪创业板单位未确认；腾讯历史人民币额三项缺失；所有候选统计范围和底层独立性未确认；股票双源全部拦截；指数完整质量全部未通过。
所有pair的buy_permission/research_permission均false，空、旧、未来、冲突、混合源、错误单位/范围和缓存注入均不能放行。实验不写正式信号或账户文件，无真实下单入口。
真实证据：`private/beta03/real-results.json`（新生成原21/候选6/最终腾讯3的结果）、每个packet JSON（包含原响应/原记录）、每个trace JSONL、`real-run.log`；SDK源码审计、官方文档快照和包校验均同目录。raw HTTP响应全部留private，不放公开报告或Artifacts。
代码差异：`private/beta03/changes.diff`；基准完整性/文件SHA256/脱敏扫描：`private/beta03/integrity.json`。新增文件无账号Token、密码、私人成交或持仓；SDK匿名默认口令仅用于公共Baostock身份，不是私人凭据。本报告只保留公共指数行情与错误类型；测试fixtures标明离线用途。

## 下一阶段方案与暂停事项

建议先批准一次独立分支云端验证：仅本地这三个新增文件及必要的手动诊断工作流/分组契约变更，经Diff审阅后提交；新增Beta03组必须单列32项，不能被既有原103统计误吸收。Ubuntu需重新验证固定SDK、Baostock独立包哈希/匿名公共TCP可达性、原21及候选6矩阵、所有质量拦截，Artifacts只用白名单脱敏结果，不包含原HTTP体或私人环境。只允许一次workflow_dispatch，不开启定时/Pages/合并。
风险：本机成功不保证Ubuntu TCP出口或免费服务稳定；新浪/东方财富连接仍波动；Baostock公共TCP不加密；底层feed独立性及量额范围尚无证据；腾讯无已认证历史额。建议至少五个交易日采样，期间保持PENDING_SESSION，不启用市场量能和买入。正式接入建议须等范围、单位、独立性及云端验收解决后另行审批；目前仅推荐Baostock继续作为实验候选。
本阶段云端运行、公开Push、正式架构接入、策略修改均NOT_RUN；跨交易日稳定性PENDING_SESSION。未调用收费接口、未购买积分或开通权限。**已暂停，等待用户批准下一阶段。**
