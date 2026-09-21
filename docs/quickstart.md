# 合成环境启动与演示

本版只允许合成工程验证。已实测 macOS 26.6.2 arm64、Chrome 152.0.7977.77、Godot 4.7.2；macOS 原生包为 universal（实际运行架构 arm64），Windows x64 已有真实实机工程运行（Windows 11 Pro 26200／AMD64，WN01–WN06 一次运行 236 项检查 0 失败）。Windows 实体 LAN 基础场景已实测；Docker、完整 LAN 故障矩阵、独立人类或真实研究验收尚未完成，详见 [验收记录](verification.md)。

## 服务端

需要 Python 3.12、uv、Node 24、pnpm。依赖版本和发行摘要分别锁在 `uv.lock`、`pnpm-lock.yaml` 和模板安装脚本中。

```sh
uv sync --frozen --python 3.12
pnpm install --frozen-lockfile
.venv/bin/python tools/dev_instance.py
.venv/bin/python tools/serve.py
```

`dev_instance.py` 只接受不存在的 `local_data/`，生成随机合成 Owner 密码、实例 UUID 和服务器秘密。凭据文件为 `local_data/dev_credentials.json`，权限 0600；用本地编辑器查看，不把内容粘进日志或仓库。再次启动只运行 `serve.py`，不会重建 Owner。数据库、包和秘密都留在被忽略的 `local_data/`。

在 Chrome 打开 `http://admin.localhost:8000/login`。实验使用 `http://experiment.localhost:8000`，两个 hostname 隔离管理 Cookie。服务仅绑定本机回环。LAN 辅助命令见文末；不要把 localhost 发给另一台电脑。公网部署未启用。

## 构建依赖

安装官方 Godot **4.7.2** 和匹配的导出模板。macOS 模板使用 universal 二进制；Windows x64 完整包需要同一版本官方模板目录里的 `windows_release_x86_64.exe`（`GEP_GODOT_TEMPLATES` 或标准用户模板目录均可）。构建与验证工具在复制或导出前核对模板字节的固定官方 SHA-256：`GEP_GODOT_TEMPLATES` 指向的目录若存在该文件但字节不符会直接失败，不会改用未校验模板。下载以下官方归档到任意本地临时目录，再运行安装器；参数替换为实际下载文件路径：

- [Godot 4.7.2 export templates](https://github.com/godotengine/godot-builds/releases/download/4.7.2-stable/Godot_v4.7.2-stable_export_templates.tpz)
- [Godot SQLite v4.7 demo archive](https://github.com/2shady4u/godot-sqlite/releases/download/v4.7/demo.zip)

```sh
.venv/bin/python tools/install_web_templates.py /path/to/Godot_v4.7.2-stable_export_templates.tpz
.venv/bin/python tools/install_sqlite.py /path/to/demo.zip macos windows
.venv/bin/python tools/build.py            # 默认 Web + macOS，保持既有命令不变
.venv/bin/python tools/build.py windows    # 新增：Windows x64 交叉导出
```

安装器先核对固定 SHA-256，再选择必要文件；不带平台参数时仍只安装 macOS 运行时，保持既有兼容。`build.py windows` 在复制 Windows 模板前同样按固定官方 SHA-256/大小校验（不符即失败）。Web 模板放在项目 `.godot/`，SQLite 框架／DLL 放在被忽略的插件 `bin/`。不需要重新克隆仓库。默认构建得到 `build/synthetic_web.zip`、`build/native/synthetic.zip`、原生 `.app` 及 `build/native/descriptor.json`；`build.py windows` 另外得到 `build/windows/GEP Synthetic Experiment/` 程序根目录、`build/windows/synthetic_windows.zip` 与 `build/windows/descriptor.json`。

## 研究者 GUI

1. 登录后创建研究，先设置无需 ID、名单 ID 或 ID+密码模式，以及每个名单 ID 的参与次数。名单每行一个 ID；密码模式用 Tab 分隔 ID 与密码。保留 `001`。首次批准发行后政策冻结。
2. 原生完整包路径（推荐）：粘贴 `build/native/descriptor.json` → 登记构建 → 在“上传完整原生程序包”选择 `.app` ZIP（`build/native/synthetic.zip`）→ 批准合成发行（服务端在此预分配发行 ID、冻结公开配置/schema/codebook/许可证与第三方许可声明并组装清单）→ 在发行列表点击“下载完整发行包”。解包后 `.app` 与 `connection.json` 同级，直接启动 `.app` 即使用包内默认配置，无需手工替换文件。包内 `THIRD_PARTY_NOTICES.txt` 随包冻结 Godot 引擎、godot-sqlite 与 SQLite 的许可证/版权声明，项目 `LICENSE` 只覆盖 GEP 自身代码。程序字节与可执行位原样保留，服务器不执行程序也不抓取外部地址。
3. Windows x64 完整包路径（同一实验构建，交叉导出）：先按上面的构建命令生成 `build/windows/descriptor.json`，粘贴登记 → 在“上传完整原生程序包”选择 `build/windows/synthetic_windows.zip` → 批准合成发行 → 下载完整包。解包得到一个程序根目录，里面的 EXE、PCK、原生 SQLite DLL 与 `connection.json` 都在同一目录：直接双击 EXE 即用包内默认配置，无需安装 Godot 或替换文件。Windows 包只接受描述显式声明的程序根、入口、依赖与 GDExtension 清单；路径穿越、盘符/UNC、反斜杠、原始空/`.`/`..` 组件、NTFS 非法字符 `<>"|?*`、大小写或 Unicode 归一化重名、文件与目录同名冲突、Windows 设备名、结尾点/空格、ADS、链接/特殊文件、脚本、未声明的可执行文件与错误架构全部拒绝。
4. 原生描述登记／兼容路径：只登记 `descriptor.json` 时不产生平台完整包，仍可批准发行并导出 `connection.json` 放到 `.app` 同级目录后自行分发。该路径不冒充平台完整下载。
5. Web 路径：上传 `build/synthetic_web.zip` → 验证成功 → 隔离预览（仅本地保存）→ 批准合成发行 → 开放招募 → 打开参与入口。预览没有管理 Cookie，也不创建正式采集会话。
6. 点击开始，按左／右方向键完成两个 trial。任务结束与服务器收齐是不同状态。断网时本地保存，恢复网络后补传。关掉程序后不保证上传；重开原存储环境后队列继续按原目标处理。
7. 研究状态页创建固定快照。JSONL 为原始记录，metadata 包含冻结 build/schema/codebook 与会话收尾信息；三个下载入口都重新检查权限。
8. CSV 是有限、保真文本格式：固定来源列加 `record_json`，其值为 `json:` 后接完整 JSON。它不展开或分析科学字段。Excel 使用 UTF-8、逗号、双引号文本限定符导入；读取程序去掉 `json:` 后解析 JSON。null、缺失、前导零、多响应不转换，公式样文本不求值。
9. 邀请按当前可委派权限签发，24 小时有效，可撤销。新账号自行设密码；已有账号先登录再接受，不改其密码。撤销成员权限立即影响后续下载。

Web 上传上限 128 MiB、解压上限 256 MiB、最多 256 个文件。原生程序归档上限 256 MiB、解压上限 512 MiB、最多 4096 个文件、压缩比 200；Windows 包另要求入口与每个声明的依赖都是真实 PE32+ x86-64 映像、PCK 头部记录固定引擎版本 4.7.2，且随包 GDExtension 清单的有效 `windows.release.x86_64` 项解析到已打包的声明 DLL（注释、其他平台项、路径逃逸与错误目标不算；与冻结生成的 `connection.json`/schema/codebook/许可证/声明路径冲突的程序归档也会被拒绝）；macOS 包必须真正带原生依赖（缺失整个 Frameworks 目录同样拒绝）；两种上传都显示传输进度，失败后重新上传，未完成包不进入 release，不覆盖旧构建。已发布的完整包与全部 sidecar（含 `THIRD_PARTY_NOTICES.txt`）每次下载都重新检查研究范围并核对存储字节，同一发行重复下载逐字节一致；篡改或丢失的包在创建会话前被拒绝。当前发行按平台呈现：Web 当前发行在门户给浏览器开始入口；完整原生当前发行只显示受控分发的独立程序说明，不生成无效 Web 路径，也不把受控下载变成公开下载；完整包缺失或校验失败时门户失败关闭，不显示误导入口。首版不支持断点续传或 CDN 自动转存。

## 同设备恢复

合成任务明确使用 `trial_boundary_v1`，检查点与依赖记录原子提交。研究人员在状态页为目标 session 签发 15 分钟一次性恢复许可；参与端填写该 session UUID 与许可。仍须持有原浏览器／原生存储中的私密证明，公开 UUID 不够。新参与锁住旧前台恢复，不展示旧答案或旧 ID。

状态页也可签发六位一次性恢复码：5 分钟有效、最多失败 5 次，同一会话签发新码会使旧码失效；参与端用本地私密证明和冻结绑定兑换，无需公开会话 UUID，错误码/证明/绑定返回同一拒绝且不泄露会话凭据。命名（ID＋密码）模式的研究可在同一设备上用原私密证明、名单 ID、密码与冻结绑定续接；仅公开 ID 不能恢复，标记为前台锁定的候选必须改用签发的码或许可。服务端恢复接口（`recovery_code/v1`、`recovery_named/v1`）已实现并有合成事务测试；GEC 参与壳已提供六位码输入与命名续接界面，候选只会在明确确认后继续（命名路径还可明确选择开始新的参与），原长许可仍作为高级兼容面板保留。

恢复保留 session，创建新 segment 与时钟 epoch；完整 trial 不重放。未声明兼容策略、存储版本不支持、找不到依赖或凭据无效时停止任务恢复并保留数据，需要研究人员处理。自动恢复仅覆盖此合成策略，不是通用实验恢复引擎。

只有完整声明已收齐、合法确认已本地提交、无 pending 和恢复依赖后，GEC 才清理原始副本、检查点及凭据。活动实验不能因批次 ACK 清理。导出不会删除服务器数据。原生和 Web 的清理各自使用本地事务；完成墓碑不含答案或凭据。

## 验证命令

保持服务运行，然后：

```sh
.venv/bin/pytest -q
.venv/bin/python tools/verify_browser.py
.venv/bin/python tools/phase03_verify_package.py --verify
.venv/bin/python tools/phase03_verify_windows_package.py --verify
.venv/bin/python tools/phase03_verify_windows_native.py --verify-preparation --probe-device
```

浏览器验收会先通过 GUI 登记新的合成发行，再运行实际 Chrome、独立原生程序和原生 SQLite 故障场景。完整包验收使用独立数据目录、数据库与端口：真实 GUI 上传实际 `.app` 归档并批准发行、下载完整包两次与 sidecar、邀请并撤销成员下载权限，把下载包解包后用包内默认配置启动真实程序，最后对账本地 SQLite、服务器数据库与授权 JSONL，并验证篡改包在创建会话前被拒绝。`phase03_verify_windows_package.py --verify` 用固定的本机 Godot 4.7.2 与官方 Windows 模板交叉导出真实 Windows x64 程序，先按固定官方 SHA-256 校验模板再复制，要求导出与引擎声明子进程真实退出 0（冷缓存首次编辑器进程在写出导入缓存后于关闭阶段崩溃时只保留其证据，受支持的 `--import` 预热重试必须退出 0，导出失败时最多再重试一次完整导出），独立检查 PE x86-64 入口/依赖、PCK 引擎版本与 GDExtension 声明，再走真实 GUI/数据库完整包闭环（上传、批准冻结、两次下载与 sidecar、门户平台呈现、篡改与恢复）。它**不执行** Windows 程序：交叉编译不冒充实机通过；真实 Windows 主机上的执行由 kit 内 harness 单独产生（2026-09-21 已完成 WN01–WN06 236 项检查 0 失败，见下），并以显式选择的运行/准备对作为集成门槛。

Windows 原生实机准备（03F）：`.venv/bin/python tools/phase03_verify_windows_native.py --verify-preparation --probe-device` 校验固定工具链、真实 Windows 程序归档与描述（PE32+ x86-64、PCK 4.7.2、ZIP 路径规则），并**用真实平台生命周期**组装交付 kit：独立合成实例、三个独立冻结研究/发行（无需 ID、名单 ID、ID+密码，共用同一不可变 Windows 构建，批准后模式不可改）、真实名单账号与经邀请流程创建的受限成员、真实登录与 HTTP 准入、经授权下载端点取得的完整包与 sidecar，以及平台侧 WN06 契约（篡改、权限、错平台、缺依赖）；`integrity.json` 只是成员哈希清单（不是签名），私有可用账号单独存放（0600，不在 kit 内）。报告把 `preparation_status` 与 `runtime_status` 分开，探测成功不改变 `NOT_RUN`。真实 Windows 主机上用 kit 内准备好的启动器（无需输入命令）运行 `tools/windows_native_harness.py --run`：自有 PID 的交互启动与窗口/路径/架构证据、三种模式、错误凭据/绑定、断网本地提交、重连补传、丢 ACK 去重、进程终止与重开、检查点恢复与短码+原设备证明、重放/过期拒绝、共享写锁、清理与仅数据恢复、无秘密失败导出、本地 SQLite/API/授权 JSONL 逐 ID 逐值对账、旧发行兼容；缺前置条件即 `BLOCKED`/`NOT_RUN`，不静默跳过、不冒充通过。界面体验按 `examples/synthetic_experiment/windows_native/WINDOWS_NATIVE_GUIDE.zh-CN.md` 自导执行，结果写入同目录 `WINDOWS_NATIVE_RESULT_TEMPLATE.md`。准备通过不等于实机通过，也不替代原设计者自主体验与独立 T17。严格集成门槛用
`.venv/bin/python tools/phase03_verify_windows_native.py --gate --gate-run <运行目录> --gate-prep <准备目录>`：只校验**显式选择**的运行目录，重算当前源码/构建/描述/harness 摘要、绑定 kit 字节与隔离实例数据库，并独立复核 WN01–WN06 的原始证据文件；只有真实设备全部通过且证据仍绑定当前构建才输出 `RUNTIME_PASS`，否则输出 `EVIDENCE_INVALID`/`RUNTIME_INCOMPLETE`/`EVIDENCE_ABSENT`/`SELECTION_REQUIRED` 并非 0 退出，不允许把准备、doctor 或裸 `PASS` 当阶段通过。2026-09-21：同一冻结 kit 已在真实 Windows 11 Pro 26200（AMD64，交互控制台会话）上完成 `--run`：**236 项检查 0 失败**、WN01–WN06 全 PASS，同批本机受影响套件 423 项通过。集成验收用 `.venv/bin/python tools/phase03_acceptance.py --verify --windows-run <运行目录> --windows-prep <准备目录>`（或对应环境变量）显式选择运行/准备对；未选择、证据无效或与所选准备不匹配时整体非 0 且不得用本机准备替代；原设计者自主体验与独立 T17 始终 `NOT_RUN`。运行前置条件是准备者提供的作用域 SSH 隧道（`operator/tunnel.cmd`）；缺它时运行项保持 `BLOCKED`，不修改防火墙、DNS 或证书信任。

所有账号和数据都是合成的；每次运行增加独立测试对象，不清空数据库。测试不代表独立研究人员验收。

单独验证无 GEC 的本地后端：

```sh
godot --headless --path examples/synthetic_experiment -- --local --synthetic-auto
```

返回 `local_committed` 和 `remote_status: unsupported`，不伪造服务器确认。普通运行去掉自动输入参数。

## 最小 Compose（隔离合成环境已实测）

`compose.yaml` 和 `deploy/Dockerfile` 已提供：外部指定卷、非 root、只读容器根目录、无额外 capability、显式实例标记。启动必须同时有数据库、秘密和匹配实例标记；不会自动创建空 Owner。镜像依赖用带哈希的 `requirements.txt`。

2026-09-12 已在专用 Lima 2.2.0 / Linux arm64 / Docker 29.1.3 / Compose 2.40.3 环境完成真实原生上传闭环、容器重启/替换持久化、Owner 保留、重复初始化拒绝及错卷拒绝。初始化和启动步骤见[Compose 指南](../deploy/README.md)。这不是正式部署或备份恢复验收。

## LAN acceptance helpers

`tools/serve_lan.py --ip <private-ip> --cert <certificate.pem> --key <private-key.pem>` starts the existing synthetic instance on port 8443 with direct TLS. Certificate trust must be explicitly arranged on the participant device; this command does not install certificates or change firewall rules. The experiment IP cannot access management routes. Keep the original loopback management service running separately.

`tools/verify_lan_evidence.py --study <study-uuid> --evidence-dir <private-evidence-directory> --ssh-host <authorized-ssh-alias>` compares saved recovery JSON against the guarded synthetic database and reads Windows/Chrome version metadata over an existing SSH connection. It does not read clipboard contents or arbitrary remote files, and cannot infer physical unplug/reopen actions. Keep evidence outside Git.

Web setup fields use native browser inputs for paste and IME. Recovery JSON contains the current authorized session's records, checkpoint and pending IDs, without its bearer token or private recovery proof. Exporting this file does not delete pending data. Unspecified task recovery policy permits authorized data recovery only; it does not resume trials.

独立研究者验收准备与逐步操作见[验收指南](researcher_acceptance.md)。为独立操作另建空的合成实例和测试账号，避免开发测试对象干扰；不重新初始化已有数据目录。

## 03F 集成编排与设计者环境（工程准备）

- `.venv/bin/python tools/phase03_acceptance.py --verify`：受影响集成矩阵的编排器（测试工具，不是另一个 agent 循环）。
  它先做完整性/迁移覆盖检查并记录受保护旧库的前后摘要，再依次运行真实 Web/macOS 壳回归（`tools/phase03_verify_shell.py --verify`）、
  完整包检查（`tools/phase03_verify_package.py --verify`）、**新隔离实例**（端口 >= 8040，独立数据目录/数据库/密钥）上的
  受影响浏览器规格，以及 Windows 准备门槛与实机探测；结果写入 `<证据目录>/acceptance.json` 与 `ACCEPTANCE.md`，
  逐项列出 T25–T30 与受影响 T03/04/07/08/11/13/14/16/18–24 的 `PASS`/`FAIL`/`NOT_RUN`。
  整体 `PASS` 要求每个必需 step 与子项都明确通过；步骤内部异常一律 `FAIL`；只有“唯一缺失证据是外部 Windows 设备门槛”
  才是 `BLOCKED`，缺失真实 Windows WN01–WN06 证据时整体必须非 0；原设计者自主体验与独立 T17 始终 `NOT_RUN`。
- 浏览器规格只通过环境变量连接目标（`GEP_ISO_ADMIN_URL`、`GEP_ISO_EXPERIMENT_URL`、`GEP_ISO_USERNAME`、`GEP_ISO_PASSWORD`、
  `GEP_ISO_CONNECTION`、`GEP_ISO_DB`）；缺失时规格直接报错，不会静默连到 8000 开发实例或旧验收库。
- `.venv/bin/python tools/phase03_designer_kit.py --prepare`：在新建隔离实例上经真实登记/上传/批准/授权下载取得 Web 构建原始字节与 macOS/Windows 完整包（含 `connection.json`、`artifact_manifest.json`、许可证与第三方声明 sidecar）与哈希清单，并新建空的合成设计者环境（独立实例、端口 >= 8040、口令与种子名单账号单独 0600、拒绝覆盖受保护目录）。设计者双击 `START-HERE.command` 即可打开正确后台地址（生成的服务助手先校验端口占用/PID/实例身份），不需要输入命令；`--verify --root <环境>` 会**先做启动前门槛**（manifest/哈希清单/成员扫描/配置与实例·研究·发行·模式绑定/生成引用），任一失败或解析异常只写出 `NOT_READY` 证据并非 0 返回，不停止或启动服务、不打开浏览器、不运行程序；门槛全绿才从停止状态**真实执行两个生成的 `.command` 入口**（`START-HERE.command` 与 `open-browser.command`）、核对入口自己记录的打开证据并做真实 Chrome 登录，且只用包内 `connection.json` 运行冻结 macOS 包、与实例数据库和授权 JSONL 按事件 ID 对账；stop 拒绝/启动失败/身份不符时不会继续打开可疑实例。冻结哈希清单要求与 manifest 成员完整对应（空清单/缺行/重复/陈旧构建或旧源码拒绝），必需成员由平台与包内清单派生；交付时必须同时给出设计者环境目录与冻结包目录。Windows 设计者由维护者先 `--serve` 建立作用域反向隧道（就绪必须通过严格只读远端探针证明转发可用），再双击 `OPEN-ADMIN-WINDOWS.cmd`（不改 DNS/防火墙/证书信任）。设计者的 Windows 原生参与入口是交付目录中的 `RUN-DESIGNER-WINDOWS.cmd`：先校验交付包摘要与包内清单逐成员，再解压到独立目录并用本轮独立本地存储启动真实程序，绑定包内冻结设计者实例/研究/发行/模式；工程 kit 的 `designer-mode-*.cmd` 只用于工程验证，不是设计者入口。私有账号在交付目录与参与者包之外的受保护文件（Windows 真实 ACL 限制到指定用户与必要系统/管理员账号，macOS 0600），交接只记录路径。交付逐文件摘要、来源与按平台选取范围见交付清单。清单与结果模板见[原设计者自主体验](designer_acceptance.md)；人工状态为 `NOT_RUN`。
