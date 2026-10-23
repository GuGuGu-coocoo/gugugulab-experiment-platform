# 升级到 GEP 0.1.1

[English](upgrade_0_1_1_en.md) · 中文

适用于原 Docker 0.1.0，或此前已安装安全候选镜像的实例。全新安装按[部署教程](deployment.md)操作。0.1.0 的标签与安装包保留，不覆盖。

## 升级前确认

安排停写窗口，先让正在进行的任务结束，并保留被试原设备、浏览器存储及未上传文件。升级不会把旧任务自动接到新来源。先在新的合成数据卷演练，再更新实际部署；不要将真实数据提交给公共测试或第三方服务。

- 保留原部署目录、`.env`、`GEP_PROJECT_NAME`、`GEP_VOLUME`、`GEP_EXPECTED_INSTANCE`、端口、API 地址和账号。不要在新解压目录运行启动器来“升级”，那会创建另一个实例。
- 暂禁 JSON Schema 的 `pattern`／`patternProperties`：使用这些规则的旧发行不能新建参与，相关事件上传返回 `schema_regex_unsupported`。既有数据和待传队列保留；先确认影响并准备不含正则的新实验版本，不能删字段、放松校验或重标旧记录来绕过。
- 公网部署必须准备 `*.experiment.<原域名>`，并使用新版 `compose.public.yaml` 与 `deploy/Caddyfile`。每个发行／预览改用独立来源；只换应用镜像或代理文件都不够。现有根域名、网站、管理地址和 API 地址保持不变。自有代理须实现相同隔离，不能把实验回退到共享来源。
- 本地使用继续用 `compose.yaml`，无需域名或证书。HTTPS 用户需重新登录并刷新旧表单；不会重置账号密码。新 Cookie 名称不能撤销已被盗的旧凭据。

## 1. 下载到单独目录

从 [0.1.1 发布页](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/releases/tag/v0.1.1)下载并完整解压 `gep_0_1_1_docker_linux_amd64.zip`，不要覆盖原 `.env`。只支持 Linux amd64 容器，包括 Windows Docker Desktop 的 Linux 引擎。安装包同时含 GEP 与修补后的 Caddy 镜像；没有要求从镜像注册表拉取 `gep:0.1.1`。

下面命令均从**原部署目录**执行。将 `gep_new`／`$new` 换成新版解压目录；本地用 `compose.yaml`，公网改用 `compose.public.yaml`，不要叠加两份配置。每一步报错就停止，保留现场，不继续启动或重新初始化。

## 2. 停止并备份完整卷

### Linux

```sh
set -eu
umask 077
gep_compose=compose.yaml
gep_new=/path/to/gep_0_1_1_docker_linux_amd64
gep_backup=$(mktemp -d ./backup_0_1_1_XXXXXXXX)
sudo docker compose --env-file .env -f "$gep_compose" stop
cp compose*.yaml "$gep_backup/"
cp -R deploy "$gep_backup/"
sudo cat .env > "$gep_backup/config.env"
sudo docker compose --env-file .env -f "$gep_compose" run --rm -T --no-deps \
  --entrypoint tar gep -C /data -czf - . > "$gep_backup/data.tar.gz"
tar -tzf "$gep_backup/data.tar.gz" >/dev/null
```

### Windows PowerShell

使用 PowerShell，不在 PowerShell 5.1 中用 `>` 重定向二进制归档。以下直接让容器写入新的备份目录：

```powershell
$ErrorActionPreference = 'Stop'
function Confirm-Docker { if ($LASTEXITCODE -ne 0) { throw 'Docker failed; stop here and keep existing data.' } }
$compose = 'compose.yaml'
$new = 'C:\path\gep_0_1_1_docker_linux_amd64'
$backup = Join-Path (Get-Location) ('backup_0_1_1_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $backup | Out-Null
$acl = New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$acl.SetOwner($me)
foreach ($sid in @($me, (New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')))) {
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $backup -AclObject $acl
docker compose --env-file .env -f $compose stop; Confirm-Docker
Copy-Item .env, compose*.yaml -Destination $backup
Copy-Item deploy -Destination $backup -Recurse
docker compose --env-file .env -f $compose run --rm --no-deps -v "${backup}:/backup" --entrypoint tar gep -czf /backup/data.tar.gz -C /data .; Confirm-Docker
docker compose --env-file .env -f $compose run --rm --no-deps -v "${backup}:/backup:ro" --entrypoint tar gep -tzf /backup/data.tar.gz | Out-Null
Confirm-Docker
```

两个平台都需将备份转存到独立、受保护的位置。卷内有数据库、账号、实例密钥、程序包及导出，不能只备份 SQLite 或上传到 Issue。公网部署还要完整备份原项目的 Caddy `/data` 与 `/config` 两个证书／配置卷；确认备份有效后再继续。归档可读取不等于已经验证恢复，正式使用前应在另一个新卷演练整卷恢复。

## 3. 载入镜像并更新原部署文件

Linux：

```sh
sudo docker image load --input "$gep_new/gep-image.tar"
cp "$gep_new/compose.yaml" "$gep_new/compose.public.yaml" .
cp "$gep_new/deploy/Caddyfile" "$gep_new/deploy/linux.sh" "$gep_new/deploy/windows.ps1" "$gep_new/deploy/start_windows.cmd" deploy/
cp "$gep_new/docker-release.json" .
sudoedit .env
```

Windows PowerShell：

```powershell
docker image load --input "$new\gep-image.tar"; Confirm-Docker
Copy-Item "$new\compose.yaml", "$new\compose.public.yaml", "$new\docker-release.json" -Destination .
Copy-Item "$new\deploy\Caddyfile", "$new\deploy\linux.sh", "$new\deploy\windows.ps1", "$new\deploy\start_windows.cmd" -Destination deploy
notepad .env
```

有自定义代理或 Compose 时先合并差异，不直接覆盖。只修改／补充原 `.env` 的下列设置，其他值保持不变：

```dotenv
GEP_IMAGE=gep:0.1.1
GEP_CADDY_IMAGE=gep-caddy:2.11.7-go1.26.10
GEP_SETUP_ENABLED=0
```

保存编辑后再执行下一步。不要复制 `.env.example` 覆盖原文件，不改卷名、实例 UUID、项目名或域名。

## 4. 显式迁移原库

下面一条命令在 Linux 和 PowerShell 都可使用。将 `compose.yaml` 换成自己第 2 步选用的文件名：

```sh
docker compose --env-file .env -f compose.yaml run --rm --no-deps gep python -c "from gep.runtime import configure, verify; configure(); import django; django.setup(); verify(); from django.core.management import call_command; call_command('migrate', interactive=False)"
```

Linux 若需要 Docker 管理权限，在命令前加 `sudo`；PowerShell 紧接着执行 `Confirm-Docker`。成功应包含 `0016_browser_origin`，已装安全候选版则可能显示无待执行迁移。应用启动不会代你迁移。

迁移核对卷标记与数据库实例，只新增公开路由标签及框架默认权限，不重建 Owner、重设密码或改写原会话／事件／冻结包。迁移失败时保持服务停止并检查日志；不要删卷、重复初始化或用旧程序写入迁移后的库。

## 5. 启动并核对

```sh
docker compose --env-file .env -f compose.yaml up -d --wait --wait-timeout 120
docker compose --env-file .env -f compose.yaml ps
```

同样替换实际 Compose 文件，Linux 按需加 `sudo`，PowerShell 每条 Docker 命令后执行 `Confirm-Docker`。用原账号登录，核对研究、成员权限、参与状态和旧导出；先用合成研究验证新上传、预览、独立来源参与、完成和导出。公网还要核对精确来源 CORS、证书链与后台 `__Host-` Cookie，不能忽略证书错误。

旧来源的 `/local-data` 保留按原绑定补传的入口，不自动搬迁数据或恢复旧任务。保留所有旧浏览器存储、待传记录和失败提示；旧缓存／Service Worker 及潜在凭据泄漏需单独处理。禁止 `down -v`、卷 prune、删库或以清缓存解决升级问题。

需要回退时先停止写入，保留失败现场；把升级前整卷及配置恢复到**另一个新卷／隔离目录**，核对账号、实例、权限、撤销状态和导出后，再由维护者决定切换。不能把备份覆盖到仍有新数据的卷，也不能只把 `GEP_IMAGE` 改回 0.1.0。

## 范围与未关闭风险

GEC SDK 仍为 0.1.0，协议 `gep/1`、科学任务、事件内容和导出编码不变。保留 0.1.0 不表示建议继续使用其未修补安装包。安全补修和隔离验证见[审计报告](SECURITY_AUDIT.md)；旧浏览器、已泄漏凭据、密码政策、容量、宿主及实际 DNS／ACME、既有 SDK harness 失败没有因此关闭。本次发布不代表所有测试通过、系统绝对安全或真实研究获批。
