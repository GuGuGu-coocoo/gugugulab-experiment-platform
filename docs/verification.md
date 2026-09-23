# 合成工程验收记录

更新：2026-09-23。全部使用隔离合成数据。Phase 01、02 的有限工程门槛通过；Phase 03 工程回归与 Windows x64 实机工程门槛于 2026-09-21 通过，**2026-09-21 已进行非开发者人工测试并形成修复反馈；修复后的完整回归与新一轮人工测试尚未进行**。真实研究尚未启用。

## 实际环境

| 组件 | 实测版本／范围 |
|---|---|
| Mac | macOS 26.6.2 (25G83)，arm64，Chrome 152.0.7977.77 |
| Godot / templates | 4.7.2 stable；Web Compatibility 单线程；macOS universal 包在 arm64 实际运行 |
| 原生存储 | Godot SQLite v4.7 / SQLite 3.51.0 |
| 服务端 | Python 3.12.13 / Django 5.2.17 / SQLite 3.53.1 |
| 自动回归 | Node 24.18.0 / Playwright 1.58.2 |
| 第二实体设备 | Windows 11 build 26200；9 月 8 日 Chrome 152.0.7977.76，9 月 12 日 Chrome 152.0.7977.83 |
| Windows 原生实机 | Windows 11 Pro build 26200，AMD64，交互控制台会话（2026-09-21 实机运行） |
| Compose | Lima 2.2.0 / Ubuntu 26.04 arm64 / Docker 29.1.3 / Compose 2.40.3，独立合成卷 |
| CSV 查看 | Excel 16.112.3，UTF-8 与逗号导入 |

## 最终回归

- 服务端 56 项通过：实际数据库事务、三种准入、名单有效性和参与限额、持久限流、同 ID 冲突/回滚、完成集合、私密证明、对象权限、邀请委派、固定快照与下载撤权、恶意包及初始化边界。
- Web/macOS 均已重新构建；发行准备 2 项与后续贯通 31 项通过。实际 Godot、IndexedDB/SQLite、HTTP、数据库和授权 JSONL 对账，不以 HTTP 200 或全 Mock 代替。
- macOS 导出程序经真实恢复输入框、粘贴、Start 与响应按键恢复 Trial 2；原记录不变，新 segment/epoch，四条唯一记录、一次许可消费、授权导出与本地墓碑一致。
- Windows 实体机新版 Godot Web：开始/Trial 2/结束截图确认四框不挡 Start；延迟真实上传时仍可完成并本地保存；64 条内存上限与每批 32 条上传、最终清理通过。4 条实验记录和 64 条边界记录分别与数据库及受保护导出核对。
- 最小 Compose：真实研究登记、原生运行和授权导出；数据库/文件/实例/Owner 在重启和最终源码镜像替换后保留；重复初始化拒绝，错误卷拒绝服务。

复现入口：[启动与测试](quickstart.md)、[Compose](../deploy/README.md)、[Windows 工程辅助程序](windows_acceptance.md)。

## T01–T24 有限场景结果

| 项目 | 结果与证据边界 |
|---|---|
| T01–T03、T05、T07–T08 | 通过：两类原值、事务回滚、同 ID 内容冲突、持久去重、跨研究授权、固定绑定及授权快照 |
| T04 | 通过：两个 hostname 与 host-only 管理 Cookie，隔离预览；只接纳维护者审查包，不声称恶意同源实验彼此隔离 |
| T06 | 通过：真实 IndexedDB 事务中止、刷新、ACK 持久化失败，无假成功；未测试物理磁盘耗尽/断电 |
| T09 | 通过：真实 Compose 上传、重启/替换持久化、Owner、重复初始化、初始化未完成及错卷拒绝 |
| T10 | 通过有限场景：第二实体 Windows 的受信任 HTTPS、物理断网补传、关闭重开、trial 恢复、双标签争用；新版布局与小限额背压补验。新补验使用 LAN SSH 回环隧道，与早期 HTTPS/拔线证据分列 |
| T11 | 通过合成政策：暂停/关闭拒绝新参与但接受有效旧上传；到期授权重新认证；撤销仍拒绝。实际研究撤回/保留治理未启用 |
| T12 | 未运行，Phase 04：独立备份恢复、RPO/RTO |
| T13 | 通过工程矩阵：过期/撤销/重复邀请，委派撤销，有限 Admin 越权拒绝；已有账号密码不可被邀请重置。正式 Owner 治理/MFA 未启用 |
| T14 | 通过：路径/链接/重名/摘要/外链 schema/兼容性/文件数/压缩及展开限额；真实包的截断、网络失败、fsync/rename 两处强杀和幂等重试不影响旧 release |
| T15 | 通过：有限 JSON 文本 CSV 保留 null/缺失/0/false/中文/001/负数/多值；独立解析与 Excel 导入。不是通用科学变量展开 |
| T16 | 通过本次所选源码/配置/包/容器上下文的秘密与数据排除检查；不代表完整生产安全审计 |
| T17 | **未运行**：非原开发者独立研究者操作。已准备[操作与记录指南](researcher_acceptance.md)；自动化不替代它 |
| T18 | 通过合成工程场景：同一 task 的顺序/值，原生及实体 Windows 在延迟上传期间继续响应、本地提交；无物理呈现/输入精度或开销上界承诺 |
| T19 | 通过：主体依赖通用模块，初始化选择本地/Web/原生；本地后端不报远端成功 |
| T20 | 通过 macOS arm64：独立二进制、真实 SQLite、事务失败/进程终止/接收进程重启/丢 ACK 和导出；用户实际窗口与左右键、代理真实 GUI 恢复均有证据；未做签名公证 |
| T21 | 通过：三模式 GUI/API、UUID、001、错凭据、名单到期/停用/次数、跨研究身份隔离、私密创建证明 |
| T22 | 通过：两发行、公开配置、默认外置路径、错研究/版本拒绝、实际配置替换不重定向旧待传；上传中断不发布半成品或更改旧 release |
| T23 | 通过：Web 重开/原生强杀的一致 trial 检查点；原 session、新 segment/epoch；无策略及已结束会话只能授权恢复数据；授权失败不解除暂停 |
| T24 | 通过：活动恢复依赖保留、未齐不能删、ACK 更新失败保留、清理事务前后终止可重开；原生五处清理故障、两后端共享设备锁定及最小已清理墓碑 |

## 本轮修复及复验

授权恢复成功后未重置暂停队列，已在两后端原子更新凭据和重试状态；错误许可仍不能解除暂停。已结束待传会话现可用同一研究权限、一次许可及私密证明重新认证，返回 `task_finished` 后只补传，不重做试次或扩大完成集合。开始新参与不再给已清理墓碑增加字段。

原生输入诊断确认自动打字漏掉下划线；粘贴工具虽然报超时，字段实际完整。通过实际字段比较与导出程序 GUI 复验关闭问题。Web 提示区高度变化造成四框挡住 Start 的修复，已在新版 Windows 实机截图和运行中验证。

所有以上修复均保留原科学任务顺序、原 RT/单位/来源与事件身份。曾失败的测试在修复后复验，没有把预期改成错误行为。

## 未验证与使用限制

原测试包已有工程证据；2026-09-21 已进行非开发者人工测试并形成修复反馈，本轮反馈修复与工程回归完成后安排新一轮人工测试（T17 保留编号，帮助/失败/未运行分别如实记录，可由本轮测试者复测）；Phase 03 尚未整体通过。物理断电、真实磁盘耗尽、介质安全擦除、物理输入/呈现计时、容量上界、跨设备恢复、其他原生 OS/架构、正式签名公证未验。原生 1.576 ms 响应加收尾、Windows 64 次记录 0.5 ms/本地提交约 8.8 ms 仅为单次合成观测，不是性能上界或科学时序保证。

程序关闭后不保证后台上传，重开才恢复队列。首次准入必须联网。只支持原设备/原存储环境，未声明恢复策略不能自动重做或跳过试次。清理是逻辑删除，不是取证安全擦除。合法撤回和删除是独立治理流程。

Phase 04 的真实研究协议、伦理/同意、隐私/保留/撤回、备份责任、生产保护和部署均未启用。Runner、Launcher、完整绑定套件下载及 Admin 恢复包补交仍按 README 的目标能力区分，不纳入当前通过声明。

## Phase 03 可用性基础修复

2026-09-12：44项定向服务端测试通过（名单CSV原子回滚、权限拒绝、固定快照与撤权、回显和发行入口等），1项隔离Chrome真实浏览器测试通过（metadata实际下载、立即更新招募后刷新保持、冻结政策就地错误、无脚本异常）。命令见[维护者说明](maintainer.md)。测试使用pytest临时数据库与回环服务，不是原8030现场，也不是独立T17。

首次浏览器测试受到沙箱监听限制，随后测试服务静态URL未配置造成500；修正临时测试配置后复测通过。新增组件测试曾有权限fixture和422状态预期不符，已按现有协议纠正；不更改API错误码以迁就测试。未重复运行已完成Phase01/02全套；没有数据库迁移、科学时序或真实研究验收。

## Phase 03 03C 当前发行与公开政策

2026-09-20：03C 工程批次在隔离测试库与临时包目录完成。定向命令（`pytest -q tests/test_phase03_releases.py tests/test_transactions.py tests/test_gui_packages.py`）57 项通过；真实 Chrome 命令（`GEP_T17_BROWSER=1` 下的 `tests/test_phase03_releases_browser.py`）1 项通过，覆盖研究者公开政策与当前发行 GUI、稳定入口点击携带发行/发布版本、过期入口拒绝页与零会话、刷新后新发行准入、真实 GEC 客户端准入、旧会话上传与冻结直达 URL、管理 Cookie host-only 隔离。并发探针在真实文件数据库上运行：2 项发行切换互斥与 4 项“首次准入 × 切换/关闭招募”相交（含研究者事务未提交时准入必须读到已提交状态），全部通过。只读迁移演练在独立证据目录生成报告：3 项研究迁移后均为 private、current_release=null，Release 配置字节、8 个 session 的 release 绑定、32 条事件与 4 个导出身份在迁移前后一致，源库摘要不变。全量非浏览器套件 131 项通过、4 项按需跳过。以上为合成工程证据，不代表原设计者自主体验或独立 T17 已通过。

## Phase 03 03D 完整原生发行包

2026-09-20：原生完整包批次在隔离实例（独立数据目录、数据库与端口）与真实 macOS arm64 导出上完成。定向命令（`pytest -q tests/test_phase03_packages.py tests/test_gui_packages.py`）通过，覆盖：原生程序归档的有界校验与拒绝路径（路径穿越、绝对/反斜杠、重名、链接、脚本、缺二进制/Info.plist/PCK/依赖、摘要不符、畸形压缩、文件数与压缩比、归档与展开上限）、上传绑定描述与字节不变、批准时的完整包组装（`.app` 字节与可执行位未改、公开配置/schema/codebook/许可证冻结、成员哈希清单、外层摘要只存数据库且不自引用）、重复下载逐字节一致、撤销 build scope 后完整包与 sidecar 均 403、非本研究范围 403、未知清单版本与自引用失败关闭、篡改或缺失时下载 409 且准入在创建会话前 409、打包在文件提交前失败与文件提交后数据库回滚都保留旧发行、内容寻址存储从不覆盖、以及 descriptor-only 原生与 Web 旧契约不变。

真实端到端命令（`tools/phase03_verify_package.py --verify`）：真实 Chrome 登录研究者 GUI，登记描述、上传实际 `.app` 归档（约 62 MB）、批准发行、通过 GUI 链接下载完整包两次（逐字节一致）与 sidecar（manifest、connection.json、许可证、第三方许可声明），邀请持有 build scope 的第二成员下载成功并在 GUI 撤销后立即 403；把下载包解包后直接启动真实 macOS 程序（不传 `--config`，使用包内同级 `connection.json` 默认配置）完成同一合成任务，退出码 0；在完成边界保留的本地 SQLite 记录、服务器数据库事件与授权 JSONL 导出按事件 ID/值一致（4/4/4）；随后篡改服务器存储中的完整包字节，完整包与全部 sidecar 下载均 409、准入 409（`release_unavailable`）且会话数不变，恢复字节后同一发行可再次逐字节下载。以上为合成工程证据，不代表原设计者自主体验或独立 T17 已通过。真实签名、公证与正式部署未验证。

2026-09-20（补正批次）：同一工具新增检查并全部通过——平台冻结的引擎许可/版权声明与官方本地 Godot 4.7.2 引擎现场导出逐字节一致（脚本 `tests/native/engine_notices_export.gd`），包内 `THIRD_PARTY_NOTICES.txt`（SHA-256 `a5c87cbc0b0837d3b0fc8ac1f023cf6f30e7a18d70ddea23f9fa61779dcab55a`）含 Godot 引擎 MIT 许可全文、引擎版本、Godot 内置第三方版权/许可文本与 godot-sqlite 上游许可全文，并与项目 `LICENSE` 分别冻结；GUI 下载的 sidecar 与包内成员哈希一致、被撤销 build scope 后 403。GUI 把完整原生发行设为当前发行后，真实门户列出该研究且 `data-participation="native"`、稳定入口显示独立程序参与说明且 `data-startable="0"`，两者都不含 `/run/.../web/index.html`；浏览门户与入口没有创建会话。原生包内的本机程序仍以包内默认配置完成同一合成任务（退出码 0），受控分发与既有准入不受影响。定向回归：`pytest -q tests/test_phase03_packages.py tests/test_gui_packages.py` 56 项通过，`pytest -q tests/test_phase03_releases.py` 19 项通过，全量非浏览器套件 197 项通过、4 项按需跳过；完整包验证工具 110 项检查 0 失败。

## Phase 03 03D Windows x64 完整原生包（交叉构建工程验证）

2026-09-20：Windows x64 批次在隔离实例（独立数据目录、数据库与端口）与真实交叉导出上完成。新增定向命令（`pytest -q tests/test_phase03_windows_packages.py`）81 项通过，覆盖：Windows 描述必须显式声明程序根/入口/依赖/GDExtension 清单且未知平台失败关闭、Windows ZIP 的路径穿越/绝对路径/盘符/UNC/反斜杠/大小写与 Unicode 归一化重名/保留设备名/结尾点与空格/ADS/控制字符/链接与特殊文件/脚本/未声明可执行文件/多根目录全部拒绝，且在 `PurePosixPath` 规范化之前拒绝原始空/`.`/`..` 组件、NTFS 非法字符 `<>"|?*`，并拒绝文件与目录/父路径的大小写与规范化冲突（例如 `root/./x` 与 `root/x`、`root/Foo` 与 `root/foo/bar` 不能共存；声明路径与 ZIP 成员使用同一套规则）、入口与声明依赖必须为真实 PE32+ x86-64 映像（错误架构、DLL 冒充入口、非 DLL 依赖均拒绝）、PCK 引擎版本核对、随包 GDExtension 清单按有效配置解析（`[configuration]` 的 `entry_symbol` 与 `[libraries]` 的 `windows.release.x86_64` 必须存在且其 `res://` 引用解析到已打包的声明 DLL；注释、其他平台项、未加引号、路径逃逸与错误目标一律拒绝；只证明清单自身声明，不证明 PCK 内部）、缺失入口/依赖/清单/PCK 拒绝、与冻结生成成员（`connection.json`、schema、codebook、许可证、第三方声明）路径冲突的程序归档在入库与组包写字节前即被拒绝、归档摘要与上限、冻结完整包（程序字节与模式未改、公开配置位于 EXE 同级、schema/codebook/许可证/第三方声明、成员清单不自引用）、三种冻结模式共用同一构建、篡改与打包失败保持旧发行、真实 GUI 上传-批准-下载与撤权、Windows 完整原生当前发行门户失败关闭不生成 Web 入口，以及 macOS 依赖目录缺失强化（删除整个 Frameworks 目录同样拒绝）。构建门槛负向测试 `tests/test_phase03_windows_build.py`（14 项）同批加入：导出子进程写出看似有效产物后非零退出仍必须失败、引擎声明子进程写出有效 JSON 后非零退出仍必须失败、模板损坏或 `GEP_GODOT_TEMPLATES` 覆盖目录字节不符在复制/导出前拒绝、构建工具与验证工具共用同一固定校验、导入与导出重试各自有上限。既有 `tests/test_phase03_packages.py`（32 项）与 `tests/test_phase03_releases.py`（19 项）在同一批次回归通过；四文件命令合计 146 项通过。

真实端到端命令（`tools/phase03_verify_windows_package.py --verify`）：使用固定的本机官方 Godot 4.7.2 与官方 Windows x86_64 模板交叉导出真实 EXE（109,127,680 字节）、PCK、godot-sqlite v4.7 官方归档中的 Windows x64 DLL（3,381,248 字节）与 GDExtension 清单（程序归档 39,462,413 字节；EXE 每次导出字节不同，程序归档摘要因此每次变化，以描述与冻结清单实际记录的摘要为准，本地证据保留该次运行值），独立解析 PE 机器类型 0x8664、PCK 头部 4.7.2 与 GDExtension 的 Windows release 项；随后真实 Chrome 登录研究者 GUI 登记描述、上传该归档、批准发行，通过 GUI 链接下载冻结完整包两次（逐字节一致，约 39,498,150 字节；外层摘要记录在发行行与本地证据）与 sidecar（manifest、程序根内 `connection.json`、许可证、第三方声明），邀请持有 build scope 的第二成员下载成功并在 GUI 撤销后立即 403；解包后 EXE、PCK、DLL、GDExtension 清单与冻结 `connection.json` 位于同一程序根目录，冻结配置是新增成员而非覆盖程序字节，冻结包内入口仍为 PE32+ x86-64、PCK 仍为 4.7.2；冻结引擎声明与官方本地引擎现场导出逐字节一致；真实 HTTP 准入为冻结发行创建会话并写入数据库；门户与稳定入口把该发行呈现为独立程序且不含 `/run/.../web/index.html`，浏览不创建会话；篡改存储字节后完整包与全部 sidecar 下载 409、准入 409（`release_unavailable`）且会话数不变，恢复后同一发行再次逐字节下载。工具共 115 项检查 0 失败。同一批次后既有 macOS 完整包回归（`tools/phase03_verify_package.py --verify`）110 项检查 0 失败、全量非浏览器套件 278 项通过、4 项按需跳过。

2026-09-20 构建证据接受纠正（P0306WR）：`cross_build` 与引擎声明导出不再以“产物存在”判定成功，Godot 子进程必须以真实退出码 0 通过；导出失败时保留其完整日志（崩溃运行的产物一律不接受），用受支持的 `--import` 预热导入缓存且重试必须退出 0，之后才允许一次完整导出重试，仍以退出码 0 为准。冷缓存首次编辑器进程在写出导入缓存后于自身关闭阶段崩溃（本机 Godot 4.7.2/macOS）作为本地证据保留，`--rendering-driver dummy`、`--rendering-method forward_plus` 与直接冷导出均实测不能避免，因此不隐瞒、不把崩溃运行转绿。Windows 模板来源固定：官方 `Godot_v4.7.2-stable_export_templates.tpz`（SHA-512 与同一 release 的官方 `SHA512-SUMS.txt` 一致）内 `templates/windows_release_x86_64.exe` 的 SHA-256 `d34d36f3…0562`（109,268,480 字节）为固定值，`tools/build.py` 与验证工具共用 `tools/windows_template.py` 在复制/导出前校验，`GEP_GODOT_TEMPLATES` 覆盖目录存在但字节不符时直接失败而不是改用未校验模板。纠正后工作区真实运行：导出与引擎声明子进程退出码均为 0，工具共 118 项检查 0 失败（`windows_package_verify/20260920T133114Z`，本地忽略证据目录），构建门槛四文件命令 146 项通过。

**该轮 Windows 实机执行记为 NOT_RUN**：本轮是交叉构建与冻结包本机工程验证，未在真实 Windows 主机上运行任何程序，也没有用模拟程序替代真实构建；当时仍待真实 Windows 验收的 Windows 原生版本、实机三种准入、GEC 参与/收尾、断网补传、关闭重开、检查点恢复、失败数据导出与完整对账，已于 2026-09-21 完成真实实机工程运行（见文末）。包未做代码签名，SmartScreen/杀毒提示状态未知，未购买签名，也未自动关闭系统防护或添加信任。

## Phase 03 03F Windows 原生实机准备与探测（P0307W）

**本小节及其后的 P0307W/P0307WR/P0308 早期小节均为历史轮次记录：其中“实机未运行 / `BLOCKED` / 未接受实机结论”属于当时回合的状态。2026-09-21 的真实实机运行与当前状态见文末“Phase 03 03F Windows x64 真实实机运行（P0308 报告收尾轮）”。**

2026-09-20：新增准备门槛工具 `tools/phase03_verify_windows_native.py` 与实机 harness `tools/windows_native_harness.py`。定向命令 `pytest -q tests/test_phase03_windows_acceptance.py` 31 项通过，覆盖：描述缺少显式程序元数据/错平台/错引擎版本失败、程序归档篡改入口、路径逃逸、错误 PCK 版本与摘要不符全部拒绝、kit 按成员哈希可复现且 sidecar 不自引用、篡改 kit 成员即失败、凭据样式内容被拒绝、私有账号文件 0600 且不在 kit 内、不可达/无别名/非 Windows/无桌面主机记为精确 BLOCKED、可达 Windows 桌面记录机器事实、报告把准备状态与运行状态分开且人工验收固定 NOT_RUN、harness 无 kit 时失败并输出机器可读报告、指南与模板覆盖 WN01–WN06 且人类无需输入命令，以及严格最终门槛在“仅准备”“无报告”“探测未验证准备”三种情况下都拒绝、只有真实设备运行 PASS 才通过。

真实运行命令（`tools/phase03_verify_windows_native.py --verify-preparation --probe-device`）：98 项检查 0 失败，`preparation_status=PREPARED`。它核对本机官方 Godot 4.7.2 与官方 Windows 模板固定摘要（`d34d36f3…0562`）、程序归档与 `build/windows/descriptor.json` 记录摘要一致（`dd60833d…771d`）、归档内入口与依赖均为真实 PE32+ x86-64 映像、PCK 头部 4.7.2、GDExtension `windows.release.x86_64` 声明、Windows ZIP 路径规则；组装 kit（程序归档、描述、许可证、离线冻结完整包 10 个成员含 EXE 同级 `connection.json`，`integrity.json` 覆盖全部 7 个 kit 成员且不自引用）并写入私有合成账号文件（0600）。实机探测如实记录：授权主机可达时只记录通用事实（`Microsoft Windows 11 Pro`、x64、控制台会话 Active），`runtime_status` 保持 `NOT_RUN`；同一探测稍后（主机进入不可达状态）记录 `runtime_status=BLOCKED` 与精确的 TCP 22 不可达条件。探测成功或失败都没有改变运行状态结论；主机别名、地址与本机拓扑只保留在忽略目录的交接证据中，不写入公开文档。

同一主机上真实执行 harness：`--doctor` 26 项检查 0 失败（kit 与冻结包成员逐字节匹配、私有账号文件位置与内容、机器可读报告）；完整模式到达解压与启动阶段并通过冻结包解压到含中文与空格的路径（`…\GEP 原生测试 20260920\…`）、成员哈希、PE32+/PCK/依赖、冻结配置与 EXE 同目录等检查，随后主机离线，该次启动与后续界面结论**不记为通过**。因此：准备可交付，Windows 实机 WN01–WN06 仍为 NOT_RUN/BLOCKED，原设计者自主体验与独立 T17 仍为 NOT_RUN，Phase 03 未完成。证据在本地忽略目录 `local_data/phase03_20260920/windows_native_prep/`、`p0307w/`。

严格最终门槛（P0307W 版实现，已被下节的失败关闭门槛取代）：`tools/phase03_verify_windows_native.py --gate` 读取最新 readiness 报告，只有真实设备 `runtime_status=PASS` 才退出 0；准备完成但实机未运行输出 `PREPARATION_ONLY` 并非 0 退出，缺报告输出 `EVIDENCE_ABSENT`。`tests/test_phase03_windows_acceptance.py` 的 5 项门槛测试覆盖这些分支（含“探测未验证准备不算已准备”），防止把 Windows 工程准备当成必需运行验收。

P0306R 修正：03C 的真实 Chrome 合成包曾漏带随客户端发布的 `gec/shell.js`（`bridge.js` 导入该文件），导致桥接未加载、`data-admission` 缺失而使 Chrome 旅程在 `tests/test_phase03_releases_browser.py` line 274 失败；测试包补齐该客户端文件后，`GEP_T17_BROWSER=1` 下的同一命令 1 项通过（失败与通过日志在本地忽略证据目录）。该修正随“完整冻结包/GUI/准入”批次进入两笔原子补丁，并在隔离快照中重跑真实 Chrome 旅程验证。

## Phase 03 03F Windows 原生：真实生命周期 kit、可自动化工程路径与严格门槛（P0307WR 纠正）

2026-09-20：监督审查指出 P0307W 的功能缺口（离线 DIY 冻结与占位 API、只写标签的账号文件、只按镜像名启动/终止、只看 `runtime_status` 的宽松门槛），本批按审查结论纠正，并保留全部本地证据与“实机未运行”的结论。

准备改为真实平台生命周期：`tools/phase03_windows_kit.py` 在独立合成实例中经真实认证 HTTP 流程创建三个独立冻结研究/发行（无需 ID、名单 ID、ID+密码），三者共用同一不可变 Windows 程序构建；批准后再次改模式被真实拒绝（`policy_frozen_after_release`）。名单账号经真实名单导入创建，受限成员经真实邀请流程创建/激活/登录（仅 `study.view`/`session.recover`/`data.export_raw`，无批准/上传/配置权限），并记录三个模式的真实 HTTP 准入、会话落库与错误口令 `admission_denied` 拒绝。kit 内完整包与 sidecar 全部来自授权下载端点，逐字节等于数据库外层摘要、响应头摘要、包内清单与平台 sidecar；另有同一构建的第二个真实发行成为当前发行（供旧发行兼容验证）。`integrity.json` 是成员哈希清单而非签名；可用账号/口令与 owner 材料单独 0600 存放，不在 kit 内，并逐值比对确认 kit 不含任何私有口令值。平台侧 WN06 契约在本机真实实例上执行：存储包篡改与 sidecar 篡改后下载 409、准入 409 `release_unavailable` 且不新增会话、恢复字节后同一下载 200；缺声明依赖 422 `missing_dependencies`、错平台 422 `reserved_path`、无下载授权 403。

工程 harness（`tools/windows_native_harness.py --run`）改为自有 PID 安全启动与真实自动化流程：启动脚本把本次 `Start-Process` 的 PID 写入受控文件，核对可执行文件路径、控制台会话与启动时间，只检查/终止该 PID，不再使用按镜像名查找或 `taskkill`，默认不使用 `ExecutionPolicy Bypass`；`--doctor` 只做主机/kit/ACL 检查，绝不产生 WN01 结论。WN02–WN06 路径用程序自身的 `--synthetic-auto` 输入驱动真实导出 EXE 与真实原生存储（显式 `queue.sqlite`/`writer.sqlite`，只读 URI 与固定 SQL），配合作用域回环故障代理执行：三模式准入与完成、错误凭据/错误绑定拒绝、断网本地提交与进程终止、重连补传保持同一事件 ID、丢 ACK 后重试去重、检查点恢复与新 segment、短码+原设备证明、重放与过期拒绝、共享写锁、清理墓碑、仅数据恢复、无秘密失败导出与目标绑定、授权 JSONL 逐事件 ID/逐 `rt_ms` 原值对账、旧发行（已非当前发行）仍可准入完成。运行前置条件（作用域 SSH 隧道）缺失时全部运行项记 `BLOCKED`，不冒充通过。

严格门槛改为失败关闭：`--gate --gate-run <目录> --gate-prep <目录>` 只校验显式选择的运行目录，重算当前源码/构建/描述/harness 摘要与 kit 字节、读取绑定实例数据库，并独立复核 WN01–WN06 的原始证据文件（本地存储副本、授权 JSONL、失败导出、启动记录）。裸 `runtime_status=PASS`、doctor-only、陈旧 kit、缺少/跳过 case、宿主或架构不符、构建/发行不符、原始值不符、证据文件损坏一律 `EVIDENCE_INVALID` 且非 0 退出；证据有效但 case 未全过为 `RUNTIME_INCOMPLETE`；未显式选择运行目录为 `SELECTION_REQUIRED`；只有真实设备六项全过且证据绑定当前构建才是 `RUNTIME_PASS`。`tests/test_phase03_windows_acceptance.py` 40 项覆盖描述/归档负向、真实 kit 契约（无 DIY 冻结与占位、包来自下载端点）、私有产物 0600 与口令不入 kit、harness 自有 PID 契约与 doctor 不产生 WN01 通过、指南/模板契约、探测分支、报告状态分离，以及严格门槛的正向与 9 类负向（裸 PASS、doctor、错宿主/架构、陈旧构建/源码、篡改绑定包、损坏证据、原始值不符、跳项、失败 case）。

实机状态不变：授权 Windows 实机本轮仍不可达，`runtime_status` 为 `BLOCKED`/`NOT_RUN`，`preparation_status=PREPARED` 只代表准备与工程契约；P0308 的严格运行门槛仍必须保持阻塞，原设计者自主体验与独立 T17 仍为 `NOT_RUN`。已知限制如实记录：包未做代码签名，哈希清单不是签名；运行时不做包完整性校验，本地被篡改的 PCK 不会被程序自动拒绝。

## Phase 03 03F 集成纠正、隔离回归与设计者环境（P0308）

2026-09-21：按监督审查纠正 P0307WR 准备批次的缺陷，并完成 03F 可独立完成的集成准备。全部改动都在本机合成环境内执行；原设计者自主体验与独立 T17 仍为 `NOT_RUN`，真实 Windows 实机仍为 `BLOCKED`。

**验收编排改为命名证据**：`tools/phase03_acceptance.py --verify` 不再从无关运行整体继承结论。T25–T30 与受影响 T03/04/07/08/11/13/14/16/18–24 的每个条款都拆成具名子项，每个子项绑定命名的可执行证据（pytest 节点、Playwright 规格标题、壳/完整包验证器检查标签、Windows 准备/运行项）；缺失或跳过记为 `NOT_RUN`，失败记为 `FAIL`，只有全部子项通过才是 `PASS`。步骤失败、子项失败或步骤抛出异常都会写入报告并让整体保持 `FAIL`；只有"唯一缺失证据是外部 Windows 设备门槛"时整体才是 `BLOCKED`，永不 `PASS`。报告始终写出 `acceptance.json`/`ACCEPTANCE.md`，并单列保留的历史平台覆盖。

最近一次真实运行（2026-09-21，`local_data/phase03_20260920/p0308/acceptance_20260920T214604Z/`）：步骤
`integrity=PASS, pytest=PASS（383 项通过、0 失败、0 跳过）, shell=PASS（123 项检查 0 失败）, package=PASS（110 项检查 0 失败）,
browser=PASS（6 个规格 exit 0）, windows_preparation=PASS（229 项检查 0 失败、PREPARED）, windows_runtime=NOT_RUN`；T25–T30 与
受影响 T 项全部 `PASS`，唯一未通过子项是 T20 的“真实 Windows x64 原生运行”（外部设备门槛），整体 `BLOCKED`、退出码 1。
受保护旧验收库内容摘要运行前后一致（含 WAL sidecar，按真实 SHA-256 比较，不使用文件大小）。更早的失败运行
（`acceptance_20260921T080000Z/`、`acceptance_20260921T083000Z/`，pytest 步骤 FAIL）保留原样，不被后续通过覆盖。

**显式选择运行/准备对**：`--windows-run/--windows-prep`（或 `GEP_WINDOWS_RUN_DIR`/`GEP_WINDOWS_PREP_DIR`）必须成对给出；本地准备步骤与本轮新建的准备目录永不替代所选准备，也不自动发现"最新"目录。部分对、目录不存在、证据无效或与所选准备不匹配都是精确非 0 拒绝；未选择时运行项为 `NOT_RUN`。

**设计者冻结包与空环境改为真实平台产物**：`tools/phase03_designer_kit.py --prepare` 不再复制原始归档冒充完整包。它在新建隔离实例上经真实登记/上传/批准/授权下载取得 Web 构建原始字节与 macOS/Windows 完整包（含 `connection.json`、`artifact_manifest.json`、许可证与第三方声明 sidecar），逐包核对包内成员与清单、配置绑定本实例/研究/发行/模式、无凭据字段，并对包内成员做有界秘密/会话数据扫描；失败准备目录保留为证据。`--verify` 从停止状态走生成的 `START-HERE` 服务路径（生成的服务助手校验端口占用、PID 命令行与实例身份后才打开浏览器），执行真实 Chrome 登录，并只用包内 `connection.json` 运行冻结 macOS 包，把本地记录与实例数据库、授权 JSONL 导出按事件 ID 对账；生成文档引用的路径会被逐一验证存在。Windows 入口为工程侧 `--serve` 作用域反向隧道 + `OPEN-ADMIN-WINDOWS.cmd`，不改 DNS/防火墙/证书信任，设计者不需要输入命令。

**Windows 隧道与启动器**：`--serve-runtime` 真实建立 `ssh -N -R` 作用域反向转发（严格主机密钥、`ExitOnForwardFailure`、仅公钥、自有 PID），失败先清理再精确报错；`--stop-runtime` 只停止记录且命令行一致的自有进程。`--designer-launch` 在计划任务分支也等到自有程序退出记录后才释放任务与代理；kit 启动器改为校验 Python 真实版本（`sys.version_info>=(3,12)`）；`Verify` 派生路径统一基于解析后的绝对 root。

**测试数字（2026-09-21 P0308）**：Windows 工具链与平台契约 183 项通过（`tests/test_phase03_windows_acceptance.py` 88 项、`tests/test_phase03_windows_packages.py` 81 项、`tests/test_phase03_windows_build.py` 14 项）；03F 编排器契约 27 项通过（缺失证据、真实失败子命令/超时、伪造 PASS 摘要、平台缺口、选择对拒绝、步骤抛出时仍写出报告、全部子项通过但 step 未运行不得 PASS、同一需求同时缺本地与外部子项不算“仅外部”、未知 selector/check 状态不得通过、同长度受保护内容变更被检测、受保护/越界/符号链接/已存在证据根在写入前拒绝、重复运行保持先前文件路径与内容不变、含准备/证据标记的目录不被当作验收根）；设计者 kit 契约 33 项通过（env 引号与不执行文本、0600 原子写入与用后删除、保留根拒绝、成员扫描与有界预算、生成入口引用真实解释器、文档路径解析、Windows 入口绑定端口、受保护内容变更、生成的服务助手拒绝占用端口/复用 PID/畸形 PID/未就绪实例且不误杀、哈希清单格式与覆盖、必需成员不可被删除清单绕过、陈旧构建/旧源码绑定拒绝、双击入口伪造或失败不 READY、以及真实文件的启动前门槛负向：哈希篡改、缺失 manifest、移除包内 config、旧源码绑定、损坏 JSON 都只写 `NOT_READY` 且不调用任何启动路径）。编排器的 pytest 步骤合计 392 项通过（含上述三组工具契约）；定向壳/完整包验证为 123/110 项检查 0 失败，Windows 准备 229 项检查 0 失败（`PREPARED`，实机 `BLOCKED`）。

**P0308 第三轮本地收尾（2026-09-21）**：

- **验收状态严格收敛**：`tools/phase03_acceptance.py` 的整体结论现在要求每个必需 step 与每个子项都明确 `PASS`——任何 `NOT_RUN`/未知 step 状态都不得整体 `PASS`；步骤内部异常一律记为 `FAIL` 并保留完整报告（不再默认成 `NOT_RUN`/`BLOCKED`）；`_selector_status`/`_check_status` 只接受明确已知的通过枚举，未知 pytest/浏览器结果、未知检查状态与未知 Windows 运行项一律 `NOT_RUN`。“仅外部 Windows 缺失”改为逐个检查未通过子项，同一需求还有一个本地子项缺失时不再误称“仅外部”。
- **证据根只新建、从不原地清理**：每次运行使用新的唯一证据根（默认 `local_data/phase03_20260920/p0308/acceptance_<UTC 时间戳>`）。已存在的根、受保护根（旧验收库/卷、专用运行根之外的路径）与含符号链接组件的路径都在任何写入/清理/启动之前拒绝（非 0 退出），不再提供原地刷新；失败或中断的尝试保留全部文件、路径与日志（含数据库/WAL/SHM、JSONL 队列与命令日志），重试使用新根。该检查同时存在于程序入口与 `orchestrate` 内部，程序调用方不能绕过。
- **设计者双击入口真正执行且绑定检查**：`open-browser.command` 修掉了向上两级落到 `local_data` 再执行不存在解释器的错误，`START-HERE.command` 与它共用项目 `.venv/bin/python` 绝对路径与 POSIX 安全引用（空格/单引号路径可用）；`--verify` 从停止状态真实执行这两个生成的 `.command` 入口（不再只调 `service.py start`），核对入口自己记录的打开证据、只读健康检查与真实 Chrome 登录；stop 拒绝、启动失败或实例身份不匹配都记为失败且不继续打开可疑实例；生成服务助手对 `already_running` 但未就绪/身份不符、畸形 PID 记录（缺 pid/命令行）一律拒绝打开。
- **设计者 `--verify` 先核对再启动（失败关闭）**：manifest、哈希清单、秘密/成员扫描、配置与实例/研究/发行/模式绑定、生成引用与双击入口引用全部收敛为**启动前门槛**；任一失败或解析异常都写出明确的 `NOT_READY` readiness 证据并立即非 0 返回，不停止/启动服务、不打开浏览器、不运行冻结程序，因此已检测为损坏或含秘密的包不会被误执行。只有门槛全绿才进入真实 `START-HERE`/`open-browser` 执行、真实 Chrome 登录与冻结 macOS 包运行。
- **冻结证据覆盖与源码绑定**：`freeze_hash_mismatches` 对空清单、格式错误、重复条目、缺失/多余条目明确拒绝，`--verify` 要求 manifest 成员与哈希清单完整对应；新增 `freeze_binding_problems` 把冻结记录的源码摘要、程序归档/描述摘要与当前工作区比较，并要求包内 `artifact_manifest.json` 的程序摘要等于当前构建（zip 与其自报哈希相互一致也不再接受陈旧构建）；`member_scan_problems` 从平台与包内清单派生必需成员，删除 manifest 的 `required_members` 不能绕过平台必需清单/配置；README 明确哈希清单不是签名。刷新冻结使用新唯一目录，旧包保留。
- **Windows 运行助手生命周期**：`runtime.pid` 记录真实 Popen argv 与 OS 启动时间戳，`--stop-runtime` 只停止真实 argv 仍一致且启动时间未变的记录；畸形记录（空/缺失命令行）与 PID 复用拒绝并保留现场。已用真实本地进程验证“启动→记录→从新进程停止→端口释放”，并测 PID 复用不杀无关进程。
- **隧道就绪证明转发可用**：`Tunnel.wait_ready` 除 ssh 存活与本地目标可达外，还执行严格只读远端探针（同一授权别名的独立 SSH 会话，`powershell -EncodedCommand`，Windows 侧 `Host: admin.localhost` 取回 `/login` 原始字节），归一化 CSRF token 后与本机实例页面指纹比对；探针失败/异常/页面不一致一律不 `READY`，保持 `BLOCKED`，不修改 hostkey、全局 SSH、防火墙或 DNS。
- **可提交的独立原子分组**：隔离浏览器组已由监督者原子提交并推送为 `2e42779`（该组不依赖其他组，不再重复准备）。其余三组在忽略目录 `local_data/phase03_20260920/p0308/atomic_commits_p0308r/` 准备为顺序补丁（Windows kit/运行时/门槛 → 设计者冻结/kit → 编排器与公开状态），每组含实现、测试与工具引用的指南；`make_patches.py` 以 `2e42779` 为基线生成 `stage/base→a→b→c` 与三份补丁，`verify_snapshots.sh` 在**每次运行新建的唯一** `run_<UTC 时间戳>` 目录中顺序应用并通过组内定向测试、边界检查与最终快照比对；每个 patch/pytest/边界/diff 的退出码都写入该目录的 `exit_codes.txt` 并在任一失败时以非 0 退出（不再用会吞掉失败状态的 echo 汇总），旧快照与失败证据一律保留、不删除。补丁与逐文件 SHA256 见该目录 `README.md`、`patches.sha256`、`groups.json`；不 commit、不 push。

**一次真实 flake 及其修复**：编排器首轮运行记录到 `tests/test_phase03_releases_browser.py::test_actual_chrome_publication_and_stable_entry` 间歇失败（真实 Chrome 页面在 `shell.js` 请求上得到 400；服务端定位为测试线程写入的发行行对 live-server 线程短暂不可见，属共享内存 SQLite 测试库的跨线程可见性竞态，不是产品缺陷）。修复使用项目既有的真实文件测试库机制（`GEP_TEST_DB_FILE`，与并发探针相同），编排器 pytest 步骤显式设置该变量；随后同一 358 项套件连续两轮通过。首轮失败证据保留在 `acceptance_20260921T080000Z/`、`acceptance_20260921T083000Z/`，不删除。

## Phase 03 03F Windows x64 真实实机运行（P0308 报告收尾轮）

2026-09-21：真实 Windows x64 实机工程运行与集成门槛在同一个 P0308 内完成。全部使用隔离合成数据；准备主机与 Windows 之间是作用域反向 SSH 隧道（仅公钥、严格主机密钥；不改 DNS/防火墙/证书信任），主机名、地址与进程细节只保留在本机忽略目录的交接证据中。

- **准备**：`tools/phase03_verify_windows_native.py --verify-preparation --probe-device` → 229 项检查 0 失败、`preparation_status=PREPARED`，设备探针 READY。
- **实机运行**（同一冻结 kit、摘要绑定；三个冻结研究/发行共用同一不可变 Windows 构建）：kit 内 `tools/windows_native_harness.py --run` → **236 项检查 0 失败**、`runtime_acceptance=PASS`，WN01–WN06 全部通过（三种模式准入、错误凭据/错误绑定、断网本地提交与重连补传、丢 ACK 去重、进程终止重开、检查点恢复与短码+原设备证明、重放/过期拒绝、共享写锁、清理与仅数据恢复、无秘密失败导出、本地 SQLite／服务器数据库／授权 JSONL 逐事件 ID 逐值对账、旧发行兼容）。
- **严格门槛与集成**：`tools/phase03_acceptance.py --verify --windows-run <运行目录> --windows-prep <准备目录>`（显式选择运行/准备对）→ 全部步骤与子项明确 PASS（`integrity/pytest/shell/package/browser/windows_preparation/windows_runtime`，无 `step_errors`），整体退出 0；pytest 步 423 项通过（其中 `tests/test_phase03_windows_acceptance.py` 119 项）；`acceptance.json` 的 `windows.runtime_items` WN01–WN06 全 `PASS`。证据目录 `acceptance_20260921T052103Z/`，原始运行日志见忽略目录交接记录。
- **本轮真实缺陷与修复**（先在真机复现再修复）：launcher 在程序快速失败/提前退出时可能不写 `exit.json`（真机定位为进程已退出后设事件抛异常，修复为先容错开启事件、身份读取与无条件写出退出记录，不可得如实记为 `null`）；严格门槛的数值表示比较（整数化浮点视为同一数值，真实数值变化仍拒绝）、WN01 标题在 launch 记录缺失时使用已记录的进程观测、WN04「仅数据」标记从完整 stdout 文件读取；每个用例开始前只读探测作用域隧道，丢失时精确 `BLOCKED`；`snapshot_store` 保留原始副本+摘要并用 SQLite backup 派生自包含快照；授权导出先保存整份下载（含摘要）再派生 session 子集并复验派生关系；`--stop-runtime` 按整条 argv 严格比对（wrapper 的 exec/shebang 变换显式记录）。
- **保留**：attempt1（1 项失败）与 attempt2 现场、各轮 `--verify` FAIL/BLOCKED 证据、全部诊断日志原样保留；旧 `run.json` 未修改；受保护旧验收库内容摘要在运行前后一致。
- **范围**：交叉构建工具 `tools/phase03_verify_windows_package.py --verify` 仍不执行 Windows 程序；包未做代码签名，SmartScreen/杀毒未验；本地被篡改的 PCK 不被运行时自动拒绝（已知限制）；236/423 只代表该次运行作用域，不为之后的新改动背书。
- **设计者入口的工程侧绑定（同日只读实测）**：`tools/phase03_designer_kit.py --serve` 就绪（其就绪判定包含 Windows 侧只读远端探针），并从 Windows 侧只读请求 `localhost:<冻结端口>` 的 admin 登录页与 experiment 门户页，归一化后与本机同页逐字节一致；随后只停止本次自有的服务与隧道。Windows 上的实际双击与人工体验仍未运行。

## Phase 03 03F 设计者 Windows 交付轮（P0308R，同日）

2026-09-21：在 P0308 交付轮基础上补齐设计者 Windows 交付与**可重复的启动/停止生命周期**；全部为隔离合成数据，主机、地址与进程细节只保留在本机忽略目录的交接证据中，不写入公开文档。

- **真实缺陷与修复（先复现再修复）**：交付验证脚本的 `--serve` 停止路径原先只在收到 SIGINT 时优雅退出。当调用方以非交互/后台方式启动（子进程把 SIGINT 继承为忽略）时 SIGINT 成为空操作，调用方只能超时后强杀，导致自有作用域 `ssh -N -R` 子进程成为孤儿，Windows 侧冻结端口继续被占用。修复：`--serve` 的前台等待显式接管 SIGINT 与 SIGTERM（SIGINT 被继承为忽略时也会重新安装处理），调用方按 SIGINT→SIGTERM→（如实标记的）最后手段顺序停止，并**持续排空**子进程输出（有界日志；读取不再可能阻塞，就绪截止时间真实有效）；停止后按严格身份只回收本任务自己的遗留隧道客户端，本地与 Windows 侧冻结端口都必须确认释放，否则如实失败；非本任务占用只报告、不停止。
- **交付验证**：忽略目录中的交付验证脚本连续两次真实运行均 **37 项检查 0 失败、退出 0** —— 一次为前台启动，一次模拟非交互/后台启动使子进程继承 SIGINT 忽略；两次都干净启动、正常停止（退出码 0，无强杀），无遗留自有进程，本地与 Windows 侧冻结端口均已释放。失败轮次（原第 35 项）的门槛输出、每次独立原始报告与服务日志原样保留，不覆盖。
- **不变项**：已交付文件逐字节摘要、私有账号文件、冻结包与设计者实例数据在修复前后保持不变；工程侧只停止身份一致的自有进程。
- **边界**：工程侧启动成功不等于设计者体验通过；原设计者自主体验与独立 T17 仍为 `NOT_RUN`。

**状态边界**：Windows x64 实机工程门槛已通过；2026-09-21 人工测试已发生并形成修复反馈，修复后的完整回归与新一轮人工测试尚未进行（T17 保留编号，帮助/失败/未运行如实记录）。`tools/phase03_acceptance.py --verify` 未显式选择有效 Windows 运行/准备对时保持非 0；03F 工程产物已交付，但 **Phase 03 仍未完成**（完成本轮修复与工程回归后安排新的人工测试）。

## Phase 03 人工反馈修复 R08（概览 + 明细 ZIP 与电子表格查看准备）

2026-09-24：R08 在异常退出复核后于同一 Task 内完成修正并复验；证据全部来自隔离合成实例（pytest 临时数据库与回环 live server）与真实 Chrome。Required Verification 三条命令均前台真实退出：

- 两条独立复核用例 → **2 passed**（原两条独立 probe 现全部通过：混入另一视图缓存的有效 ZIP 不再被提供；raw-only 表单的唯一可选视图默认选中）。
- `GEP_T17_BROWSER=1 .venv/bin/pytest -q tests/remediation/test_p03r08.py tests/remediation/test_p03r07.py tests/remediation/test_p03r07r.py tests/test_phase03_ui_browser.py tests/test_t17_browser.py tests/test_phase03_queries.py` → **39 passed in 39.03s**（无跳过、无失败；含真实 Chrome 7 项：新增 raw-only 主按钮直点流程与 ZIP 失败反馈 2 项）。
- `.venv/bin/pytest -q tests/remediation/test_p03r08p.py` → **7 passed**（无跳过；缺省／空白设置不发起任何网络访问、Windows 记 `NOT_CONFIGURED`/`NOT_RUN`；已配置主机以 argv 调用 `ssh` 并保持 `BatchMode`、`ConnectTimeout`、`StrictHostKeyChecking=yes`，仅显式配置时才固定 `HostKeyAlias`，无 shell 插值；无 `GEP_TEST_WINDOWS_ROOT` 时不生成可执行 Windows 暂存计划）。
- `.venv/bin/python tools/remediation_spreadsheet.py --verify-preparation`（Windows 连接设置只经运行时环境传入，见维护者指南）→ 退出 0，`preparation_status=PREPARED`、`viewing_status=READY_FOR_R11_REAL_VIEW`、0 失败；只读探测事实：macOS `Microsoft Excel 16.113.2`；Windows 主机 `Microsoft Excel 16.0.17932.20910`；WPS 未发现；LibreOffice 只有注册表残留、无可执行文件，记为不可用。未配置 Windows 设置时同一工具仍独立完成本地合成 CSV 准备，Windows 记 `NOT_CONFIGURED`/`NOT_RUN`。

- **缓存绑定与容量**：每次 ZIP 下载都由该导出自己的冻结快照确定性重建，并只在与已存文件逐字节一致时才提供（缓存命中时服务同一个已打开并验证的句柄；替换时服务本次刚原子提交的字节），另一视图／语言／导出的有效 ZIP 放在缓存路径会被原子替换，绝不原样提供；最终压缩 ZIP 与解压 CSV 合计都 ≤64 MiB（`limits` 新增 `max_zip_bytes`），超限返回 413 `export_limit_zip` 且保留 JSONL；符号链接目录组件在写出前拒绝，`mkdir`／打开／写入／flush／fsync／rename 故障映射为 503 `export_generation_failed`（`jsonl_available: true`，无 Content-Disposition、无半包）；超时清理按设备+inode 只删除本次自己发布的文件，不删除并发的成功下载。
- **raw-only 浏览器工作流**：无身份映射读取权的账号在导出页只看到“不含名单 ID”选项且默认选中，直接点击主按钮即可创建并下载；真实 Chrome 保存的 ZIP 以独立 csv/json 解析器对账为 unmapped golden（无 `participant_code` 列、无未参与名单），页面显示下载已开始。对同一账号制造真实服务端 ZIP 失败（单元格超限 413 `export_cell_limit`）与真实网络失败（请求中止）时，页面如实显示错误代码、不出现“下载已开始”、不触发任何下载，并保留显式 ZIP 重试链接与可用 JSONL 链接（JSONL 下载成功且完整保留超限载荷）。
- **权限与列表投影**：ZIP 与 metadata 每次下载重验冻结所需权限（raw-only 读者对 identified 产物 403、撤权后整份拒绝）；列表行只在当前账号仍持有冻结权限时显示计数与链接；`default_view` 使被隐藏的 identified 选项不会抢走 raw-only 读者的选择。v1 字节与三种旧格式保持，v1 `format=zip` 明确 400，v2 单文件 CSV 仍 409 `export_zip_required`。
- **边界**：`READY_FOR_R11_REAL_VIEW` 只表示软件存在、可供 R11 门槛使用，不是软件通过；Windows Excel/WPS 实机查看与新一轮人工测试仍未运行（T17 保留编号，帮助/失败/未运行分别记录）。证据在本地忽略目录：浏览器轮 `local_data/phase03_remediation_20260923/p03r08/20260923T162447Z-ae70081b/`（含 `chrome_raw_unmapped.zip`、`chrome_raw_lossless.jsonl`、`chrome_download.zip`、`cache_binding.json`、`spool_safety.json` 等）与 UI 轮 `.../test_phase03_ui_browser/20260923T162447Z-56e9d04e/`；工具轮 `.../p03r08/20260923T162532Z-3bbeca5f/`；失败现场与旧证据不清理。以上为合成工程证据，**Phase 03 仍未完成**。

## Phase 03 人工反馈修复 R04 checkpoint 修复轮（2026-09-24）

2026-09-24：R04 在原 Task 内修复 Supervisor 独立复核的 4 项边界（删除后公开设置仍可写、新 operation 误匹配旧 proof、跨 scope 预览漏清、中断导出临时文件漏清），未重做 R08P（`b2b10c3` 保持），未改科学任务、旧迁移、旧验收源、队列/RUN_STATE/gate，未 commit/push。全部为合成数据库/合成夹具与隔离私有文件；Required Verification 两条命令均前台真实退出：

- 独立复核边界用例（内部留存；相应公开回归见下方命令） → **4 passed in 1.56s**（Supervisor 独立 probe 原样通过，未修改其任何期望）。
- `GEP_T17_BROWSER=1 .venv/bin/pytest -q tests/remediation/test_p03r04.py tests/remediation/test_p03r04_regressions.py tests/remediation/test_p03r06.py tests/remediation/test_p03r06r.py tests/remediation/test_p03r08.py tests/remediation/test_p03r02c.py tests/remediation/test_p03r02cr.py` → **68 passed in 80.01s**（无失败、无跳过；含真实 Chrome 7 项与真实多进程 probe 子进程 2 次）；附加全量 `.venv/bin/pytest -q` → **667 passed / 26 skipped**（跳过均为既有浏览器/外部环境开关）。
- **修复内容**：准入永久拒绝改为“原 operation + 精确 instance/研究/发行/构建绑定 + 原 proof”（墓碑新增专用域 operation-binding HMAC，迁移 `0014_deletion_operation_binding`），新 operation/绑定错配/未知对象统一 `admission_unavailable`；公开政策、当前发行、名单 preview/commit、权限矩阵、账号导入指向研究的行与 GUI POST 都在最终锁定事务内重查生命周期；跨 scope staged 预览按持久主键游标有界分页清理并在 `complete` 前独立核对；导出 spool 归属（`<id>.zip` 与 `.<id>.<16 hex>.tmp`）在删除导出行前持久化，缓存检查与发布放进同一数据库写锁事务，生成进程被 SIGKILL 后由清单覆盖。
- **真实多进程复验**（`tests/remediation/deletion_concurrency_probe.py`，真实 SQLite 文件与 OS 进程）：预检查→标记→提交的同步屏障被拒（`study_deleted`，`public` 保持 0、lifecycle `deleting`）；导出生成/删除竞争无可访问孤儿、删除后不再发布；被 SIGKILL 的生成进程无残留 spool 文件；`cleanup_deleted_studies --batch-size 1` 被 SIGKILL 后状态非 complete、重跑完成且依赖全清。
- **边界**：本段记录的是 R04 中间修复轮；最终工程验收见下方 R04R，人工测试未重跑，Phase 03 未完成；旧/新客户端三平台联测（旧包 403 暂停实机、新版解析 `study_deleted`）留 R11，不能冒充已跑。证据只写新的唯一目录 `local_data/phase03_remediation_20260923/p03r04*/<UTC+random>/`，失败现场与旧证据不清理。以上为合成工程证据。

## Phase 03 人工反馈修复 R04R 收尾轮（2026-09-24）

2026-09-24：R04R 修复 Supervisor guarded review 在既定 §D 契约下复验出的 2 项失败（保留动作失去研究关联、`--batch-size 1` 一次删完整份导出 spool），并补齐同模块两项一致性问题（非法清理参数非 0 退出、最小删除状态页不再显示研究 title）。只追加迁移 `0015_audit_study_uuid`，未改旧迁移、原验收库或科学任务；全部为合成数据库/夹具与隔离私有文件。Required Verification 两条命令均前台真实退出：

- 独立复核边界用例（内部留存；相应公开回归见下方命令） → **6 passed in 2.12s**（exit 0；Supervisor 独立 6 项边界原样通过。修复前同一命令为 **2 failed / 4 passed**，未修改其任何期望）。
- `GEP_T17_BROWSER=1 .venv/bin/pytest -q tests/remediation/test_p03r04r.py tests/remediation/test_p03r04.py tests/remediation/test_p03r04_regressions.py tests/remediation/test_p03r08.py` → **28 passed in 25.44s**（exit 0；无失败、无跳过，含真实 Chrome 3 项与真实多进程 probe 子进程 2 次）；附加全量 `.venv/bin/pytest -q` → **674 passed / 26 skipped**（跳过均为既有浏览器/外部环境开关）；`makemigrations --check --dry-run` → `No changes detected`（exit 0）。
- **修复内容**：新增可空 `Audit.study_uuid`（迁移 `0015_audit_study_uuid`），清理在同一有界事务为有研究外键的审计写入最小研究 UUID、置空外键并清除原始 before/after（mark 行保留最小 counts），历史已脱钩记录保持 NULL；导出文件清理按真实 unlink 计数（zip 与每个 tmp/链接各一项，`os.scandir` 有界内存扫描、不物化整个目录），未删完的 spool 归属留在持久清单，file-only 批次按真实文件进展继续（不因数据库游标不动而提前停住），`--batch-size 1`/`--max-batches 1` 后如实非 complete、重跑前进并最终完成；旧精确文件名 manifest 兼容，其他导出/未知文件/符号链接目标不误删；`cleanup_deleted_studies` 对 `--batch-size < 1` 与 `--max-batches < 0` 在清理前以非 0 退出；删除状态页只显示作业状态/时间/数量/错误，不显示研究 title。
- **真实多进程复验**（`tests/remediation/deletion_concurrency_probe.py`，真实 SQLite 文件与 OS 进程）：数据库阶段 SIGKILL 后状态非 complete、重跑完成且依赖全清（kill 触发改为任务真实推进后立即触发，避免落在“数据库行已删、complete 未写”的合法中间窗口）；新增文件阶段 SIGKILL：61 个 spool 文件（中断时剩余 60 个）在 `--batch-size 1` 下被中断时状态非 complete、未完成归属留在清单，重跑删净 owned 文件并保留 3 个无关文件；非法参数 `--batch-size 0`、`--batch-size -3`、`--max-batches -1` 真实退出码均为 1。
- **边界**：R04/R04R 已通过官方任务验证和关键边界复核；人工测试未重跑，Phase 03 未完成。旧/新客户端三平台联测（旧包 403 暂停实机、新版解析 `study_deleted`）留 R11，不能冒充已跑。证据只写新的唯一目录 `local_data/phase03_remediation_20260923/p03r04r*/<UTC+random>/`，失败现场与旧证据不清理。以上为合成工程证据。
