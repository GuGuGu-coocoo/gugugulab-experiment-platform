# GEC 开发者文档

[English](gec_godot_en.md) · 中文

GEC 是接入依赖，不是实验 Runner。你的 Godot 项目负责刺激、按键、随机化、评分和事件含义；GEC 处理参与准入、本地事务、上传和结束页面。研究者在 GEP 选择无需 ID、ID 或 ID 与密码，同一实验程序不需要为这三种模式分别改一套登录界面。

当前版本是 GEC 0.1.0、协议 `gep/1`，宿主为 Godot 4.7.2 标准版。Web 使用 Compatibility、单线程和 Chrome；原生依赖提供 macOS universal 与 Windows x64，macOS 实际运行验证为 arm64。其他浏览器、ARM Windows 或 Linux 原生实验不在当前验证范围内。

GEP 服务端 0.1.1 安全更新不更改 GEC SDK 版本或实验程序。现有部署按[升级说明](upgrade_0_1_1.md)处理来源隔离和旧队列，不为升级重写冻结包或改变科学任务。

## 1. 获取通用依赖

GEP 服务端发行包只交付 Docker，不是 Godot 插件安装器。GEC 源码在仓库的 [GEC_SDK 文件夹](../GEC_SDK/)，包含 Godot 接入代码和 Web SDK。已有维护者提供的 `gec_godot_0_1_0.zip` 时直接进入下一节；包不能包含特定游戏、账号或固定研究连接。

自行生成通用包时，先下载并解压[当前源码](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/archive/refs/heads/main.zip)。在开发电脑准备 Python 3.12，下载官方 [godot-sqlite v4.7 demo.zip](https://github.com/2shady4u/godot-sqlite/releases/download/v4.7/demo.zip)，保留原文件。进入源码目录运行：

```sh
python3 tools/build_gec_addon.py --output build/gec_download \
  --sqlite-archive /path/to/demo.zip
```

把路径换成下载文件的实际位置。Windows 的 Python 启动方式可用 `py -3.12` 替代 `python3`。工具只用 Python 标准库，核对官方依赖并生成 `build/gec_download/gec_godot_0_1_0.zip`；输出目录必须是新的，不能覆盖旧包。这里的 Python 是开发电脑上的构包工具，不是 GEP 部署要求。

## 安装到现有项目的副本

先复制原项目并确认它能正常运行，再接入 GEC。把通用 ZIP 中这些内容复制到 `project.godot` 所在目录：

- `addons/` 与 `gec_web/`。
- `package_web.gd`、`package_native.gd`。
- `SQLITE_LICENSE.md`、`SQLITE_NOTICE.txt`、`GEC_LICENSE.txt`、`GEC_GODOT_NOTICES.txt`、`GEC_THIRD_PARTY_NOTICES.txt`。

已有同名依赖先核对差异，不覆盖原工程的 `LICENSE`、主场景或 `export_presets.cfg`。包里的 README 和示例 descriptor 是参考资料。打开 Godot 等待导入后，应能看到 `addons/gec/session.gd`、SQLite 原生依赖和四个 `gec_web/*.js`。

## 3. 给任务接线

新建一个入口场景，把原任务作为子节点，例如 `Task`。任务在收到明确的开始调用前不能自行开始；仅禁用 process 不能阻止任务在 `_ready()` 里执行一次性逻辑。可以在原任务中加入“等待宿主开始”的模式，但不要改变研究的科学逻辑。

下面示例假定你的任务节点提供 `start_task()`、`retire_task()`；它们是你定义的任务方法，不是 GEC API。入口脚本的事件回调由你连接到原任务的信号：

```gdscript
extends Node

const GECSession = preload("res://addons/gec/session.gd")
var gec = GECSession.new()
@onready var task = $Task

func _ready() -> void:
    add_child(gec)
    var configured = gec.configure_defaults({
        "event_type": "task.response",
        "schema": {"id": "response", "version": "1"}
    })
    if configured.has("error"):
        push_error(str(configured))
        return
    gec.failed.connect(_on_gec_failed)
    var prepared = gec.prepare("my-task-1", task, _start_task, _retire_task)
    if prepared.has("error"):
        push_error(str(prepared))

func _start_task() -> void:
    task.start_task()

func _retire_task() -> void:
    task.retire_task()

func on_trial_response(trial_id: String, choice: String, rt_ms: float) -> void:
    var result = gec.record({"trial_id": trial_id, "choice": choice, "rt_ms": rt_ms})
    if result.has("error"):
        _on_gec_failed(result)

func on_task_finished() -> void:
    var result = gec.finish()
    if result.has("error"):
        _on_gec_failed(result)

func _on_gec_failed(result: Dictionary) -> void:
    task.process_mode = Node.PROCESS_MODE_DISABLED
    push_error(str(result))
```

准入成功后 GEC 隐藏前置表单并调用 `_start_task`；结束后用 `_retire_task` 关闭你自己的显示和输入，包括不继承普通节点可见性的 CanvasLayer。GEC 再显示结束与上传状态。完整演练见 [Moss Duel 接线教程](moss_duel_gec.md)。

`record()` 同步复制输入并排队，返回 `queued` 不代表已经落盘；后台按队列串行记录和提交。`local_saved` 信号表示一次本地提交成功，`completed` 表示本地完成声明，不代表服务器收齐。任务宿主应处理错误／暂停提示，不能把错误返回当作记录成功，也不应仅靠 `push_error` 继续无保护地收集下一条数据。

## 4. 描述事件和程序版本

在工程根目录新建 `gec_descriptor.json`，使程序版本与 `prepare()` 完全一致。下面的字段与接线示例配套：

```json
{
  "version": "my-task-1",
  "platform": "godot_web",
  "host_version": "4.7.2",
  "sdk_version": "0.1.0",
  "protocol_version": "gep/1",
  "program_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "schemas": {
    "task.response": {
      "id": "response",
      "version": "1",
      "schema": {
        "type": "object",
        "properties": {
          "trial_id": {"type": "string"},
          "choice": {"type": "string"},
          "rt_ms": {"type": "number", "minimum": 0}
        },
        "required": ["trial_id", "choice", "rt_ms"],
        "additionalProperties": false
      }
    }
  },
  "codebook": {
    "trial_id": {"description": "Trial identifier supplied by the task"},
    "choice": {"description": "Response value supplied by the task"},
    "rt_ms": {"unit": "ms", "source": "Task-owned response clock"}
  }
}
```

程序摘要的零值是包装前占位，包装助手从实际导出字节计算；不要手填一个猜测的摘要。代码本的来源、单位和字段要符合你的实际任务，示例不替你建立科学计时方法。

descriptor 描述程序，不是连接配置。不要写 Owner 密码、名单密码或参与 token。Web 配置由 GEP 注入；原生完整发行的公开 `connection.json` 随包冻结。协议中的 `purpose: synthetic` 是现有兼容字段，保留原值，不因界面去掉“测试”字样而手工改它或替换旧队列配置。

审计分支将每个 Web 发行和预览放在独立来源；不要把当前页面域名当成 API 地址。GEC 已按注入配置的 `api_url` 请求中央 API，程序／SDK 字节无需为此重打包，跨来源权限由服务端按发行核对。旧来源的队列由平台可信页补传，不会自动搬到新来源或自动续试；详见[安全边界](security.md)。

安全审计分支暂不接受 Schema 的 `pattern`／`patternProperties` 正则规则（含嵌套定义）；可用类型、枚举、长度和数值范围表达适用约束。旧发行若含这些规则，也会拒绝新参与及相关事件，返回 `schema_regex_unsupported`，而不是静默跳过校验。不要删掉待传数据或将它重标为新版；普通字段名 `pattern` 不受此限制。详见[协议兼容边界](protocol.md)。

## 5. 导出和包装

安全审计分支只接受 Stored／Deflate 压缩的程序 ZIP；沿用下列打包助手即可，不使用 BZIP2／LZMA。补丁和未关闭风险见[审计报告](SECURITY_AUDIT.md)，不代表已发布镜像自动更新。

使用 Godot 4.7.2 的同版本导出模板。保留原工程自己的预设，按平台调整，不复制整份别人项目的预设覆盖它。

### Web

1. 使用 Compatibility，关闭线程和 Web GDExtension 支持，导出为独立目录的 `index.html`。
2. 在 Web 预设的 HTML → Head Include 追加桥接；已有内容保留：

   ```html
   <script type="module" src="gec/bridge.js"></script>
   ```

3. 在资源排除过滤器保留原值，并追加：

   ```text
   addons/godot-sqlite/*,connection.json,gec_descriptor*.json,package_web.gd,package_native.gd,gec_web/*,exports/*
   ```

4. 导出项目，取消调试导出。打开 `package_web.gd`，在脚本编辑器选择“文件 → 运行”（Windows Ctrl+Shift+X，macOS ⌘+Shift+X），选择刚导出的 `index.html`。
5. 使用成功弹窗给出的 Web ZIP。它包含标准目录和 SDK，不是整个 Godot 工程。旧同名 ZIP 保留，按弹窗路径取新文件；失败时没有可用新包。

把 ZIP 上传到 GEP，验证后隔离预览。预览只本地保存；需要实际上传时，发布并从研究参与入口运行。

### 原生

1. 资源排除过滤器追加下面内容，但**不要排除 SQLite 插件**：

   ```text
   connection.json,gec_descriptor*.json,package_web.gd,package_native.gd,gec_web/*,exports/*
   ```

2. macOS 导出完整 `.app` ZIP。Windows 选择 x86_64、关闭 Embed PCK，将 EXE 和同名 PCK 导出到单独目录，保留原生依赖。
3. 在脚本编辑器运行 `package_native.gd`，选择 macOS ZIP 或 Windows EXE。使用它生成的程序 ZIP 及配套 `_descriptor.json`。
4. 在 GEP 登记描述、上传程序包并发布，然后下载完整发行包。解压并运行其中的程序，不单独替换 EXE 或 `connection.json`。

原生包没有 Web 预览；在对应系统运行。原生未签名／未公证的系统提示不在这里提供绕过步骤，分发前按自己目标系统的签名要求安排。

## 6. 验证一条真实数据链路

用合成数据建立研究，配置参与方式与名单，上传、预览并发布。设置当前版本后开放参与，从**参与入口**完成一个有已知值的任务，再到后台核对完成状态、创建快照、下载 JSONL，逐条比较事件和值。预览成功不能替代这个闭环。

三种参与方式来自发行配置，不需改任务代码。第一次准入需要联网；准入后断网先本地保存，联网后补传。页面／进程关闭后不保证继续后台上传，重新打开时也不能把“读取到内存”当作提交完成。不要自动清理旧 pending 数据、给旧数据换研究或版本。

GEC 便利层默认只处理已保存数据的补传，不推断任务状态。0.1.0 不公开恢复码和许可界面；若研究需要任务 checkpoint，先明确 trial 边界、随机状态与科学顺序，再使用[低层协议](protocol.md)，不要在便利层仍有队列时并发调用低层 commit／checkpoint。

## 7. 发布前核对

- `program_version`、descriptor 版本、schema ID 和事件类型一致。
- Web 使用实际 GEP 地址；原生使用完整冻结包，不内置后台秘密。
- 本地保存和上传出错时可见，结束页不在缺数据时宣称已收齐。
- 原任务的计时、随机化、评分、排除规则没有被接线改变。
- 在实际目标系统运行并对账；交叉导出不等于实机通过。

GEP 服务器安装见[部署教程](deployment.md)，研究配置与发行操作见[使用教程](researcher.md)。
