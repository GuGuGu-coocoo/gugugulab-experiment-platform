# 实验开发者说明

实验科学逻辑继续通过通用数据模块记录事件/checkpoint并结束任务，固定release/session和本地待上传数据不随新配置重新绑定。本轮后台修复不改变事件协议、原生存储格式或科学任务；Windows x64 目标只是同一场景的导出平台，`task.gd` 与事件/检查点语义不变。

Web托管与预览统一使用维护者配置的GEP_PUBLIC_API（含非默认端口）；原生的connection.json仍是描述登记／兼容路径。GEC 统一准入/恢复/收尾壳已提供：同一 Web/macOS 构建按冻结配置里的 `mode`（`anonymous`/`id`/`password`）动态渲染入口，公开配置另带 `shell_capability`（未知能力直接失败关闭，旧配置走兼容字段集）。原生完整下载套件已按有界上传实现：登记原生描述后上传实际的 `.app` ZIP，批准发行时服务端冻结公开配置/schema/codebook/许可证与 `THIRD_PARTY_NOTICES.txt` 依赖声明并组装带成员哈希的清单，外层摘要只存数据库；发行列表提供完整包与 sidecar 下载，包内 `connection.json` 与 `.app` 同级即可直接启动。当前发行按平台呈现：只有真实 Web 产物才给浏览器入口，完整原生当前发行只显示受控分发说明，不生成无效 Web 路径。Windows x64 完整包沿用同一数据层与参与壳：描述显式声明程序根、入口、依赖与 GDExtension 清单，服务端校验真实 PE32+ x86-64 映像、PCK 引擎版本与 Windows ZIP 路径规则（盘符/UNC、反斜杠、大小写/Unicode 归一化重名、设备名、结尾点/空格、ADS、链接与脚本），冻结包内 `connection.json` 与 EXE 同级，程序按既有可执行文件同级查找逻辑读取；未知平台失败关闭，不回退 Web 或旧分支。

当前接入及构建见[启动指南](quickstart.md)与[协议](protocol.md)。不开发通用Runner；刺激、随机化、试次与科学时序由实验负责。
