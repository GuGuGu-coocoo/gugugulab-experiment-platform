# 实验开发者说明

实验科学逻辑继续通过通用数据模块记录事件/checkpoint并结束任务，固定release/session和本地待上传数据不随新配置重新绑定。本轮后台修复不改变事件协议、原生存储格式或科学任务；Windows x64 目标只是同一场景的导出平台，`task.gd` 与事件/检查点语义不变。

Web托管与预览统一使用维护者配置的GEP_PUBLIC_API（含非默认端口）；原生的connection.json仍是描述登记／兼容路径。GEC 统一准入/恢复/收尾壳已提供：同一 Web/macOS 构建按冻结配置里的 `mode`（`anonymous`/`id`/`password`）动态渲染入口，公开配置另带 `shell_capability`（未知能力直接失败关闭，旧配置走兼容字段集）。原生完整下载套件已按有界上传实现：登记原生描述后上传实际的 `.app` ZIP，批准发行时服务端冻结公开配置/schema/codebook/许可证与 `THIRD_PARTY_NOTICES.txt` 依赖声明并组装带成员哈希的清单，外层摘要只存数据库；发行列表提供完整包与 sidecar 下载，包内 `connection.json` 与 `.app` 同级即可直接启动。当前发行按平台呈现：只有真实 Web 产物才给浏览器入口，完整原生当前发行只显示受控分发说明，不生成无效 Web 路径。Windows x64 完整包沿用同一数据层与参与壳：描述显式声明程序根、入口、依赖与 GDExtension 清单，服务端校验真实 PE32+ x86-64 映像、PCK 引擎版本与 Windows ZIP 路径规则（盘符/UNC、反斜杠、大小写/Unicode 归一化重名、设备名、结尾点/空格、ADS、链接与脚本），冻结包内 `connection.json` 与 EXE 同级，程序按既有可执行文件同级查找逻辑读取；未知平台失败关闭，不回退 Web 或旧分支。

当前接入及构建见[启动指南](quickstart.md)与[协议](protocol.md)。不开发通用Runner；刺激、随机化、试次与科学时序由实验负责。

参与壳的准入与本地测试契约：准入成功（含仅数据恢复）后前置表单（名单/密码/恢复字段与开始按钮）立即隐藏并禁用、焦点与回调移除，不能重复准入或遮挡科学刺激；保存失败或取消确认不会提前锁死入口，但已退役的入口即使后来进入错误状态也不能重新提交。本地测试（原生 `--local` 的独立 `data/local_backend.gd`，以及 Web 隔离预览 `preview:true`）只有记录提交与完成集合两笔都持久化后才显示“本地测试完成”，任一写入失败报 `storage_error` 并保留本机记录；保存结果与完成状态独立，未完成时只提示结果已保存。结果 JSONL 只由明确点击保存/下载：原生先在所选目录写同目录临时文件、核对完整内容后原子替换旧目标，打开后写入/flush 失败报 `results_export_unavailable` 且保留旧文件与本机记录，反馈与打开目录指向最终文件的父目录；Web 下载在 SDK 内校验，只导出本机当前未锁定的本地测试会话，缺失/已清理/锁定/远端会话一律拒绝且不产生下载。全程无网络请求、不伪造远程回执。

收尾状态呈现契约（R09C，计数与导出边界经 R09CR 补修）：参与壳每次从持久 summary（`kind`、`complete_ack`、`pending`、记录数与 `delivery`）重建显示，不只看内存 state 字符串，同 state 下的计数/回执/墓碑变化仍会公告。补传次数始终等于持久 `retry_failures`：初次正常发送失败不算补传、显示通用重试文案（无计数）；之后的第 1/2 次补传失败显示“正在补传”及与持久计数一致的数字，第 3 次连续补传失败且本机仍有合法未锁记录时显示“重试上传”和“导出失败数据”；真正有进展的一轮不计数，ACK 重置计数后下一次计数从 1 开始。失败面按持久状态显式排除已收齐的墓碑、已存完成回执（`complete_ack`/`remote_acknowledged`）与前台锁定会话，不能只靠计数恰好为 0；重开的壳不会为已收到的数据显示旧上传失败或旧答案。本地保存失败只报本地错误，不显示“已完成/等待上传”。`study_deleted` 永久删除终止立即停止发送、禁用重试但保留合法本机导出；手动重试不清计数、不解除安全暂停，仍尊重持久退避/`Retry-After`。失败数据导出由原生 GUI 保存对话框与自动化入口共用同一条原子路径：同目录临时文件完整写入、flush、核对回读后才一次替换目标；打开/写入/flush/核对/替换任一步失败都删除临时文件、保留旧目标与本机队列，界面只显示真实结果（已导出路径、明确错误或取消），导出文档不含 proof/token/密码。浏览器 bridge 对每次完成的调用都给显式回复（成功 flush 原本解析为 undefined，旧行为与“桥没有回复”无法区分，会让壳永久忙碌）。

## 实验数据默认字段薄封装（R10）

`data/experiment_data.gd` 保留原有四参数 `record(kind, payload, schema, observed)`，并增加一次声明、重复简短记录：

```gdscript
data.configure_defaults({"event_type": "exp.rt", "schema": {"id": "rt", "version": "1"},
    "fields": {"trial_id": func(): return trial.id, "choice": func(): return trial.choice}})
var result = await data.record()                       # 读取已声明字段
var special = await data.record("exp.other", {"note": "explicit"}, {"id": "other", "version": "1"})
```

- `fields`（`{字段名: Callable}`，按声明顺序逐一显式求值）与 `snapshot_provider`（一个返回 Dictionary 的 Callable）互斥；声明之外的字段、场景树节点、窗口内容和身份秘密都不采集。
- 显式 `kind`/`schema` 优先于默认；`payload` 非 null 时完全替换 provider/fields 输出，不隐式合并；`payload` 为 null 才读取已声明来源。
- `observed` 只用调用方显式传入的值，不推测科学时钟；为空时事件不带 `observed_time`。
- 复制发生在 `record()` 调用时、`await` 之前：之后修改节点或字典不会追改历史记录。provider/字段回调必须返回普通有限 JSON 值（String 键 Dictionary、Array、字符串、有限数字、布尔、null）。
- 深度与循环：所有会被复制的值（payload、observed、schema、provider/字段输出）都先在复制与任何后端调用之前做有界检查。字典/数组的直接、间接或混合循环报 `invalid_value`；容器嵌套超过公开 `MAX_VALUE_DEPTH = 64`（根 Dictionary/Array 记为第 1 层，每层嵌套 +1）同样报 `invalid_value`。合法共享但无环的子结构照常接受，不会被误判为循环（检查只用当前祖先路径的引用同一性，不用全局 visited）。拒绝先于 `duplicate`、`backend.record` 与引擎递归发生，不依赖引擎栈溢出后的默认返回值；错误 detail 只含截断路径与原因（≤160 字符），不回显原 payload 或凭据。服务端 `validate_tree` 的 16 层限制独立生效，本封装不改变科学协议。
- `record()` 不隐式 commit/finish，不改变随机化、评分、RT、checkpoint 或科学顺序；底层后端仍校验 schema 绑定与背压，未知 schema 不会先进入 RAW。

配置与记录都返回明确错误 `{"error": <code>, "detail": ...}`：

| code | 含义 |
|---|---|
| `invalid_config` | 配置缺键/多键、`event_type` 为空、`fields` 类型错误或与 provider 同时出现 |
| `invalid_field` | 字段名或 Callable 无效（配置时，或调用时已失效） |
| `invalid_provider` | provider 不是有效 Callable 或未返回 Dictionary |
| `invalid_schema` | schema 不是含非空 String `id`/`version` 的 Dictionary（独立本地后端返回同名错误） |
| `invalid_defaults` | 未配置默认且调用时缺少 kind/schema |
| `invalid_payload` | 没有已声明来源且 payload 为 null，或 payload 不是 Dictionary |
| `invalid_value` | 值不是普通有限 JSON（Node/对象、非有限 float、非 String 键、直接/间接/混合循环引用、超过 `MAX_VALUE_DEPTH` 的嵌套） |
| `provider_error` / `field_error` | provider/字段返回 `{error: ...}`，按显式失败处理，不写入缓冲区 |

### 各层边界（客户端缓冲 / 本机持久记录 / 服务端 RAW）

- 包装器只做**形状**检查：`schema` 必须是含非空 String `id`/`version` 的 Dictionary；某个 id/version 是否被研究登记由服务端的发行描述符决定。形状检查不是登记检查，也不是绑定检查。
- `record()` 先进入本机内存/本地队列缓冲；`commit()` 才把记录写入本机持久存储（独立本地后端为 `local.sqlite`，原生远端后端为 `queue.sqlite` 中的会话记录与 `pending` 列表）。`record()` 本身从不上传、commit 或 finish。
- 只有远端上传通过服务端校验后事件才进入服务端 RAW（事件行）。服务端按 `event_type` 查描述符并要求 `schema_id`/`schema_version` 精确匹配；未登记或版本不符的批次整批拒绝，RAW 零新增。拒绝后本机记录身份、payload 与 `pending` 保持不变，可修复后重试或显式导出；不可重试的 422 会安全暂停并在持久 delivery 中记录 `last_error_kind`，不以超时或重试掩盖。

独立示例（不修改科学任务，真实本地后端、真实 SQLite、零网络请求）：

```sh
godot --headless --path examples/synthetic_experiment --script ../data_defaults_demo/demo.gd
```

示例源码与说明在 `examples/data_defaults_demo/`；成功时打印一行 `DATA_DEFAULTS_DEMO {...}` 并按明确路径保存结果 JSONL。同一目录的 `web_demo_bootstrap.gd` 是 P03R10R 浏览器回归用的测试专用入口：测试把真实项目复制到新构建目录、只替换 `bootstrap.gd`，再经真实 Web 导出、真实桥与浏览器 SDK 运行同一套默认字段/显式覆盖/复制与拒绝路径；它不是发行入口，也不修改原项目或科学任务。以上来源约束与逐值对账只代表当前合成配置和本轮实测环境（本机回环、匿名/本地模式），不代表其他代理或部署配置；失败诊断以显式 `error` 及后端/服务器返回为准，成功路径证据不外推为对所有失败模式的结论。
