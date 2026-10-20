# GEP — GuguguLab Experiment Platform

当前版本：**0.1.0**（2026-10-20）。研究者后台、参与门户及 GEC 实验接入，面向合成数据测试。用户已完成本轮体验并确认通过；具体工程证据与未验证范围见[验收记录](docs/verification.md)。本版本不批准真实研究或生产部署。

GEP 管理研究、账号权限、实验上传与发布、招募、参与记录及数据导出。GEC 在参与前处理准入，在实验中本地保存并上传数据，在结束后显示完成状态。支持无需 ID、ID、ID 与密码三种参与方式。

## 启动

```sh
uv sync --frozen --python 3.12
pnpm install --frozen-lockfile
.venv/bin/python tools/dev_instance.py
.venv/bin/python tools/serve.py
```

打开 `http://admin.localhost:8000/login`。首次初始化生成本地随机合成账号；已有实例按[启动指南](docs/quickstart.md)接续，不重新初始化或覆盖数据。参与门户使用 `http://experiment.localhost:8000/`。

## 使用与接入

- [研究者指南](docs/researcher.md)：创建研究、名单、上传、发布、招募、参与记录与导出。
- [GEC Godot 接入](docs/gec_godot.md)与[实验开发者指南](docs/experiment_developer.md)：通用依赖、事件与打包。
- [Moss Duel 示例](examples/moss_duel/README.md)及[接入教程](docs/moss_duel_gec.md)：独立游戏与接入副本分开使用。
- [维护者指南](docs/maintainer.md)、[部署说明](deploy/README.md)、[协议](docs/protocol.md)。
- [人工测试指南](docs/researcher_acceptance.md)与[工程验收记录](docs/verification.md)。

## 0.1.0

后台按研究分区，提供账号与权限管理、Excel 名单与权限导入、唯一当前招募版本、Web 隔离预览、原生程序分发及受保护的数据快照。修改权限后提交并用本人密码确认。

GEC 默认中文，可切换 English；开始与结束页面居中，配色与 GEP 一致。结束语为“实验结束，感谢您的参与。”数据工具默认折叠。参与次数上限为 1000；后台完成状态显示“已完成／未完成／进行中”。恢复入口暂不公开，已有本地保存和补传机制保留。

本地交付集中在 `build/v0_1_0/`，不进入 Git。源码版本由 `v0.1.0` 标签定位；变更见 [CHANGELOG](CHANGELOG.md)。凭据、数据库、内部开发记录、下载及恢复文件不随源码发布。

## 支持范围与限制

Godot 4.7.2；Web 使用 Chrome，原生已有 macOS arm64 与 Windows x64 的合成工程证据。历史 Windows 原生、Excel 和可靠性结果只对应当时冻结的构建；本轮界面改动没有重新进行完整原生实机验收。其他浏览器、原生架构、代码签名与公证不在当前通过声明中。

任务结束、本地保存与服务器收齐是不同事实；界面“已完成”不等于科学验收。物理断电、科学计时、容量上界、独立备份恢复及真实数据治理仍需各自验证。Phase 04、VPS／DNS／TLS 变更和真实研究未启用。

采用 [MIT License](LICENSE)；第三方许可见 [third_party](third_party/)。
