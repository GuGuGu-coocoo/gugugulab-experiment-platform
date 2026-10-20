# 安全边界

GEP 0.1.0 提供本地与显式启用的 HTTPS 部署配置。应用层防护不等于你的服务器已完成部署验收、渗透测试或被试数据治理；上线前先用合成数据核对链路。

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

当前不同实验仍共用 experiment origin，不承诺互不信任的实验彼此隔离。代理后的登录总限速按直接来源计数，不把未经验证的转发 IP 当作真实客户端；大型部署需在可信入口合理设置额外网络限速。

0.1.0 以 Docker 交付，见[中文部署教程](deployment.md)／[English deployment guide](deployment_en.md)。此前源码快照保留在 `v0.1.0-source-snapshot-20261020`；正式 Docker 发行的 `v0.1.0` 按用户确认更新，不将旧快照的检查结果冒充新版实测。

依据：[Django 点击劫持防护](https://docs.djangoproject.com/en/5.2/ref/clickjacking/)、[OWASP CSP 指南](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)、[Django 部署检查](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)、[Django 5.2.18 安全修复](https://docs.djangoproject.com/en/5.2/releases/5.2.18/)、[Gunicorn 安全维护范围](https://github.com/benoitc/gunicorn/security)。
