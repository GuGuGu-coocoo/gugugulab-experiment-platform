# 合成工程验收记录

日期：2026-09-08。所有数据为隔离合成数据。此记录区分实际运行、组件验证和外部待验；**Phase 01–03 尚不能宣称全部验收通过**。

## 实际环境

| 组件 | 实测版本／范围 |
|---|---|
| OS | macOS 26.6.2 (25G83)，arm64 |
| Godot / templates | 4.7.2 stable，Web Compatibility / 单线程；macOS universal 包在 arm64 执行 |
| Chrome | 152.0.7977.77，实际 WebGL/WASM、IndexedDB、Web Locks |
| Native store | Godot SQLite v4.7 / SQLite 3.51.0；事务、进程终止分别验证 |
| Python / Django / server SQLite | Python 3.12.13 / Django 5.2.17 / SQLite 3.53.1 |
| Node / browser testing | Node 24.18.0 / Playwright 1.58.2 |
| CSV viewer | Excel 16.112.3；UTF-8、逗号导入实测 |

## 已执行

- `pytest`：32 项通过，覆盖严格 JSON、UUID、三种准入、创建操作私密证明、整批回滚、内容冲突、持久化去重、完成缺口、对象越权、固定快照、下载撤权、邀请码和现有账号密码保护、归档恶例、请求限额、错误版本及卷标记。
- Chrome GUI：登录、研究创建、三种参与政策／名单、构建登记、配置导出、Web 上传／隔离预览／批准发布，管理 Cookie 与实验 hostname 分离。
- 真实 Godot Web：按键产生两种数据结构，实际 IndexedDB → HTTP API → SQLite → ACK → JSONL 逐 ID／值对账；断网保存、恢复上传、trial 边界恢复与新 epoch。
- 独立 macOS 二进制：外置配置默认读取、两类数据、逐条 JSONL 对账、进程终止后原会话恢复、原事件不重做、新 segment。
- 原生丢 ACK：代理在真实服务器提交后断开响应，重启独立接收进程，再重传；四条记录仍只有四个 ID。
- 原生事务故障：调用真实 SQLite 插件，在 COMMIT 前返回失败／SIGKILL；重开只保留前一个完整记录／检查点边界。不是断电测试。
- 真实 IndexedDB：事务 abort、ACK 本地更新失败、清理中断／重开、未知存储版本、单写入者、新参与锁定旧前台恢复、配置更换不重定向旧 pending。
- 原生 HTTPS：Godot HTTPRequest 使用系统信任访问官方 Godot GitHub 元数据成功，未关闭证书验证。GEP 全链路验收使用本机 HTTP，不能外推为 LAN HTTPS 验收。
- CSV：独立解析保留 null／缺失／0／false／中文／001／负数／多值；Excel 实际导入后中文正常、公式样字符串保留为文本。

## T01–T24 对照

| 项目 | 状态与证据范围 |
|---|---|
| T01–T03、T05、T07–T08 | 本机工程通过；真实 Godot、API、数据库和导出，及服务器负向矩阵 |
| T04 | Chrome 两 hostname 和 host-only Cookie 通过；只接纳维护者审查包，不声称恶意同源实验彼此隔离 |
| T06 | IndexedDB 事务／刷新／ACK 失败通过；真实磁盘耗尽和物理断电未执行 |
| T09 | 2026-09-12 实际 Compose 通过：真实原生上传/授权导出、容器重启及替换持久化、Owner 保留、重复初始化和错卷拒绝；见 [Compose 指南](../deploy/README.md) |
| T10 | **外部待验**：Windows 11 Pro build 26200 / Chrome 152.0.7977.76 已完成真实受信任 HTTPS、物理断网、补传、关闭重开队列、trial 恢复及双标签争用；小限额背压等剩余矩阵待补齐 |
| T11 | 到期／撤销／旧队列固定绑定组件通过；真实研究撤回与保留治理未启用 |
| T12 | Phase 04；独立备份恢复、RPO/RTO 未验 |
| T13 | 委派、重复邀请、已有账号不可重置与撤权组件通过；正式 Owner 治理／MFA 未启用 |
| T14 | 有界 ZIP、路径／类型／摘要／版本检查和实际发布通过；上传中断全矩阵仍需扩展 |
| T15 | 有限 JSON 文本 CSV 解析和 Excel 导入通过；不是科学字段展开或通用电子表格保证 |
| T16 | Git／配置／依赖产物检查；未加入运行库、真实数据或凭据；完整生产安全审计未执行 |
| T17 | 自动 GUI 工程回归通过；**独立人类验收未执行** |
| T18 | 同一 task 源码、本地／两种 GEC 原值和固定顺序对账；物理输入、呈现精度和开销上界未验 |
| T19 | 通用模块、独立本地后端、同一 task 与两种 GEC 通过；本地不报远端成功 |
| T20 | 当前 macOS arm64 独立二进制及事务／重启／丢 ACK 通过；原生窗口/匿名启动/实体左右键已由用户实际操作验证；正式签名公证未做 |
| T21 | 三模式 GUI+API，UUID、001、错误凭据、重复创建、跨研究隔离通过 |
| T22 | 两发行、公开配置、默认外置路径、错绑定／版本、旧 pending 目标固定通过；完整大包中断矩阵未验 |
| T23 | Web 刷新与原生强杀，完整 trial、原 session、新 segment/epoch 通过；新增恢复数据包导出；Web 无策略时授权恢复仅允许数据导出、不进入试次的真实 API/IndexedDB 检查通过；原生无策略路径尚待独立实测 |
| T24 | Web 活动保留／未齐／ACK 失败／清理中断与共享设备锁定通过；原生完成清理及进程终止边界通过；原生清理每个中断点的完整矩阵未执行 |

## 已修复的问题

1. Godot JSON 读回把整数表示为 `1.0`：服务器按整数数值校验序号，仍拒绝小数和布尔值；不改原 payload。
2. Web 动态求值与 CSP 冲突：改用 JavaScriptBridge 对象接口，未放宽 CSP。
3. CSV 中文被 Excel 默认编码误读：CSV 增加 UTF-8 BOM；RAW 不变。
4. 自动化抢在刺激准备前按键：用画布可访问 trial 标签等待实际显示，再输入。
5. Godot 文本控件无法完整处理零间隔自动逐键输入：恢复验收采用 30 ms 逐键间隔；尚未声称支持任意自动注入速率。

## 剩余限制

Docker、独立操作人及剩余故障矩阵仍待验。第二实体 Windows 的基础 LAN 场景已验证，不等于完整故障矩阵通过。Compose 初始化／运行验收、完整限额故障矩阵、原生 GUI 恢复输入、原生清理中断全矩阵仍未交付完整验收。真实研究、生产部署、备份责任、计时、隐私／撤回／保留政策、MFA 与发行签名另行批准。当前版本不可用于真实收数。

## LAN 复验及自动化更新

Windows 11 Pro build 26200、Chrome 152.0.7977.76，使用单独用户批准的临时 CA 和 IP SAN，未跳过 TLS 校验。正常收数、部分断网、完整断网后关闭重开补传、同设备试次恢复、双标签拒绝及重开不显示旧身份均有操作记录。四份 Windows 本地恢复导出经 SSH 取回，按 event_id 与服务器完整值比较通过。未完成会话开始新参与后的恢复授权和清理全矩阵仍需补充。

发现网络恢复后旧错误仍显示：已在持久清理成功后清空错误，真实断网回归通过。发现 Godot Web 凭据粘贴异常：网页改用浏览器原生输入框，真实 Godot 恢复测试通过连续更换系统剪贴板、各粘贴一次完成恢复，提交后清空秘密字段。新输入方式已在 Mac Chrome 自动验证，并在 Windows Chrome 新构建上实际复验：会话 ID 与许可各粘贴一次即恢复 Trial 2。原会话保留、第一试次两条完整记录不变、新运行段和 epoch、四条唯一记录与授权导出均核对通过，许可仅消费一次。

新构建 Web/macOS 成功；发行准备两项与独立回归十四项通过，随后新增无策略数据恢复测试并重验存储七项通过。没有以自动浏览器替代 T17 独立研究者验收。

Mac 原生窗口补验：用户确认独立窗口文字/按钮正常，实体左右键完成两个试次；服务器四条唯一记录、完成无缺口、授权导出一致。原生 SQLite 会话表最终仅保留已确认墓碑。此次未在清理前取原生原始快照，不把逻辑清理检查外推为介质安全擦除或科学计时验证。

原生恢复导出补验：原生保存对话框导出的两条记录与 checkpoint 和 SQLite 逐值一致，不含令牌/私密证明。关闭重开后经既有恢复参数传入授权许可，实际图形 Start 恢复到 Trial 2，完成后原记录不变、新 segment/epoch、服务器收齐及授权导出一致。CUA 自动填写原生恢复框仍返回 400，粘贴工具超时，GUI 输入问题保留待排查；参数路径不替代该项。Web 四框遮挡 Start 按钮也已记录，按用户要求稍后集中修复。

## 今日界面修复收尾

提示区原为两行，切换为单行后 Godot VBox 中的按钮上移，而 Web 覆盖输入框保留初始坐标，导致遮挡。固定初始两行提示区的最小高度后，真实 Godot 场景对 Trial/Error/Finished 状态逐帧比较，四个字段及 Start 矩形保持一致，许可框下边界不超过按钮顶部。status_layout.gd 通过；重新构建 Web/macOS，发行准备两项及独立回归十五项通过。旧批准发行未原地修改。

原生自动输入继续失败：包含大小写/下划线/连字符的无敏感样例与随机一次性许可，分别用文本注入、明确按键及混合方式测试，出现 not_recoverable/http_400；没有取得字段实际值的可靠比较。不能将其确定归因为程序或工具，也没有用恢复参数成功替代 GUI 输入通过。保留未完成记录和原本地队列，停止扩大今日范围。

## 2026-09-10 状态索引

本次仅依据上述 2026-09-08 证据整理 [README 的开发状态与目标架构](../README.md)，没有新增功能测试结果。原生 GUI 输入、Compose、限额/状态/恢复/清理剩余矩阵、大包上传中断、Windows 修复后布局与 T17 独立研究者仍待闭合。上文较早“Web 四框稍后修复”记录已由“今日界面修复收尾”覆盖，不能据此重复认定源码未修复；也不能将源码修复等同于 Windows 新布局实机确认。Launcher、完整下载套件及 Admin 补交图示为目标能力，不是新验收证据。

## 2026-09-12 native GUI recovery acceptance

The native recovery input limitation is resolved for the tested macOS workflow. A diagnostic harness loaded the real scene and compared fields without logging credential contents: automated text injection dropped an underscore; paste reported a tool timeout but delivered the exact 43-character permit. Clicking Start then resumed Trial 2.

A separate run in the exported macOS executable used the actual recovery fields, paste, Start and Right, with no recovery command-line arguments. It reached `remote_acknowledged`. Both runs retained the original session and first trial records, created a new segment and clock epoch, stored exactly four unique events, consumed one recovery permit, matched the authorized JSONL snapshot and retained only the acknowledged local tombstone. This closes the native GUI input issue; independent human usability acceptance remains outstanding. The server regression suite also passed all 32 tests on this date.

Native cleanup fault coverage: five additional real SQLite/HTTP tests passed. Failed batch-ACK persistence retained all four pending records; failed completion-ACK persistence retained raw records and checkpoint; failed tombstone persistence retained the durable completion ACK. SIGKILL immediately before or after the cleanup COMMIT reopened safely and completed cleanup. All five cases ended with four unique server records and the expected original RT values. These are process/transaction failure tests, not physical power-loss or secure-erasure claims.

Authorized recovery previously left a paused upload queue paused. Both backends now reset the retry budget only after the server accepts the study permit and private proof. Native data-only recovery and Web real IndexedDB tests reject invalid permits without unpausing; valid recovery uploads preserved records without replay. Both distributions rebuilt successfully; 2 release prerequisites and all 21 subsequent end-to-end tests passed. Completed-session reauthorization remains outside this new test evidence.

Compose acceptance on 2026-09-12 used an isolated Ubuntu arm64 VM with Docker 29.1.3 / Compose 2.40.3. Real GUI setup and the exported Godot experiment completed against that instance. Restart and container replacement preserved database records, build, Owner and volume file; reinitialization failed and a different initialized volume stopped with the expected marker error. Final-image incomplete initialization also refused serving. The full server suite passed 34 tests.

Finished-queue reauthentication was previously rejected. It now requires the same scoped permit/private proof and keeps the original completion set closed. Both real IndexedDB and native SQLite tests recover expired finished queues as data-only, retain IDs/segments and clean only after receipt; extra undeclared events and revoked sessions remain rejected. The GUI explicitly says no trials resume and displays data status. Rebuilt Web/macOS packages passed 2 release prerequisites plus all 23 end-to-end tests; 36 server tests passed. This supersedes the earlier completed-session reauthentication limitation.
