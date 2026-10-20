# GEP — GuguguLab Experiment Platform

[English](README.md) · 中文

GEP 是可以部署在自己服务器上的实验管理平台，用来发布实验、管理参与和收集数据。GEC 负责把 Godot 实验接入 GEP，处理参与入口、本地保存、上传和结束页面；实验任务本身仍由你的项目控制。

## 下载与部署

[下载 GEP 0.1.0](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/releases/tag/v0.1.0)。发行包包含 **Linux amd64 Docker 镜像**、Compose 配置和 Linux／Windows 启动脚本。服务器不用安装 Python、Node 或 Godot。ARM 服务器需要另行构建，不能直接使用这个镜像。

- **Windows：**安装 Docker Desktop，启动 Linux 容器引擎，解压发行包，双击 `deploy/start_windows.cmd`。
- **Linux 或 VPS：**安装 Docker Engine 和 Compose，解压发行包，运行 `sudo sh deploy/linux.sh start`。

在部署电脑上打开 `http://admin.localhost:8080/login`，首次设置时自行填写 Owner 用户名和密码。域名可以不填，留空即本地使用。远程服务器先通过 SSH 转发完成设置，再启用公网访问。

[部署教程](docs/deployment.md)从拿到服务器开始，介绍 Docker 安装、首次设置、域名、HTTPS、升级和备份。向导里填写域名只会保存配置，不会修改 DNS 或自动开放公网。

## 可以做什么

- 在研究工作台管理实验，在独立参与门户展示研究。
- 使用无需 ID、ID 或 ID 加密码三种参与方式，通过电子表格导入名单。
- 上传 Web 实验，隔离预览、发布，并为每个研究选择一个当前版本。
- 分发受支持的 macOS 和 Windows 原生实验包。
- 查看参与记录，按完成状态筛选，按权限导出数据。
- 管理 Owner、Admin 和成员账号，按研究分配可见性和权限。

GEP 和 GEC 界面支持中文和英文。0.1.0 暂不公开恢复入口；已有本地数据和待上传记录仍保留。

## 教程

| 使用者 | 从这里开始 |
|---|---|
| 服务器维护者 | [从服务器到 Docker 部署](docs/deployment.md) |
| 研究者 | [GEP 图文使用教程](docs/researcher.md) |
| Godot 开发者 | [GEC 接入、事件记录与打包](docs/gec_godot.md) · [GEC_SDK 源码](GEC_SDK/) |

## 游戏示例

Moss Duel 分为[干净独立版](examples/moss_duel/README.md)和[已接入 GEC 版](examples/moss_duel_gec/README.md)。想从零练习，请复制独立版并按[手动接入指南](docs/moss_duel_gec.md)操作；想先试 GEP 完整流程或对照接线，可用已接入版。两份工程的游戏脚本和素材相同，详见[示例总览](examples/readme_zh.md)。

另有[协议](docs/protocol.md)、[安全说明](docs/security.md)和[工程验证记录](docs/verification.md)。

## 使用范围

GEC 面向 Godot 4.7.2，Web 使用 Chrome，原生平台为 macOS arm64 和 Windows x64。Docker 镜像运行在 Linux 容器引擎中，包括 Windows 的 Docker Desktop。验证记录只说明实际检查过的版本和环境，不代表任意研究的科学计时、容量或被试数据收集条件已经通过。开展研究前，应落实同意、数据保留、访问权限和备份安排。

代码与文档采用 [Apache License 2.0](LICENSE)，第三方组件保留各自[许可](third_party/)。反馈问题时请用公开或合成示例，不上传被试记录、密码和恢复文件。
