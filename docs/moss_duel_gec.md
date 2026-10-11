# 给 Moss Duel 手动接入 GEC

Moss Duel 是一个独立的 Godot 2D Boss 战小游戏。原游戏没有 GEC、服务器地址或参与凭据，可以直接玩。这里是在**游戏副本**中接入 GEC 的操作练习，用合成游戏事件测试 GEP。它不代表真实研究或科学计时验收。

接入后，GEC 负责开始参与、名单/密码、同设备数据恢复、本地保存和上传收尾；游戏仍负责移动、攻击、弹反、Boss、伤害和胜负。每次参与只打一局，胜利或死亡后进入 GEC 收尾页。重新参加必须重新打开入口，建立新 session。

## 准备两个包

- `moss_duel_standalone.zip`：干净的游戏项目。
- `moss_duel_gec_kit.zip`：外置接入包，包含入口脚本、现有 GEC 源码、Web SDK、经过校验的原生 SQLite 依赖、导出预设与包装助手。它不含研究连接配置或密码。

使用 **Godot 4.7.2**，并在 Godot 的“编辑器 → 管理导出模板”（`Editor → Manage Export Templates`）安装同版本模板。GEP 当前只接受这个版本、Web SDK 0.1.0 和协议 `gep/1`；不要换成其他版本后继续登记。

若从仓库源码开始，游戏目录为 `examples/moss_duel/`，接入源文件目录为 `integrations/moss_duel/`。接入源码目录不重复存放完整依赖；手动操作使用已经准备好的 **kit ZIP**。

## 一次复制，一次切换主场景

1. 解压游戏包，先导入其中的 `project.godot`，点击右上角运行项目，确认可以正常玩。
2. 关闭项目，把这个游戏文件夹复制一份，命名为 `moss_duel_gec`。后续只修改这份副本。
3. 解压接入包。把里面的**全部内容**复制到 `moss_duel_gec` 文件夹，也就是 `project.godot` 所在目录。替换 `export_presets.cfg`；若提示 `LICENSE`、`GODOT_NOTICES.txt` 或 `GEC_GUIDE.md` 同名，也可以替换，它们是配套说明。原游戏脚本、`main.tscn` 和素材不替换。
4. 在 Godot 项目管理器导入副本的 `project.godot`。首次扫描依赖时等待编辑器完成导入。
5. 在左下角“文件系统”中右键 `gec_entry.tscn`，选择“设为主场景”（`Set As Main Scene`）。

完成后，项目根目录应同时有：

```text
project.godot          原游戏项目
main.tscn             原游戏场景，保持原样
gec_entry.gd          新增的接线脚本
gec_entry.tscn        新的主场景
gec_descriptor.json   新增的构建描述模板
package_web.gd        Web 包装助手
package_native.gd     原生包装助手
export_presets.cfg    接入包提供的导出预设
data/                 通用数据模块
addons/gec/           GEC 适配后端与参与壳
addons/godot-sqlite/  原生依赖，包含 bin/
gec_web/              固定版本的四个 Web JS 文件
```

**不用往游戏脚本粘贴代码，也不用填写服务器地址。** 接线已在新增场景里完成；删除接入文件并把 `main.tscn` 设回主场景即可继续独立玩法。普通 GEC 参与需要后台批准的发行配置；在编辑器里直接运行接入场景，没有配置时不能开始远端参与，这是预期行为。

## 先做 Web 测试

### 从 Godot 导出

1. 选择“项目 → 导出”（`Project → Export`）。选择列表中的 **Web**。
2. 点击“导出项目”（`Export Project`），保存到项目文件夹内的 `exports/web/index.html`。第一次可以在保存窗口创建 `exports` 和 `web` 文件夹。取消“导出调试”（`Export With Debug`），确认导出。
3. 返回 Godot，双击 `package_web.gd`，让它显示在脚本编辑器中。点击脚本编辑器的“文件 → 运行”（`File → Run`），或按 `Ctrl+Shift+X`（macOS 为 `⌘+Shift+X`）。这一步运行的是包装助手，**不是右上角的游戏运行按钮**。
4. 在弹出的文件选择框中选择刚导出的 `exports/web/index.html`。
5. 看到“打包完成”后，结果在 `exports/moss_duel_gep_web.zip`。若同名文件已存在，新结果为 `_2`、`_3` 等，旧文件不覆盖。使用成功弹窗所列的本次路径。

助手自动把 Web 输出、固定 Web SDK 与真实程序摘要写进标准 ZIP。不要手改摘要，不要只压缩 `index.html`，也不要在文件管理器里直接打开 HTML 来验证远端接入。

### 在 GEP 后台操作

1. 登录准备好的**合成测试实例**，创建一个新研究，例如“小游戏接入练习”。在“参与与名单”确认当前方式为“无需 ID”（新研究默认如此），不使用真实名单或被试数据。
2. 打开该研究的“构建与发行”，选择 `moss_duel_gep_web.zip` 并上传。
3. 验证成功后打开“隔离预览”。应先看到 GEC 的开始参与界面；点击开始后进入游戏。打一局，胜利或死亡后显示“本地测试完成”。预览只本地保存，不会显示服务器收齐。
4. 返回后台，批准该构建的合成发行。进入“招募与门户”，在“当前发行”选刚批准的发行并点击“设为当前发行”；将“招募状态”改为“开放新参与”（立即保存）。若仅通过分享入口测试，研究可以保持私有；公开门户展示需要另行显式设置公开。
5. 从后台打开参与入口。再打一局，结束后等到“数据已上传并确认”之类的服务器收齐提示。
6. 在研究的“导出”页保留默认选项，点击“下载数据（概览 + 明细）”。这会创建快照并自动下载 ZIP；查看原始事件时，点击本次快照的“下载 JSONL”链接。默认 v2 JSONL 每行的 `event_type` 应为 `game.duel`；游戏动作在 `record.payload.kind`，最后汇总为 `round_summary`。JSONL 记录数因实际操作不同而不同。

Web 的连接配置由 GEP 注入，三种参与方式由发行时冻结的配置决定。测试名单 ID 或 ID+密码时创建另一项合成研究，沿用同一个游戏构建；不用分别重建游戏。

## 再做原生测试

本接入包包含 macOS 与 Windows x64 的 SQLite 依赖。选择一个当前可运行的系统即可；跨平台导出成功不等于已经在目标电脑运行。本小游戏的原生参与壳使用英文，避免依赖未随包提供的中文字库；这只改变显示语言，冻结连接配置和参与政策保持原样。Web 参与壳仍使用其正常语言设置。

### macOS

1. Godot“项目 → 导出”选择 **macOS**，导出到 `exports/native/moss_duel.zip`，取消“导出调试”。
2. 打开 `package_native.gd`，通过脚本编辑器“文件 → 运行”启动包装助手，选择这个 ZIP。
3. 成功后获得 `exports/native/moss_duel_gep_macos.zip` 与 `moss_duel_gep_macos_descriptor.json`。同名已存在时使用弹窗里的新路径。

### Windows x64

1. Godot“项目 → 导出”选择 **Windows**，导出到 `exports/windows/Moss Duel/Moss Duel.exe`，取消“导出调试”。保持 `Moss Duel` 文件夹和 EXE 名称，不启用嵌入 PCK。
2. 打开 `package_native.gd`，通过脚本编辑器“文件 → 运行”启动包装助手，选择这个 EXE。
3. 成功后获得 `exports/windows/moss_duel_gep_windows.zip` 与 `moss_duel_gep_windows_descriptor.json`。助手自动包含 PCK、SQLite DLL 和 GDExtension 清单。

### 登记并下载可运行包

1. 为原生测试创建另一项合成研究。不要把已通过的 Web 研究当前发行切换掉后继续使用旧入口。
2. 在“构建与发行”中打开本次生成的 `_descriptor.json`，复制**整个文件**粘贴到原生构建描述框，登记构建。
3. 为这个构建上传刚生成的 `moss_duel_gep_macos.zip` 或 `moss_duel_gep_windows.zip`。GEP 最终校验平台、架构、摘要、依赖和包结构；助手打包完成不代替这项检查。
4. 批准合成发行。进入“招募与门户”，在“当前发行”选刚批准的原生发行并点击“设为当前发行”；将“招募状态”改为“开放新参与”（立即保存）。默认暂停招募时，即使包已批准也不能新参与。
5. 返回“构建与发行”，点击该发行的“下载完整发行包”。解压下载包后直接打开 `.app` 或 EXE；不要使用 Godot 原始导出目录里的程序做远端验收。
6. 完整下载包已带同级 `connection.json`，不需要填写地址或密码名单。打一局后核对 GEC 收尾和后台 JSONL。

若系统拦截未签名的开发程序，只使用这份明确来源的合成包，并按本机允许的方式打开；不要修改系统证书信任或关闭全局保护。原生入口只接受 HTTPS 或受支持的本机回环地址；跨设备服务使用已准备好的安全连接/回环转发，不把普通 LAN HTTP 地址手写进去。

## 断网、重开和恢复应看到什么

- 首次开始前要联网完成准入。战斗中断网，游戏继续，记录先在本机持久保存；网络恢复后补传。任务结束不等于服务器收齐。
- 游戏动作通过串行接线写入，每条先 `record()` 进入缓冲，再 `commit()` 持久保存；成功后后台上传。网络重试保留原事件 ID。尚未 commit 的进程内记录不能保证在强制结束后保留。
- 本机存储失败时暂停当前游戏，显示 **Retry local save（重试本地保存）**。点击后使用已有缓冲中的身份重试，成功后再继续；不要关闭程序来代替重试。小游戏及其接线提示使用英文，避免依赖未随包提供的中文字库。
- **本示例没有战斗 checkpoint 策略。** 重开后可以按 GEC 权限恢复原会话的已保存数据，不能从 Boss 血量或攻击中间自动继续，也不能把中断的一局重打后拼入旧 session。需要新的一局时重新打开参与入口开始新的参与。
- 一局声明的数据全部收齐，并且确认已持久保存后，GEC 才自动清理本地原始副本。仅批次上传成功不会清理仍活动的会话。

本次手动接入、文件选择、下载落盘和整体使用体验仍需要你实际操作后判断。记录帮助、失败和未运行项目；自动工程检查不替代人工测试。

## 可选：查看接入究竟改了什么

原游戏只发出两个通用信号，不知道 GEC：

```gdscript
signal telemetry_event(kind: String, payload: Dictionary)
signal run_finished(summary: Dictionary)
```

外置 `gec_entry.gd` 连接信号，准入后调用 `Game.start_round()`，完成后调用通用数据层的 `finish()` 并显示 GEC 收尾；不会修改游戏伤害、随机化或评分。事件统一使用 `game.duel`、schema `duel_event/1`，payload 为 `{kind, data}`，只采集游戏显式发出的普通 JSON 值。`elapsed_ms` 是游戏自己提供的时间，不能当作已验证的科学反应时。

第一次按本文接入保持版本 `moss-duel-1`。以后修改游戏或接入代码，再向同一研究登记新构建时，在 `gec_entry.gd` 的 `PROGRAM_VERSION` 和 `gec_descriptor.json` 的 `version` 中同时改为新版本，例如 `moss-duel-2`；重新导出、打包、登记。已批准的旧发行不改写。
