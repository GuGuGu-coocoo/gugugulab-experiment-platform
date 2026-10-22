# 从服务器到 GEP 部署

[English](deployment_en.md) · 中文

这份教程使用 GEP 0.1.0 的 Docker 发行包。可以先在本机运行，也可以部署到 VPS 或自己的服务器。以下 Linux 步骤以全新的 Ubuntu 24.04／26.04 LTS、x86_64 服务器为例；发行镜像是 linux/amd64，不适用于 ARM 服务器。

部署前先看[安全审计与未关闭风险](SECURITY_AUDIT.md)。审计分支的补丁尚未替换 0.1.0 附件；生产镜像、宿主 PHP、证书、日志与备份仍需在实际环境核查。

## 1. 准备服务器

准备服务器的 IP、SSH 登录账号和 sudo 权限。确认有可用磁盘，并安排独立备份；本项目没有给出经实测的并发人数或最低内存承诺，配置应按实验包大小和实际负载选择。

在自己的电脑打开终端，用服务商给出的账号登录：

```sh
ssh your_user@your_server_ip
```

把示例账号和 IP 换成自己的。第一次连接时，核对服务商控制台提供的 SSH 主机指纹，不忽略指纹变化。若服务器已有网站、Docker 或其他业务，先核对端口和数据，不覆盖原配置，也不要照搬卸载旧组件的命令。

无域名也能先部署并通过 SSH 使用。有公网访问需求时，之后再准备自己管理的域名。自有服务器若在路由器后面或使用运营商共享地址，填写域名不会自动让它能从互联网访问。

## 2. 安装 Docker

### Ubuntu 服务器

Docker 已可用时，直接检查 `sudo docker version` 和 `sudo docker compose version`，不重复安装。全新 Ubuntu 可按下面步骤添加 Docker 官方软件源：

```sh
sudo apt-get update
sudo apt-get install -y ca-certificates curl unzip
sudo install -d -m 0755 /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
  "${UBUNTU_CODENAME:-$VERSION_CODENAME}" "$(dpkg --print-architecture)" \
  | sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
```

应能看到客户端、服务端和 Compose 版本。其他系统按 [Docker 官方安装说明](https://docs.docker.com/engine/install/)选择对应发行版，不把 Ubuntu 软件源加到其他系统。这里使用 sudo，不要求把普通账号加入具有高权限的 docker 用户组。

### Windows 本机

安装并打开 [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)，由你阅读并决定是否接受协议。确认 Linux 容器引擎正在运行。下载并完整解压 GEP 发行包后，双击 `deploy/start_windows.cmd`，接着进行第 4 节的首次设置。部署 GEP 不需要另外安装 Python、Node 或 Godot。

## 3. 下载并启动 GEP

Linux 服务器上建立一个自己的部署目录：

```sh
mkdir -p gep
cd gep
curl -fL -o gep_0_1_0_docker_linux_amd64.zip \
  https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/releases/download/v0.1.0/gep_0_1_0_docker_linux_amd64.zip
unzip gep_0_1_0_docker_linux_amd64.zip
cd gep_0_1_0_docker_linux_amd64
sudo sh deploy/linux.sh start
```

第一次启动会载入包内镜像、创建专用持久卷并打开设置页。`.env` 记录卷名和预期实例 UUID，不含 Owner 密码。正常结果是服务健康；此时还没有 Owner，也没有研究。

以后应在这个目录启动、停止或查看状态：

```sh
sudo sh deploy/linux.sh status
sudo sh deploy/linux.sh stop
sudo sh deploy/linux.sh start
```

保留 `.env` 和数据卷。启动失败时先看日志，不删除卷来“重新开始”。

## 4. 创建 Owner

本机部署直接打开 `http://admin.localhost:8080/login`。VPS 或另一台服务器上的部署，先在**自己的电脑**另开终端，建立 SSH 转发：

```sh
ssh -N -L localhost:8080:127.0.0.1:8080 your_user@your_server_ip
```

保持这个窗口运行，再在自己电脑的 Chrome 打开 `http://admin.localhost:8080/login`。8080 已被本机其他软件占用时，先解决端口冲突，不复用不明服务。

在“首次设置”填写：

1. Owner 用户名。
2. 密码和确认密码。至少 6 位，包含 ASCII 大写字母、小写字母、数字和可见标点；空格不算符号。
3. 域名可不填。若填写，使用根域名，例如 `example.com`，不要带 `https://`、端口或路径。

点击“完成设置”，看到“设置完成”后进入管理后台，用刚才的账号登录。自填密码只保存认证哈希，不生成明文初始密码文件。Owner 负责管理平台账号和授权，请自行妥善保存凭据。

本地参与门户为 `http://experiment.localhost:8080/`。localhost 指访问者自己的电脑，不能把这个地址直接发给远程被试。首次向导完成后关闭，重启不会重新创建 Owner。

## 5. 可选：使用域名和 HTTPS

只在本地使用时，到这里就已经能进入 GEP，不需要本节。向导填写域名只保存地址及待启用文件，**不修改 DNS，也不启用公网**。

准备公网访问时，以 `example.com` 为例，在自己的 DNS 服务商处新增：

| 记录 | 指向 |
|---|---|
| `admin.example.com` | GEP 服务器的公网地址 |
| `experiment.example.com` | 同一服务器的公网地址 |

只有实际配置了 IPv6 才添加 AAAA；错误的 AAAA 会影响连接和证书验证。保留原个人网站的根域名／www 记录。普通 DNS 模式就够了，不需要给 GEP 提供 DNS 服务商密钥。

确认服务器的公网入口允许 TCP 80 和 443，SSH 仍仅按自己的管理安排开放。不要开放 8000 或 8080。Docker 发布的端口可能绕过 ufw 的常规规则，应同时核对云安全组和 Docker 网络规则，见 [Docker 防火墙说明](https://docs.docker.com/engine/network/packet-filtering-firewalls/)。不要为本教程覆盖已有防火墙。如果 80／443 已由自己的代理占用，让原代理转发两个域名；不要直接停掉已有网站来腾端口。

回到部署目录，用 `sudoedit .env` 编辑配置（首次通过 sudo 启动后，该文件由 root 持有），将现有空行改为：

```dotenv
GEP_BASE_DOMAIN=example.com
```

替换为自己的域名，保留 `GEP_VOLUME`、`GEP_PROJECT_NAME`、`GEP_EXPECTED_INSTANCE` 等原有值。然后切换到**独立的公网配置**：

```sh
sudo docker compose --env-file .env -f compose.yaml stop
sudo docker compose --env-file .env -f compose.public.yaml up -d --wait --wait-timeout 120
sudo docker compose --env-file .env -f compose.public.yaml logs --tail 60 caddy
```

不要把两个 Compose 文件用 `-f` 叠加。公网配置不映射 GEP 应用端口，只有 Caddy 对外提供 80／443。Caddy 在 DNS 和网络条件具备后自动申请、续期证书；详见 [Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https)。它的证书卷也要保留。

现在检查 `https://admin.example.com/login` 和 `https://experiment.example.com/`。浏览器应认可证书，后台应可登录。看到证书警告时停止检查并诊断，不点击忽略。原 HTTP／localhost 登录 Cookie 不迁移到新域名，重新登录即可。

首次上线先用合成研究核对一次上传、实际参与、完成状态和导出。这里提供部署步骤，不把未在你服务器上运行过的步骤写成已验收。

## 6. GEP 的下一步

按 [GEP 使用教程](researcher.md)创建研究、配置名单、上传并预览实验、发布、选择当前版本，再开放参与。Godot 实验接入见 [GEC 开发者文档](gec_godot.md)。个人网站可以直接放参与门户链接，不需要把 GEP 后台嵌入网页框架。

## 7. 备份与升级

数据库、账号、秘密、程序包和导出都在持久卷中；仅备份数据库文件不够。对 Linux 的完整卷冷备份，可以在部署目录执行下面的命令。示例是**公网配置**；本地使用时将文件名改为 `compose.yaml`：

```sh
umask 077
gep_backup_dir=$(mktemp -d ./backup_XXXXXXXX)
sudo docker compose --env-file .env -f compose.public.yaml stop
sudo docker compose --env-file .env -f compose.public.yaml run --rm -T --no-deps \
  --entrypoint tar gep -C /data -czf - . > "$gep_backup_dir/data.tar.gz"
sudo cat .env > "$gep_backup_dir/config.env"
tar -tzf "$gep_backup_dir/data.tar.gz" >/dev/null
sudo docker compose --env-file .env -f compose.public.yaml up -d --wait
```

确认备份命令及归档检查成功后，把新建 `backup_…` 目录转存到独立、受保护的位置。备份内有服务器秘密，不上传公开仓库或 Issue；另行保留 Caddy 证书卷。命令每次新建目录，不覆盖旧备份。上面的二进制重定向是 Linux shell 用法，不要照搬到 Windows PowerShell 5.1。Windows 应在停止服务后用适用于当前 Docker Desktop 的卷备份方式；参见 [Docker 卷说明](https://docs.docker.com/engine/storage/volumes/#back-up-restore-or-migrate-data-volumes)。

升级前先备份，并在隔离副本上演练。加载新版发行包中的镜像，更新**原部署目录**的启动文件和 `GEP_IMAGE`，保留原 `.env` 的卷、项目名和实例 UUID，再用正在使用的 Compose 配置重建容器。涉及迁移的版本按该版升级说明执行。不要运行 `down -v`、卷 prune 或重新初始化原卷；回退也不能让旧程序盲读已经迁移的新库。

## 8. 常见问题

- **打不开首次设置：**先看 Docker 是否运行、服务是否健康、SSH 转发是否仍在，再核对地址与端口。不要用服务器 IP 冒充后台主机名。
- **再次启动出现登录页：**实例已设置好，这是正常结果，不应重新创建 Owner。
- **提示错卷或初始化未完成：**保留 `.env`、卷和日志，检查挂载与预期 UUID；不删数据重试。
- **域名已填但外网不可访问：**向导不操作 DNS／路由，按第 5 节完成并检查解析、端口和证书。
- **公网登录或表单被拒绝：**使用配套 HTTPS 配置，不随意信任客户端转发头，不关闭 CSRF。代理设置见[安全说明](security.md)。

0.1.0 的发布不代替研究伦理、同意、保留政策、容量及科学计时验证。已有工程记录见[验证说明](verification.md)。
