# GEP 0.1.1 · Docker security update / 安全修复版

## 中文

下载附件 **`gep_0_1_1_docker_linux_amd64.zip`**。包含 GEP 与修补后 Caddy 的 Linux amd64 镜像、Linux／Windows 启动脚本、中英图文指南、升级说明及安全审计。部署电脑只需 Docker；Windows 使用 Docker Desktop 的 Linux 容器引擎，ARM 不适用。

本版将 Web 发行与预览分到独立来源，修复 HTTPS 子域 Cookie 覆盖，收紧匿名上传、归档解析、准入预算及 JSON 校验，并暂时禁用 Schema 正则。应用基线为 Python 3.12.15；代理为 Caddy 2.11.7、Go 1.26.10、x/net 0.60.0，运行镜像不保留 pip。

**已有实例请先看[升级说明](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.1/docs/upgrade_0_1_1.md)，不要重新创建账号或数据卷。** 先停写并备份完整卷和原配置，再显式执行 `0016_browser_origin`。保留原账号、密码、权限、实例和数据。公网还需 `*.experiment.<原域名>` 的解析与配套代理；本地无需域名。使用 `pattern`／`patternProperties` 的旧发行会停止新准入及相关上传，旧待传数据保留，不自动改绑或续试。HTTPS 升级后重新登录。

GEC SDK 仍为 0.1.0、协议仍为 `gep/1`，不改实验科学逻辑或导出编码。[GEC 接入文档](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.1/docs/gec_godot.md) · [GEC_SDK 文件夹](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/tree/v0.1.1/GEC_SDK) · [全新部署](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.1/docs/deployment.md) · [GEP 使用教程](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.1/docs/researcher.md)。

0.1.0 标签、发布页和安装包保持原样，不含本次补丁。验证范围与未关闭项见[安全审计](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.1/docs/SECURITY_AUDIT.md)：旧浏览器缓存、可能泄漏的凭据、密码政策、容量、实际公网配置和既有 SDK／全量测试失败仍需处理。没有“全部测试通过”或系统绝对安全的结论，也不批准真实研究。

## English

Download **`gep_0_1_1_docker_linux_amd64.zip`**. It contains Linux amd64 images for GEP and the patched Caddy proxy, Linux/Windows launchers, localized illustrated guides, upgrade instructions and the audit. Only Docker is required on the deployment host; Windows uses Docker Desktop's Linux container engine. This is not an ARM build.

This update isolates Web releases and previews by origin, fixes HTTPS sibling-domain cookie substitution, bounds anonymous uploads, archive parsing and admission requests, handles invalid JSON, and temporarily rejects schema regex rules. The runtime uses Python 3.12.15; the proxy is Caddy 2.11.7 built with Go 1.26.10 and x/net 0.60.0. The application runtime no longer includes pip.

**Existing installations: read the [upgrade guide](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.1/docs/upgrade_0_1_1_en.md) first. Do not create a new account or volume.** Stop writes, back up the complete volume and original configuration, then explicitly apply `0016_browser_origin`. Retain accounts, passwords, permissions, instance identity and data. Public deployments also need `*.experiment.<existing-domain>` and matching proxy configuration; local use needs no domain. Old releases using `pattern` / `patternProperties` cannot accept new admissions or applicable uploads. Pending data is retained without rebinding or automatic task resumption. HTTPS users sign in again.

GEC SDK remains 0.1.0 with protocol `gep/1`; scientific logic and export encoding are unchanged. [GEC developer guide](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.1/docs/gec_godot_en.md) · [GEC_SDK folder](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/tree/v0.1.1/GEC_SDK) · [New deployment](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.1/docs/deployment_en.md) · [GEP user guide](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.1/docs/researcher_en.md).

The 0.1.0 tag, release and attachment remain unchanged and do not include these patches. See the [audit and open findings](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/v0.1.1/docs/SECURITY_AUDIT.md). Legacy browser caches, potentially stolen credentials, password policy, capacity, actual public deployment conditions and existing SDK/full-suite test failures remain open. This release does not claim that all tests pass, absolute security or approval for real studies.
