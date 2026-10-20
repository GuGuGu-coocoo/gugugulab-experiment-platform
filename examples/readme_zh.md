# Moss Duel 示例

[English](README.md) · 中文

同一个游戏提供两份工程：

在 GitHub 点击 **Code → Download ZIP**，解压后打开下表对应的工程目录。维护者也可用 `tools/moss_duel_examples.py` 生成两份独立的完整 ZIP，命令见[维护者说明](../docs/maintainer.md)。

| 工程 | 内容 | 适合做什么 |
|---|---|---|
| [moss_duel](moss_duel/README.md) | 完全独立的游戏，没有 GEC、服务器配置或账号要求 | 先玩游戏，再亲手从零接入 GEC |
| [moss_duel_gec](moss_duel_gec/README.md) | 同一游戏，已接好 GEC 入口、事件记录、结束页面及导出配置 | 直接体验 GEP 流程，或对照自己的接线 |

想从零练习，请从 **moss_duel** 开始，复制工程后按[手动接入指南](../docs/moss_duel_gec.md)操作，不要把已接入版当作干净工程。通用 GEC 下载包仍独立于游戏。

两份工程都使用 Godot 4.7.2。已接入版没有固定服务器、研究或账号凭据；Web 连接由 GEP 提供。依赖与构包见各工程 README。旧合成宿主与旧接线层只作为 `tests/fixtures/` 中的回归测试材料，不再作为读者示例。
