# Windows x64 原生完整包验收要求

状态：2026-09-20用户确认纳入Phase03D/03F必需范围。当前真实工程数字（2026-09-21 P0308）：Windows 工具链与平台契约测试 `pytest -q tests/test_phase03_windows_acceptance.py tests/test_phase03_windows_packages.py tests/test_phase03_windows_build.py` **183 项通过**；准备/实机探测命令 `tools/phase03_verify_windows_native.py --verify-preparation --probe-device` 在 03F 编排运行中为 **229 项检查 0 失败**、`preparation_status=PREPARED`、`runtime_status=BLOCKED`（外部设备当前不可达；具体主机与地址只保留在本机忽略目录的交接证据中，不公开）。但**冻结程序在真实 Windows 上的完整 WN01–WN06 执行仍未通过**，实机验收状态为 NOT_RUN/BLOCKED（未接受任何启动或界面结论）。本文是验收要求，不是通过报告。macOS原生成果和Windows浏览器结果分别保留。原设计者自主体验与独立 T17 均为 NOT_RUN。

研究者目标：上传Windows构建、批准并下载冻结完整包，直接发给被试。开发者需交付EXE、实验资源、GEC与Windows原生存储依赖；包由平台绑定公开配置、完整性清单和许可声明。维护者在独立合成环境核对包及目标实例，保留旧会话与失败现场。

1. 从后台下载完整包，在真实Windows x64电脑解压直接启动，无需Godot或修改配置；核对版本、哈希与中文/空格路径。
2. 用同一构建完成无需ID、ID及ID+密码三种参与方式；错误凭据拒绝，前后参与界面状态准确。
3. 验证本地保存、断网期间继续已准入实验、重连补传、丢ACK不重复、关闭重开及进程终止后保留队列。
4. 验证检查点恢复、短码与原设备私密证明、过期/错误/重复码拒绝；已结束待传只补传，不重做任务；共享设备锁与已清理状态准确。
5. 验证上传失败数据导出不含秘密且不删除队列，本地SQLite、服务器记录及授权导出逐ID/值一致。
6. 验证完整包及附件校验、篡改/撤权拒绝、缺依赖和错平台处理，原生入口不显示无效Web启动链接，旧发行/会话不改写。

完整包约定（工程实现）：Windows 描述显式声明程序根目录、入口、声明的原生依赖与 GDExtension 清单；服务端不执行程序，只核对真实 PE32+ x86-64 映像、独立 PCK 的引擎版本、清单对依赖的声明以及 Windows ZIP 路径规则。冻结包把 `connection.json` 放在 EXE 同级，程序按可执行文件同级查找；包未做代码签名，SmartScreen/杀毒提示状态未知（不得自动关闭防护或添加信任）。

## 2026-09-20 准备、真实生命周期 kit 与严格门槛（P0307WR 纠正）

上一轮（P0307W）的准备 kit 用本地离线冻结与占位 `api_url` 组装，账号文件只写标签，严格门槛只读取最新
readiness 报告的 `runtime_status`。P0307WR 已按监督审查纠正，全部保留本地真实证据、未接受任何实机结论：

- **准备改由真实平台生命周期产生**：`tools/phase03_windows_kit.py` 在独立合成实例（独立数据目录/数据库/密钥/回环端口）中，经真实认证 HTTP 流程创建**三个独立冻结研究/发行**（无需 ID、名单 ID、ID+密码），三者共用同一不可变 Windows 程序构建；批准后再次修改模式被真实拒绝（`policy_frozen_after_release`）。名单账号经真实名单导入创建，受限成员经真实邀请流程创建、激活并登录（仅 `study.view`/`session.recover`/`data.export_raw`），并记录三个模式的真实 HTTP 准入与错误口令拒绝。kit 内每个完整包与 sidecar 都是**从授权下载端点真实下载的字节**，与数据库记录的外层摘要、清单程序摘要、包内清单与 sidecar 逐字节一致；不再有 `REPLACED_AT_RELEASE_TIME` 占位、不再有本地重新打包。`integrity.json` 只是成员哈希清单（不是签名）。可用账号、口令与 owner 材料单独 0600 存放，不在 kit 内、不进日志、不随参与者包分发。
- **WN02–WN06 可自动化工程路径已实现**：`tools/windows_native_harness.py --run` 用程序自身的合成自动化输入驱动**真实导出的 Windows EXE** 与真实原生存储（显式 `queue.sqlite` + `writer.sqlite`，只读 URI、固定 SQL）对真实 API/数据库执行三种模式、错误凭据/错误绑定、断网本地提交与重连补传、丢 ACK 去重、进程终止与重开、检查点恢复与新 epoch、短码+原设备证明、重放/过期拒绝、共享写锁、清理墓碑、仅数据恢复、无秘密失败导出与目标绑定，并用**授权 JSONL 导出**逐事件 ID、逐 `rt_ms` 原值对账；另含旧发行兼容（同一构建的第二个真实发行成为当前发行后，旧发行仍可准入与完成）。故障注入是作用域回环代理（不修改冻结配置）。
- **启动安全**：不再按镜像名查找或终止进程。启动脚本把本次 `Start-Process` 的 PID 写入受控文件，核对该 PID 的可执行文件路径、控制台会话与启动时间，只检查/终止该 PID；无交互控制台会话时明确失败；PowerShell 路径经单引号安全转义；默认不使用 `ExecutionPolicy Bypass`；私有账号文件的 ACL 用 `icacls` 检查并在过宽时收紧到当前用户（或如实记为未验证）。
- **严格门槛改为失败关闭**：`--gate --gate-run <目录> --gate-prep <目录>` 只校验**显式选择**的运行目录，重算当前源码/构建/描述/harness 摘要与 kit 字节，读取绑定实例数据库，并独立复核 WN01–WN06 的原始证据文件（本地存储副本、授权 JSONL、失败导出、启动记录）。裸 `runtime_status=PASS`、doctor-only、陈旧 kit、缺少或跳过的 case、宿主/架构/构建/发行不符、原始值不符、证据文件损坏一律输出 `EVIDENCE_INVALID` 并非 0 退出；证据有效但 case 未全过输出 `RUNTIME_INCOMPLETE`；未显式选择运行目录输出 `SELECTION_REQUIRED`。只有真实 Windows x64 设备六个 case 全部通过且证据仍绑定当前构建才输出 `RUNTIME_PASS`。
- **实机状态不变**：授权 Windows 实机（设备与地址由维护者在外部准备时提供，不写入公开文档）在本轮探测中仍不可达，运行项保持 `NOT_RUN`/`BLOCKED`，`preparation_status=PREPARED` 只代表准备与工程契约，P0308 的严格运行门槛仍必须保持阻塞。原设计者自主体验与独立 T17 仍为 `NOT_RUN`。
- **边界与限制**：包未做代码签名；哈希清单不是签名；运行时不做包完整性校验，本地被篡改的 PCK 不会被程序自动拒绝（已在指南与结果模板中写明，不作为通过项）。运行前置条件是准备者提供的作用域 SSH 隧道或等价已验证 LAN 端点，不修改防火墙、DNS 或证书信任。

## 2026-09-21 P0308 集成纠正（工程准备完成，实机仍未运行）

- **隧道端点契约纠正**：生成的 `operator/tunnel.cmd` 以前把 Windows 监听端口写成冻结 API 端口（如 8074），而 harness 与故障代理期望隧道监听端口（`port+100`，如 8174）。现在 kit 明确记录 `listen=port+100`（Windows 侧）→ `target=port`（准备主机侧），方向为**反向**：准备主机在 `--serve-runtime` 时向 Windows 建立作用域 SSH 连接，Windows 侧不需要人工填写主机名或输入命令。`runtime_prerequisite` 与 `pending_reason` 如实记录准备状态；空白主机名不再被表示为"可以双击"。`tools/phase03_verify_windows_native.py --verify-preparation` 本轮记录 `runtime_prerequisite=READY`，但外部主机 TCP 22 不可达，运行时保持 `BLOCKED`（主机别名与地址只保留在本机忽略目录的交接证据中）。
- **启动器可搬移**：以前生成的启动器引用 kit 外的兄弟 `tools/` 目录，复制 kit 与私有账号到新机器后目标缺失。现在 harness 随 kit 一起冻结在 `operator/harness/windows_native_harness.py`，启动器只引用 kit 内相对路径，并显式检测 Python 3.12 运行时前置（缺失时明确失败，不静默跳过）；准备工具会把 kit + 私有账号复制到全新目录逐项验证目标齐全。
- **设计者启动连接生命周期**：`--designer-launch` 现在先检查作用域隧道，再启动作用域故障代理（冻结端口 → 隧道端口），代理生命周期覆盖整个程序使用过程，程序退出后关闭自有 launcher/脚本并停止代理；隧道不可达时明确报告外部前置未满足，不启动程序。这是测试环境准备，不是通用 Launcher，也不产生 WN01 结论。
- **严格门槛跨源对账**：`--gate` 不再只信任运行报告里的摘要/计数/哈希，而是独立读取本地原生 SQLite 快照、授权 JSONL 与绑定实例数据库，按 session/event ID 和整条事件信封的规范 JSON（含嵌套 payload、原始单位/来源、浮点值）逐条对账；缺失、额外、重复 ID、错会话/错发行绑定、坏 JSON/SQLite 都是精确的非 0 拒绝。负向测试会真实修改 SQLite/JSONL 字节并同步更新表面摘要与文件哈希，门槛仍必须拒绝。
- **前置失败不可被覆盖**：`--run` 不再在启动成功后清空 WN01 的 kit/ACL/结构前置检查；任何前置失败都会保留并使运行时用例停止，避免"坏 kit + 成功启动"被判通过。异常清理只释放自有 launcher/任务/脚本，不按进程名终止任何用户程序。
- **仍未运行**：真实 Windows x64 上的 WN01–WN06、原设计者自主体验、独立 T17 全部为 `NOT_RUN`；`tools/phase03_acceptance.py --verify` 在缺实机证据时整体保持非 0。Windows 仍是设计者自主体验的主要环境。

## 2026-09-21 P0308 第二轮纠正（隧道生命周期、运行期启动器与接受门槛）

监督复核指出上一轮仍有未完成的本地工程。以下均已实现并以真实进程/文件验证，实机结论不变（未运行）：

- **`--serve-runtime` 真正建立作用域反向隧道**：以前生成的 `tunnel.cmd` 与说明声称准备机会自动建立反向 SSH 连接，但 `serve_runtime` 只启动本地实例。现在 `serve_runtime` 按 `runtime.tunnel` 建立 `ssh -N -R <listen>:127.0.0.1:<target> <授权别名>`：`BatchMode=yes`、`ExitOnForwardFailure=yes`、`StrictHostKeyChecking=yes`、`ConnectTimeout=8`、仅公钥认证、`ServerAliveInterval`；就绪判定要求自有 ssh 进程**存活超过连接超时**且本地目标端口可达，连接仍挂起时绝不报告就绪，失败时先停止自有实例再以精确原因非 0 退出（绝不留下"看起来就绪"的服务）。隧道与实例的 PID/命令行写入 `tunnel.pid`/`runtime.pid`；`--stop-runtime` 只停止记录中且命令行仍一致的进程，PID 被复用时拒绝而不是误杀。不修改服务器/全局 SSH 配置、防火墙、DNS 或证书信任。
- **隧道就绪必须证明转发可用**：以前的 `wait_ready` 只等自有 ssh 存活超过连接超时并确认 Mac 本地端口可达，一个仍挂起或远端未建立转发的连接也可能被判就绪。现在就绪还会经**独立的严格只读远端探针**（同一授权别名的另一条 SSH 会话，`powershell -EncodedCommand`，不写任何文件、不改配置）：Windows 侧请求 `http://127.0.0.1:<listen>/login`（显式 `Host: admin.localhost`，不改 DNS），取回原始字节；Python 侧去掉每次请求的 CSRF token 后与本机实例同页指纹比对，状态码非 200、探针报错、页面不一致或不可解码都不得 `READY`。ssh 进程、命令行与启动时间戳写入 `tunnel.pid`；`--stop-runtime` 只停止记录中**真实 argv 仍一致且启动时间未变**的进程，畸形记录（缺 pid/命令行为空）与 PID 复用一律拒绝并保留现场，不误杀无关进程。
- **记录真实进程身份而不是猜命令**：`serve_runtime` 以前写入简化的 `gunicorn --bind <port>`，与真实 Popen argv 不连续而必然拒绝停止自有服务。现在 `runtime.pid` 保存真实 argv 与 OS 启动时间戳，命令行比较按空白归一化后的有序连续匹配；本地真实启动→记录→从新进程 `--stop-runtime`→端口释放，以及 PID 复用/畸形记录不误杀均有测试。
- **运行期启动器生命周期**：`--designer-launch` 在无 launcher 句柄的计划任务分支也会等到自有程序退出记录（脚本写出的 `exit.json`）后才释放任务与故障代理，不再在程序仍在运行时立刻关闭；交互分支仍等待自有 launcher 进程。等待只观察自有对象，不探测或终止任何外来进程。
- **Python 前置校验真实版本**：kit 启动器不再用 `python -c "import sys"` 接受任意解释器，而是检查 `sys.version_info[:2]>=(3,12)`；可搬移检查同时拒绝缺少版本校验的启动器。
- **`Verify` 派生路径一致解析**：`tools/phase03_verify_shell.py` 的 `Verify.__init__` 让 `data`/`evidence`/`native`/数据库路径全部基于已解析的绝对 root，避免相对 root 时传给导出程序的 `--config` 不可读（P0308 实际出现过的 `invalid_configuration` 回归）。
- **数字更新（2026-09-21 P0308 第三轮）**：Windows 工具链与平台契约 `pytest -q tests/test_phase03_windows_acceptance.py tests/test_phase03_windows_packages.py tests/test_phase03_windows_build.py` **183 项通过**（本轮新增 10 项：只读远端探针命令/失败与异常不 READY、登录页归一化与本地指纹、真实本地启动→记录→新进程停止→端口释放、畸形 PID 记录拒绝、PID 复用拒绝、`_record_pid` 不写畸形记录）。

记录实际Windows、Godot/GEC/SQLite版本、包摘要和证据。Windows实机不可用则记为待验或受阻，不能用交叉编译、Mock或Mac测试替代，不得宣称Phase03全部完成。不自动关闭系统防护或修改证书信任；签名/SmartScreen提示按实记录。

工程通过后由原设计者在Windows自主体验，确认可交付后再进行独立T17。首次准入仍需联网；程序退出后不保证持续上传。真实部署和真实研究不因本范围变更自动启用。
