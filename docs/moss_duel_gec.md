# 从独立 Moss Duel 手动接入 GEC

[English](moss_duel_gec_en.md) · 中文

仓库只保留同一游戏的[干净独立版](../examples/moss_duel/README.md)和[已接入 GEC 版](../examples/moss_duel_gec/README.md)。本教程从**干净独立版**开始；已接入版用于直接运行或对照，不是本教程的起点。

这次模拟研究者**已经有一个能独立运行的游戏**，然后下载通用 GEC、自己手动接入。原游戏没有 GEC 或服务器配置；GEC 下载包没有 Moss Duel 的入口场景、接线脚本或导出预设。下面新增的文件由你亲手创建。

操作只在游戏副本中进行，用合成事件测试 GEP，不连接真实研究。接入后每次参与打一局，游戏仍负责移动、攻击、弹反、Boss、伤害和胜负。GEC 负责开始参与、名单/密码、本地保存、上传与收尾。

## 1. 先玩独立游戏

1. 打开仓库的 `examples/moss_duel/`，或解压维护者提供的 `moss_duel_standalone.zip`。
2. 用 **Godot 4.7.2 标准版**导入其中的 `project.godot`，运行项目。
3. 点击 **Enter the arena**。J 轻击、按住 K 再松开为重击、L/Shift 弹反、E 冲刺、空格跳跃、Esc 暂停。原游戏可以反复开局，不需要网络或 GEP。
4. 关闭项目，复制整个游戏文件夹，命名为 `moss_duel_gec`。下面只修改这份副本。

## 2. 下载并安装通用 GEC

解压单独的 `gec_godot_0_1_0.zip`，按[通用安装指南](gec_godot.md#安装到现有项目的副本)复制依赖与许可。它和这个游戏无关，也可安装到其他 Godot 项目。不要把已接入版的入口脚本提前复制过来；下面会亲手创建。

完成后，项目根目录应有原来的 `project.godot`、`main.tscn`、`scripts/`、`assets/`，以及新增的 `addons/`、`gec_web/`、`package_web.gd`、`package_native.gd`。此时直接运行项目仍是原游戏，**尚未接入**。

## 3. 亲手创建入口场景

1. 在 Godot 中打开副本，选择“场景 → 新建场景”（`Scene → New Scene`）。选择 **其他节点（Other Node）**，搜索并创建 **Node** 作为根节点，把它重命名为 **GECEntry**。
2. 将文件系统里的 `main.tscn` 拖到场景树的 `GECEntry` 上，作为它的子场景。把这个子节点重命名为 **Game**，注意大小写。不要打开或修改 `main.tscn` 的内部节点。
3. 保存新场景到项目根目录，文件名为 **gec_entry.tscn**。
4. 选中根节点 `GECEntry`，点击“附加脚本”（`Attach Script`），语言选 GDScript，取消模板，路径为 **res://gec_entry.gd**，点击创建。
5. 用下面整块代码替换新脚本的全部内容，然后保存。代码只写在新文件里，**原游戏脚本保持原样**。

```gdscript
extends Node
const GEC = preload("res://addons/gec/session.gd")
var gec
var record_failed := false
@onready var game = $Game

func _enter_tree() -> void:
	$Game.set_host_controlled(true)
	$Game.process_mode = Node.PROCESS_MODE_DISABLED

func _ready() -> void:
	gec = GEC.new()
	add_child(gec)
	gec.configure_defaults({"event_type": "game.duel", "schema": {"id": "duel_event", "version": "1"}})
	game.telemetry_event.connect(_record)
	game.run_finished.connect(_finished)
	gec.prepare("moss-duel-3", game, _start, _retire)

func _start() -> void:
	game.process_mode = Node.PROCESS_MODE_INHERIT
	game.show()
	game.start_round()

func _record(kind: String, payload: Dictionary) -> void:
	if record_failed:
		return
	var result: Dictionary = gec.record({"kind": kind, "data": payload})
	if result.has("error"):
		record_failed = true
		game.process_mode = Node.PROCESS_MODE_DISABLED
		gec.shell.set_overlay_visible(true)
		gec.shell.announce("Game event rejected: " + str(result.error))

func _finished(summary: Dictionary) -> void:
	_record("round_summary", summary)
	if not record_failed:
		gec.finish()

func _retire() -> void:
	game.set_host_controlled(true)
	game.hide()
```

6. 在文件系统中右键 **gec_entry.tscn**，选择 **设为主场景（Set As Main Scene）**。

这块接线用的是游戏已有的普通事件/结束信号和开始方法。事件类型 `game.duel` 与下方构建描述一致。`_retire()` 同时关闭游戏 HUD 和世界，因为 CanvasLayer 不会随父节点隐藏。GEC 默认中文，可在界面切换 English。

此时编辑器运行会显示 GEC 入口，但没有连接配置时不能开始远端参与。不要为此把服务器地址写进脚本；接下来通过 GEP 隔离预览和批准的发行测试。

## 4. 亲手创建构建描述

在项目根目录用文本编辑器新建 **gec_descriptor.json**，粘贴以下全部内容并保存。文件名不能带额外的 `.txt`。

```json
{
  "version": "moss-duel-3",
  "platform": "godot_web",
  "host_version": "4.7.2",
  "sdk_version": "0.1.0",
  "protocol_version": "gep/1",
  "program_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "schemas": {
    "game.duel": {
      "id": "duel_event",
      "version": "1",
      "schema": {
        "type": "object",
        "properties": {
          "kind": {"type": "string", "minLength": 1},
          "data": {"type": "object"}
        },
        "required": ["kind", "data"],
        "additionalProperties": false
      }
    }
  },
  "codebook": {
    "purpose": "Synthetic game integration exercise.",
    "kind": "Explicit game signal name; round_summary is the game's end summary.",
    "data": "Unchanged explicit game payload; no implicit scene or credential collection.",
    "elapsed_ms": {"unit": "ms", "source": "game-owned clock", "meaning": "Game elapsed time, not a validated reaction time."},
    "recovery": "Saved-data upload only; no battle checkpoint or replay."
  }
}
```

摘要占位值由包装助手计算；不要手改摘要。Web/macOS/Windows 用同一个描述源文件，助手根据真实导出生成正确的平台描述。

## 5. 导出 Web，放到 GEP 测试

1. 按[通用导出指南的 Web 部分](gec_godot.md#web)修改副本原有的 **Web** 预设：追加一行 HTML 桥接，追加资源排除项。不要用别人提供的整份预设覆盖原游戏配置。
2. 导出到 **exports/web/index.html**，取消“导出调试”。运行项目根目录 **package_web.gd**，在弹窗选择刚导出的 `index.html`。
3. 成功弹窗给出本次 **gep_web.zip** 路径；同名存在时结果带 `_2` 等后缀，旧文件不覆盖。
4. 登录准备好的**合成实例**，创建新研究“小游戏接入练习”。在“参与与名单”确认“无需 ID”，不使用真实名单。
5. “实验上传与发布”上传本次 ZIP。验证后点击“预览（不上传数据）”，应先显示 GEC 入口；点击开始才进入游戏。打一局自然胜利或死亡，核对本地保存状态。预览只本地保存，不会显示服务器收齐。
6. 返回后台点击“发布版本”。在“招募与门户”选择该版本，点击“设为当前版本”，再将“招募状态”改为“开放新参与”（立即保存）。通过分享入口测试可保持研究私有。
7. 打开分享入口，再打一局。结束后等到 GEC 显示数据上传并确认。
8. 在“导出”页点击“下载数据（概览 + 明细）”，再点本次快照的“下载 JSONL”。v2 JSONL 的 `event_type` 为 `game.duel`，动作在 `record.payload.kind`，最后只有一条 `round_summary`。记录数量随操作变化。

三种准入方式由 GEP 冻结配置决定，使用另一项合成研究测试名单 ID 或 ID+密码即可，不需要为各模式重建游戏。

## 6. 再测原生

1. 按[通用导出指南的原生部分](gec_godot.md#原生)修改副本的 macOS 或 Windows 预设。原生保留 SQLite 依赖；Windows 选择 x86_64、不嵌入 PCK。
2. macOS 导出到 **exports/native/moss_duel.zip**；Windows 导出到 **exports/windows/Moss Duel/Moss Duel.exe**。程序名称只是这次练习的选择，通用 GEC 支持自己的名字。
3. 在脚本编辑器运行 **package_native.gd**，选择导出的 ZIP 或 EXE。弹窗提供程序 ZIP 与配套 `_descriptor.json` 的准确路径。
4. 创建另一项新合成研究。在“实验上传与发布”粘贴配套描述文件的**整个 JSON**登记构建，再上传对应程序 ZIP。
5. 发布后设为当前版本、开放新参与，然后下载 **完整实验包**。
6. 解压下载包，运行其中的 `.app` 或 EXE。同级公开 `connection.json` 由 GEP 提供，不要使用 Godot 原始导出目录的程序代替完整下载包。
7. 打一局，核对收尾与后台 JSONL。GEC 既有原生支持 macOS arm64 / Windows x64；本次新接线的可见原生 GUI 与 Windows 实机完整闭环仍待测试，详见[验收记录](verification.md)。其他系统未验。

跨设备服务使用准备好的安全连接或受控回环转发；不改全局证书信任，不把普通 LAN HTTP 地址手写进配置。

## 7. 断网与恢复的边界

- 首次开始前联网完成准入；战斗中断网仍可玩，已提交记录留在本机，联网后补传。游戏结束不等于服务器收齐。
- 接线的 `record()` 先同步复制并进入队列；后台逐条记录、本地提交、分批上传。进入队列不等于保存完成；未提交的进程内记录不能保证在强制关闭后保留。
- 本地提交失败时暂停任务并提示 **Retry local save**。重试沿用缓冲中已有记录身份，成功后继续，不通过关闭程序代替重试。
- 本游戏**没有战斗 checkpoint 策略**。重开只能按权限恢复已保存数据补传，不恢复 Boss 血量、不重打一局拼进旧会话。新一局要从重新打开的参与入口开始新 session。
- 整次声明的数据收齐，合法确认已持久保存且无恢复依赖后，才清理本地原始副本；活动会话不因某批 ACK 提前清理。

这套文档可以由工程测试在临时副本验证，但**你亲手创建场景、粘贴代码、选择文件、下载和试玩的体验仍需人工测试**。遇到问题记录所在步骤与提示，不把自动测试算作你的手动接入已通过。
