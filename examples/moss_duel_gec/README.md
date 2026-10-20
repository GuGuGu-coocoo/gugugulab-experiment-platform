# Moss Duel：已接入 GEC

[English](readme_en.md) · 中文

这份工程已接好 GEC，适合先体验上传、发布和收数，或对照自己的接线。要从零练习，请从[干净独立版](../moss_duel/README.md)复制工程，再按[手动接入指南](../../docs/moss_duel_gec.md)操作。

## 已经接好了什么

- `gec_entry.tscn` 是主场景，先显示 GEC，准入成功后才开始游戏。
- `gec_entry.gd` 使用通用 `GECSession`，连接游戏原有信号，记录 `game.duel`，结束后交回 GEC。
- `gec_descriptor.json` 使用程序版本 `moss-duel-3`；Web、macOS、Windows 导出预设已配置桥接和资源排除项。
- `scripts/`、`assets/`、`main.tscn` 与独立版相同，未为 GEC 改写战斗逻辑。

工程没有服务器地址、研究绑定、后台密码或名单凭据。开始时缺少连接配置会被阻止，这是预期行为；不用把地址写进脚本。

## 打开与依赖

使用 **Godot 4.7.2 标准版**及同版本导出模板。维护者生成的完整 `moss_duel_gec.zip` 已包含 SQLite 依赖；若直接下载仓库源码，原生二进制不在 Git 中，需先从[通用 GEC 包](../../docs/gec_godot.md#1-获取通用依赖)复制 `addons/godot-sqlite/bin/` 到本工程同名位置。接线、GEC 源码及其余文件已经齐备，不必重新写代码。

在 Godot 导入本目录的 `project.godot`。Web 桥接和打包脚本已配置，不要再用独立版的预设覆盖它们。

## 通过 GEP 运行 Web 版

1. 用 **Web** 预设导出到 `exports/web/index.html`，取消调试导出。
2. 在脚本编辑器运行 `package_web.gd`，选择刚导出的 `index.html`；使用成功弹窗给出的 ZIP。
3. 在 GEP 的“实验上传与发布”上传 ZIP，再点击“预览（不上传数据）”。先出现 GEC，点击开始后进入游戏；预览只在本机保存。
4. 发布版本，在“招募与门户”设为当前版本并开放新参与，再从参与入口打一局。结束后核对完成状态及导出数据。

三种参与方式都由 GEP 选择，无需重写游戏。原生版按[通用 GEC 导出说明](../../docs/gec_godot.md#原生)包装，在 GEP 发布后下载完整实验包；只运行原始 EXE 或 `.app` 不会自动得到连接配置。

这份示例不恢复战斗进度，也不改变 GEP 已保存的旧会话或待传数据。玩法与按键见[独立版说明](../moss_duel/README.md)；数据只用于合成接入练习，不构成科学计时验收。
