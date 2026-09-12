# 合成环境启动与演示

本版只允许合成工程验证。已实测 macOS 26.6.2 arm64、Chrome 152.0.7977.77、Godot 4.7.2；原生包为 universal，实际运行架构为 arm64。Windows 实体 LAN 基础场景已实测；Docker、完整 LAN 故障矩阵、独立人类或真实研究验收尚未完成，详见 [验收记录](verification.md)。

## 服务端

需要 Python 3.12、uv、Node 24、pnpm。依赖版本和发行摘要分别锁在 `uv.lock`、`pnpm-lock.yaml` 和模板安装脚本中。

```sh
uv sync --frozen --python 3.12
pnpm install --frozen-lockfile
.venv/bin/python tools/dev_instance.py
.venv/bin/python tools/serve.py
```

`dev_instance.py` 只接受不存在的 `local_data/`，生成随机合成 Owner 密码、实例 UUID 和服务器秘密。凭据文件为 `local_data/dev_credentials.json`，权限 0600；用本地编辑器查看，不把内容粘进日志或仓库。再次启动只运行 `serve.py`，不会重建 Owner。数据库、包和秘密都留在被忽略的 `local_data/`。

在 Chrome 打开 `http://admin.localhost:8000/login`。实验使用 `http://experiment.localhost:8000`，两个 hostname 隔离管理 Cookie。服务仅绑定本机回环。LAN 辅助命令见文末；不要把 localhost 发给另一台电脑。公网部署未启用。

## 构建依赖

安装官方 Godot **4.7.2** 和匹配的 macOS 导出模板。macOS 模板使用 universal 二进制。下载以下官方归档到任意本地临时目录，再运行安装器；参数替换为实际下载文件路径：

- [Godot 4.7.2 export templates](https://github.com/godotengine/godot-builds/releases/download/4.7.2-stable/Godot_v4.7.2-stable_export_templates.tpz)
- [Godot SQLite v4.7 demo archive](https://github.com/2shady4u/godot-sqlite/releases/download/v4.7/demo.zip)

```sh
.venv/bin/python tools/install_web_templates.py /path/to/Godot_v4.7.2-stable_export_templates.tpz
.venv/bin/python tools/install_sqlite.py /path/to/demo.zip
.venv/bin/python tools/build.py
```

安装器先核对固定 SHA-256，再选择必要文件。Web 模板放在项目 `.godot/`，SQLite 框架放在被忽略的插件 `bin/`。不需要重新克隆仓库。构建得到 `build/synthetic_web.zip`、`build/native/synthetic.zip`、原生 `.app` 及 `build/native/descriptor.json`。

## 研究者 GUI

1. 登录后创建研究，先设置无需 ID、名单 ID 或 ID+密码模式，以及每个名单 ID 的参与次数。名单每行一个 ID；密码模式用 Tab 分隔 ID 与密码。保留 `001`。首次批准发行后政策冻结。
2. 原生路径：粘贴 `build/native/descriptor.json` → 登记构建 → 批准合成发行 → 导出 `connection.json` → 保存到 `.app` 同级目录。开放招募后独立启动该 `.app`。大程序无需上传服务器；连接配置不参与原程序摘要。
3. Web 路径：上传 `build/synthetic_web.zip` → 验证成功 → 隔离预览（仅本地保存）→ 批准合成发行 → 开放招募 → 打开参与入口。预览没有管理 Cookie，也不创建正式采集会话。
4. 点击开始，按左／右方向键完成两个 trial。任务结束与服务器收齐是不同状态。断网时本地保存，恢复网络后补传。关掉程序后不保证上传；重开原存储环境后队列继续按原目标处理。
5. 研究状态页创建固定快照。JSONL 为原始记录，metadata 包含冻结 build/schema/codebook 与会话收尾信息；三个下载入口都重新检查权限。
6. CSV 是有限、保真文本格式：固定来源列加 `record_json`，其值为 `json:` 后接完整 JSON。它不展开或分析科学字段。Excel 使用 UTF-8、逗号、双引号文本限定符导入；读取程序去掉 `json:` 后解析 JSON。null、缺失、前导零、多响应不转换，公式样文本不求值。
7. 邀请按当前可委派权限签发，24 小时有效，可撤销。新账号自行设密码；已有账号先登录再接受，不改其密码。撤销成员权限立即影响后续下载。

上传上限 128 MiB、解压上限 256 MiB、最多 256 个文件。页面显示传输进度；失败后重新上传，未完成包不进入 release，不覆盖旧构建。首版不支持断点续传或 CDN 自动转存。

## 同设备恢复

合成任务明确使用 `trial_boundary_v1`，检查点与依赖记录原子提交。研究人员在状态页为目标 session 签发 15 分钟一次性恢复许可；参与端填写该 session UUID 与许可。仍须持有原浏览器／原生存储中的私密证明，公开 UUID 不够。新参与锁住旧前台恢复，不展示旧答案或旧 ID。

恢复保留 session，创建新 segment 与时钟 epoch；完整 trial 不重放。未声明兼容策略、存储版本不支持、找不到依赖或凭据无效时停止任务恢复并保留数据，需要研究人员处理。自动恢复仅覆盖此合成策略，不是通用实验恢复引擎。

只有完整声明已收齐、合法确认已本地提交、无 pending 和恢复依赖后，GEC 才清理原始副本、检查点及凭据。活动实验不能因批次 ACK 清理。导出不会删除服务器数据。原生和 Web 的清理各自使用本地事务；完成墓碑不含答案或凭据。

## 验证命令

保持服务运行，然后：

```sh
.venv/bin/pytest -q
.venv/bin/python tools/verify_browser.py
```

浏览器验收会先通过 GUI 登记新的合成发行，再运行实际 Chrome、独立原生程序和原生 SQLite 故障场景。所有账号和数据都是合成的；每次运行增加独立测试对象，不清空数据库。测试不代表独立研究人员验收。

单独验证无 GEC 的本地后端：

```sh
godot --headless --path examples/synthetic_experiment -- --local --synthetic-auto
```

返回 `local_committed` 和 `remote_status: unsupported`，不伪造服务器确认。普通运行去掉自动输入参数。

## 最小 Compose（隔离合成环境已实测）

`compose.yaml` 和 `deploy/Dockerfile` 已提供：外部指定卷、非 root、只读容器根目录、无额外 capability、显式实例标记。启动必须同时有数据库、秘密和匹配实例标记；不会自动创建空 Owner。镜像依赖用带哈希的 `requirements.txt`。

2026-09-12 已在专用 Lima 2.2.0 / Linux arm64 / Docker 29.1.3 / Compose 2.40.3 环境完成真实原生上传闭环、容器重启/替换持久化、Owner 保留、重复初始化拒绝及错卷拒绝。初始化和启动步骤见[Compose 指南](../deploy/README.md)。这不是正式部署或备份恢复验收。

## LAN acceptance helpers

`tools/serve_lan.py --ip <private-ip> --cert <certificate.pem> --key <private-key.pem>` starts the existing synthetic instance on port 8443 with direct TLS. Certificate trust must be explicitly arranged on the participant device; this command does not install certificates or change firewall rules. The experiment IP cannot access management routes. Keep the original loopback management service running separately.

`tools/verify_lan_evidence.py --study <study-uuid> --evidence-dir <private-evidence-directory> --ssh-host <authorized-ssh-alias>` compares saved recovery JSON against the guarded synthetic database and reads Windows/Chrome version metadata over an existing SSH connection. It does not read clipboard contents or arbitrary remote files, and cannot infer physical unplug/reopen actions. Keep evidence outside Git.

Web setup fields use native browser inputs for paste and IME. Recovery JSON contains the current authorized session's records, checkpoint and pending IDs, without its bearer token or private recovery proof. Exporting this file does not delete pending data. Unspecified task recovery policy permits authorized data recovery only; it does not resume trials.
