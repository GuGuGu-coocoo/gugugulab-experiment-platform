# GEP — GuguguLab Experiment Platform

GEP 提供研究者后台与数据服务，GEC 为实验提供本地优先的数据接入。当前已有可运行的**隔离合成工程版**，尚未完成 Phase 01–03 全部验收，不能用于真实研究收数。

同一个 Godot 合成任务通过自己的通用数据模块运行；本地后端、Web IndexedDB 后端和原生 SQLite 后端由入口选择。任务包含 RT／选择和不规则嵌套事件。后台提供三种准入、构建登记、Web 包上传／预览／批准、原生连接配置、状态、受控恢复、受保护固定快照与成员权限。

已实测 Godot 4.7.2、macOS 26.6.2 arm64、Chrome 152：Web 和独立原生程序的数据经本地事务、真实 HTTP API、数据库、ACK 和 JSONL 导出对账；覆盖断网、丢 ACK、进程终止、完整 trial 恢复及清理边界。实际支持范围和未执行项见[验收记录](public_docs/verification.md)。

## 启动

```sh
uv sync --frozen --python 3.12
pnpm install --frozen-lockfile
.venv/bin/python tools/dev_instance.py
.venv/bin/python tools/serve.py
```

打开 `http://admin.localhost:8000/login`。首次初始化生成本地随机合成凭据，再次启动不重建账号。[完整构建、配置、演示和验收步骤](public_docs/quickstart.md)。

## 当前边界

- 本机 Web 与 macOS 原生合成链路已运行；其他平台不承诺兼容。
- 完成任务、本地保存、服务器收齐和科学验收是不同事实。
- 只支持原设备／原存储的明确 trial 恢复；公开 ID 不能取回旧答案或接管会话。
- 最小 Compose 已有定义，但本机没有容器运行环境，尚未完成运行验收。Windows 11 / Chrome 的实体 LAN 基础链路已验；独立研究者操作和原生窗口可视检查仍待验。
- Runner、Local Launcher、自动分析、真实研究和生产部署未启用；部分完整故障矩阵仍未完成，详见验收记录。

接口：[GEP/1 协议](public_docs/protocol.md)。依赖：[第三方声明](public_docs/third_party.md)。

## 许可

GEP 与 GEC 的项目原创代码采用 [Apache License 2.0](LICENSE)。允许使用、修改、分发与商业使用，具体义务以许可证正文为准。

第三方依赖及材料保留各自的许可证，见[第三方声明](public_docs/third_party.md)。研究者上传的实验、量表、刺激材料及被试数据不因使用本软件而自动适用本许可证。
