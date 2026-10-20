# GEP 0.1.0

English · 中文

## English

Self-host GEP with the attached `gep_0_1_0_docker_linux_amd64.zip`. It includes the Linux amd64 image, Compose configurations, Windows/Linux launchers and Chinese/English guides. The server does not require a host Python, Node or Godot installation.

On Windows, start Docker Desktop in Linux container mode, extract the archive and open `deploy/start_windows.cmd`. On a Linux server, follow the [deployment guide](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.0/docs/deployment_en.md). First setup lets you choose the Owner account and password. The domain is optional; entering it does not change DNS or enable public access.

GEP provides study management, Web/native publication, three participation modes, rosters, permissions, completion records and exports. GEC connects Godot experiments while preserving the task developer's responsibility for task logic and timing.

[GEP user guide](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.0/docs/researcher_en.md) · [GEC developer guide](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.0/docs/gec_godot_en.md)

The image is not an ARM build. Recovery controls are not exposed. Back up the existing volume and `.env` before upgrading; do not reinitialize them. Public DNS/TLS and real-study arrangements need verification in your own environment. The earlier source-only 0.1.0 snapshot is preserved at `v0.1.0-source-snapshot-20261020`; `v0.1.0` now identifies this Docker release.

## 中文

下载附件 `gep_0_1_0_docker_linux_amd64.zip`，包含 Linux amd64 镜像、Compose 配置、Windows／Linux 启动脚本和中英教程。服务器不需要安装 Python、Node 或 Godot。

Windows 启动 Docker Desktop 的 Linux 容器引擎后，完整解压并双击 `deploy/start_windows.cmd`。Linux 服务器按[部署教程](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.0/docs/deployment.md)操作。首次设置自行填写 Owner 账号密码；域名可选，填写不会自动修改 DNS 或启用公网。

GEP 提供研究管理、Web／原生实验发布、三种参与方式、名单、权限、完成记录和导出；GEC 负责 Godot 接入，不替开发者决定任务逻辑或科学计时。

[GEP 使用教程](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.0/docs/researcher.md) · [GEC 开发者文档](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.0/docs/gec_godot.md)

镜像不适用于 ARM，恢复入口暂不公开。升级前备份原卷和 `.env`，不重新初始化。实际公网 DNS／HTTPS 和研究使用条件仍需在自己的环境核对。旧源码快照保留为 `v0.1.0-source-snapshot-20261020`，`v0.1.0` 按确认更新为本次 Docker 发行。
