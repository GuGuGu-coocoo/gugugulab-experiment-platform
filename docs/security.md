# 安全边界

GEP 0.1.0 提供本地与显式启用的 HTTPS 部署配置。应用层防护不等于你的服务器已完成部署验收、渗透测试或被试数据治理；上线前先用合成数据核对链路。

2026-10-22 起的独立分支审计、证据等级与开放风险见 [SECURITY_AUDIT.md](SECURITY_AUDIT.md)。该分支已拆分实验／预览来源；下面的补丁均未进入已发布的 v0.1.0 镜像，旧设备缓存及真实部署仍未验证。

运行镜像在按哈希安装依赖后移除 pip，不在容器内安装或升级软件；更新依赖需重新构建镜像。公开 Compose 使用随包提供的重建 Caddy：功能版本保持 2.11.7，Go 1.26.10、x/net 0.60.0，两个构建基础镜像均固定摘要。固定输入不等于以后自动获得安全更新，维护者仍须定期核对公告并重新构建。隔离镜像、TLS 与合成备份恢复的实际结果见审计报告，不代替生产配置核查。

本次分支补丁在新参与准入前增加全局每分钟 600 次、同一直接来源每分钟 120 次的预算，再执行原有细分限速；事件上传和已有会话的标识、顺序、去重不改。代理后的多个参与者共用直接来源预算，不能用未验证的 X-Forwarded-For 绕过，部署者需据真实规模安排可信入口限速。

HTTPS 模式的后台 Session／CSRF Cookie 使用 `__Host-gep_admin`／`__Host-gep_csrf`，同时 Secure、Path=/、无 Domain，防止同站子域注入父域同名 Cookie 覆盖身份。仅 host-only／SameSite 不足以防止该覆盖。升级后需重新登录并刷新旧表单，不改密码、账号或数据库 Session；已被盗 Cookie 不据此自动撤销。本地 HTTP 保留原名称，公网仍须 HTTPS。

上传路由的匿名 POST 在 CSRF 尝试解析 multipart 正文前即拒绝；已登录请求仍执行原有 CSRF、账号状态及研究／操作授权。取得公开的 CSRF Cookie 不等于取得上传身份，限额也不能替代身份检查。

经用户确认，暂时禁止 JSON Schema 的 `pattern`／`patternProperties` 规则，避免回溯正则阻塞验证。新构建拒绝登记；旧发行不再允许新参与，涉及这些规则的事件批次整体拒绝（`schema_regex_unsupported`，422，`retryable:false`）。冻结定义、既有记录、旧导出和本地待传队列不改写或删除；需要开发者提供不含正则规则的新版本。普通数据字段仍可名为 `pattern`。这不是一般 Schema 复杂度／资源耗尽问题已全部解决的证明。

Web、原生 ZIP 和 XLSX 先核对全部成员的预算，再解压或解析任何成员，只支持 Stored／Deflate。XLSX 最多 256 个成员，原有 2 MiB 压缩／10 MiB 展开上限不变。API 请求根必须是 JSON 对象；不合法根值和超大整数返回受控 4xx，不回显原始内容。Docker 构建基线已在分支更新至固定摘要的 Python 3.12.15-slim；是否完成目标镜像构建与系统组件扫描，以审计报告为准。

## 分支中的来源隔离

`experiment.<域名>` 只运行平台门户、API 与可信的旧队列处理页；每个 Web 发行在 `r-<发行 UUID 的 hex>.experiment.<域名>`，每个预览在 `p-<构建 UUID 的 hex>.experiment.<域名>`。服务器只在相符的主机名提供相符程序，预览与发行不共用浏览器存储。GEC 的 API 地址保持原值；CORS 只接受精确 scheme／host／port，服务器再核对发行和会话归属，不使用通配 CORS 或跨源管理 Cookie。原生 Bearer／proof 授权不变。

旧 `/run/…/web/index.html` 由可信中转页接管，有旧记录时不会直接运行新任务。门户的“旧版本本机记录”（`/local-data`）可按原绑定补传，沿用原 ACK、暂停和退避，不搬迁到新发行、不重标、不删未确认数据，也不自动续试。只有已收齐的原有清理条件成立才清理。过期、正则拒绝或被删除研究的数据仍留本机；需联系研究者处理。新独立来源同样保留可信处理页，即使研究已删除也不重新提供实验程序。

迁移 `0016_browser_origin` 只新增公开来源标签表并登记既有 Web 构建／发行，不改原始记录、身份、会话、凭据或冻结包。标签不含参与者或研究标题，删除研究后仅保留标签以便旧设备访问可信处理页；它不能恢复研究或授予读写权。部署前仍需隔离迁移和完整卷恢复核对。

没有清理旧浏览器缓存／Service Worker，也不能证明旧 token 从未被复制。完整缓存的旧代码可能继续在旧来源离线执行，旧未完成任务不能跨来源自动续接；**这部分历史风险不能当作已关闭**。公开配置还需要 `*.experiment.<域名>` 的解析与逐名称 TLS，见[部署教程](deployment.md)；本轮未改实际 DNS／证书或服务器。

## 已有保护

Docker 首次设置仅在显式允许且数据卷完全为空时开放，限预设管理主机名，校验同源表单及 CSRF。完成后关闭，不覆盖已有或中断初始化的数据。使用者自填密码通过进程标准输入传递，仅保存认证哈希；域名字段只生成待启用配置，不执行外部 DNS 或证书操作。部署仍默认仅监听回环；不要把未初始化的设置页暴露到公网。

研究者后台与实验使用不同主机名，登录 Cookie 不跨主机共享；管理操作检查 CSRF、账号状态、当前权限和对象归属。导出下载时重新检查权限；上传包检查路径、结构、限额及平台内容。程序包不在服务器执行，私有数据目录不作为静态目录公开。

## 0.1.0 的应用层保护

- GEP 后台和门户的脚本按页面随机 nonce 授权，不允许任意内联脚本或 HTML 事件属性执行；仍保留现有主题样式。实验 Web 包沿用原有支持 Godot／WebAssembly 的策略，不改变已发布包的字节或接口。
- 页面禁止嵌入框架，降低点击劫持风险；个人网站应链接到参与门户，不嵌入后台页面。
- 跨站跳转不发送来源 URL；同站保留来源，避免破坏 Django 对正常表单的 CSRF 校验。早期请求拒绝、CSRF 错误与其他错误响应也带安全头，并禁止 HTTP 缓存；没有清理浏览器的 IndexedDB 或本地队列。
- CSRF Cookie 使用 SameSite=Strict；登录会话继续使用 host-only、HttpOnly 和 SameSite=Strict。启用 HTTPS 的环境仍需配置 `GEP_HTTPS=1`，本轮不强制本地 HTTP 跳转 HTTPS。
- 登录保留同一来源／账号每分钟 20 次的限速，另对同一直接来源地址合计每分钟 120 次限速，防止不断更换用户名绕过。限速时在登录页提示稍后重试，返回 429 与 Retry-After；不收紧参与者上传频率。
- Django 更新至同一受支持系列的 5.2.18 安全补丁，现有数据模型不变，无新增数据库迁移。

响应头层不查询数据库、不读文件、不扫描实验内容。新限速仅在登录时额外使用一项现有持久计数，不在实验记录或批次上传路径增加该检查。未做容量／吞吐量基准，不能承诺所有环境下性能完全不变。

## 部署前仍需完成

公网 HTTPS、反向代理的可信来源和真实客户端地址、网络限流、备份恢复、主机更新与权限、日志脱敏及监控须在实际环境验证。首次向导不会更改 DNS、防火墙或证书信任，也不自动信任 X-Forwarded-For 等请求头。

Docker 使用 Gunicorn 25.3 系列。`compose.public.yaml` 不向主机发布应用端口；Caddy 处理 HTTPS 并覆盖 `X-Forwarded-Proto`，应用仅在显式开启代理信任且使用安全 Cookie 时识别该头。本地 Compose 不开启这一信任。自有代理也必须覆盖客户端传入的头，并确保应用只能由受控代理访问，不能靠关闭 CSRF 解决配置错误。参见 [Django 代理设置](https://docs.djangoproject.com/en/5.2/ref/settings/#secure-proxy-ssl-header)。

已发布的原版 0.1.0 仍共用 experiment origin，不承诺互不信任的实验彼此隔离；上面的分支隔离不能追溯改变它。代理后的登录总限速按直接来源计数，不把未经验证的转发 IP 当作真实客户端；大型部署需在可信入口合理设置额外网络限速。

0.1.0 以 Docker 交付，见[中文部署教程](deployment.md)／[English deployment guide](deployment_en.md)。此前源码快照保留在 `v0.1.0-source-snapshot-20261020`；正式 Docker 发行的 `v0.1.0` 按用户确认更新，不将旧快照的检查结果冒充新版实测。

依据：[Django 点击劫持防护](https://docs.djangoproject.com/en/5.2/ref/clickjacking/)、[OWASP CSP 指南](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)、[Django 部署检查](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)、[Django 5.2.18 安全修复](https://docs.djangoproject.com/en/5.2/releases/5.2.18/)、[Gunicorn 安全维护范围](https://github.com/benoitc/gunicorn/security)。
