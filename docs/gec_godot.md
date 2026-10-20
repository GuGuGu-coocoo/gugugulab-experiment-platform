# 给已有 Godot 项目安装 GEC

GEC 是通用接入依赖。下载包不包含你的游戏、主场景、游戏接线、研究地址或账号。你保留现有游戏，自己决定开始、记录什么事件和结束的位置；GEC 负责准入、持久保存、上传和收尾。

当前包为 **GEC 0.1.0 / `gep/1`，Godot 4.7.2 标准版**。Web 使用 Compatibility、单线程；原生依赖包含 macOS universal 与 Windows x64。macOS 只在 arm64 实际运行，其他系统不在本包的验证范围内。未发布 GitHub Release，下载文件由维护者提供。

## 安装到现有项目的副本

1. 先运行你的原项目，确认玩法正常，再复制一份项目用于接入。
2. 解压 `gec_godot_0_1_0.zip`。把 `addons/`、`gec_web/`、`package_web.gd`、`package_native.gd`、`SQLITE_LICENSE.md`、`SQLITE_NOTICE.txt`、`GEC_LICENSE.txt` 和 `GEC_GODOT_NOTICES.txt`、`GEC_THIRD_PARTY_NOTICES.txt` 复制到项目根目录，即 `project.godot` 所在目录。
3. 若已有同名依赖或许可文件，先检查版本和差异，别直接覆盖。**不要替换项目的 LICENSE、主场景或 export_presets.cfg。** 包内 `README.md` 和 `gec_descriptor.example.json` 是阅读材料，不自动改变项目。
4. 用 Godot 打开项目，等依赖导入完成。项目中应能看到 `addons/gec/session.gd`、`addons/godot-sqlite/bin/` 和四个 `gec_web/*.js`。

## 你自己创建接线

推荐新增一个入口 `Node`，把原游戏场景作为子节点，接线写在新入口的脚本里。原游戏可以只提供普通事件信号、开始方法和结束信号。具体演练见 [Moss Duel 手动接入](moss_duel_gec.md)，其中给出可以整块粘贴的脚本。

下载包的 `session.gd` 提供以下便利接口：

| 调用 | 意义 |
| --- | --- |
| `configure_defaults({"event_type": ..., "schema": {"id": ..., "version": ...}})` | 声明之后记录使用的事件类型与 schema。必须与构建描述一致。 |
| `prepare(program_version, task_node, on_start, on_retire)` | 安装参与壳、禁止任务提前运行；完成在线准入后调用你的 `on_start`。返回初始化结果，不代表参与准入成功。 |
| `record(payload, kind = "", schema = {})` | 同步复制普通 JSON 数据并排队，后台串行记录和本地提交；返回 `queued` **不代表已持久保存**。 |
| `finish()` | 请求收尾，等待排队记录本地提交后声明整次完成，收尾界面继续显示上传状态。 |
| `status()` | 查看队列数量、本地保存错误和后端数据状态。 |

`on_retire` 由你处理游戏自己的显示层，包括不继承父节点可见性的 CanvasLayer。`display_locale` 可覆盖参与壳的显示语言，不修改冻结参与政策。配置或本地保存错误有明确提示；本地提交失败会暂停任务，点击重试使用已有记录身份，成功后再继续。记录函数的错误返回也应由宿主处理，不能把错误当作记录成功。

`local_saved` 信号表示某次本地提交成功；`completed` 表示队列排空后的本地完成声明，**不是服务器收齐**。最终确认由参与壳及后端状态显示。

通用低层接口仍在 `addons/gec/experiment_data.gd`，包括 `prepare/record/commit/status/checkpoint/finish`，可选择本地后端或 GEC 后端。便利接口默认**仅恢复已保存数据并补传**，不推断游戏状态、不自动重开一局。需要恢复任务时必须先定义 checkpoint 策略，再使用低层接口；不要在便利接口还有排队记录时并发调用低层 checkpoint/commit。参见 [开发者指南](experiment_developer.md)与[协议](protocol.md)。

## 创建构建描述

在项目根目录创建 `gec_descriptor.json`。参考包内 `gec_descriptor.example.json`，填写自己的程序版本、事件 schema 和 codebook。`program_sha256` 的占位值由包装助手从真实导出字节计算，不要自己猜摘要。版本必须与 `prepare()` 的 `program_version` 一致。

这个文件描述程序，不是连接配置。不要放服务器密码、名单密码或参与凭据。Web 配置由 GEP 注入；原生正常流程从 GEP 下载完整冻结发行包，获得程序同级的公开 `connection.json`。

## 配置现有导出预设

使用 Godot“项目 → 导出”，安装 **4.7.2** 同版本模板，保留已有项目预设及游戏选项，只作以下修改。

### Web

1. Web 预设使用 Compatibility、关闭线程和 GDExtension 支持。导出文件名为 `index.html`，放到独立目录，例如 `exports/web/index.html`。
2. 在 Web 的 **HTML → Head Include** 加入以下一行；如果已有内容，追加这一行，不覆盖原内容：

   ```html
   <script type="module" src="gec/bridge.js"></script>
   ```

3. 在“资源”页的 **排除文件过滤器（Exclude Filter）** 中，保留原值并追加：

   ```text
   addons/godot-sqlite/*,connection.json,gec_descriptor*.json,package_web.gd,package_native.gd,gec_web/*,exports/*
   ```

4. 点击“导出项目”，取消“导出调试”。双击项目根目录的 `package_web.gd`，在脚本编辑器选择“文件 → 运行”（Windows `Ctrl+Shift+X`，macOS `⌘+Shift+X`）。在弹窗选择刚导出的 `index.html`。
5. 成功弹窗给出标准 GEP Web ZIP 路径；助手加入固定 Web SDK、计算摘要，不改变原始导出。已有同名 ZIP 保留，用弹窗所列的新文件。失败时没有可用的新包，按原因修正后重试。

### 原生

1. 原生预设资源的排除过滤器保留原值并追加：

   ```text
   connection.json,gec_descriptor*.json,package_web.gd,package_native.gd,gec_web/*,exports/*
   ```

   **原生不要排除 `addons/godot-sqlite/`**，它用于真实本地事务保存。
2. macOS 导出 ZIP；Windows 选择 x86_64，关闭 **Embed PCK**，将 EXE 导出到独立子目录，EXE 和 PCK 保持同一文件名主体。程序可以使用你自己的名字。
3. 通过脚本编辑器“文件 → 运行”启动 `package_native.gd`，选择 macOS ZIP 或 Windows EXE。成功弹窗提供程序 ZIP 与配套 `_descriptor.json` 的准确路径。
4. 在 GEP 的“实验上传与发布”登记整个原生描述 JSON，上传对应程序 ZIP。发布测试版本后，下载**完整发行包**，解压并运行其中的程序。

包装助手仅帮助产生标准输入；GEP 仍校验平台、版本、结构、限额、摘要和依赖。失败不发布半成品。若编辑器中的接入版没有连接配置，不能开始远端参与是预期行为；请用 GEP 隔离预览或批准的完整发行测试。

## 在 GEP 验证

使用新合成研究：Web 上传 → 验证 → 隔离预览 → 批准 → 设置当前发行 → 开放新参与 → 分享入口。隔离预览只在本地保存，不显示服务器收齐。原生登记并批准后使用完整下载包。结束后，在研究的“导出”页创建固定快照并下载 JSONL，对照声明的事件和值。

三种准入方式由冻结配置决定，同一构建可以在不同合成研究测试，无需分别重建游戏。首次开始要联网；活动中断网先本地保存，联网后补传。关闭进程后不保证继续上传，再次打开恢复队列。进入内存、本地提交和服务器收齐是三种不同状态。

安装步骤的工程核验不能代替人的操作体验。本接入练习不启用真实研究，不提供科学计时验收，不开发 Runner。
