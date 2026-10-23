# Changelog / 版本记录

## 0.1.1 — 2026-10-23 · Security fixes / 安全修复

- Give each Web release and preview a separate origin; bind CORS and served assets to their release/build. Retain old-origin queues through a trusted upload page without relabelling data or automatically resuming tasks.
- Use `__Host-` session/CSRF cookies in HTTPS mode to prevent sibling-domain cookie substitution. HTTPS users sign in again; accounts and passwords are retained.
- Reject anonymous uploads before multipart parsing, enforce admission budgets and pre-parse ZIP/XLSX limits, and reject invalid JSON root/numeric inputs.
- Temporarily reject JSON Schema `pattern` and `patternProperties`. This also affects new admissions and applicable event uploads for old releases; frozen definitions, exports and pending data are not deleted.
- Ship Python 3.12.15 and a rebuilt Caddy 2.11.7 using Go 1.26.10 / x/net 0.60.0. Remove pip from the application runtime. Include both images, localized guides and version-specific upgrade instructions in the Docker bundle.
- Apply migration `0016_browser_origin` explicitly to existing instances. Keep the original `.env`, project/volume/instance identifiers, credentials, data and API address. Public deployments also need isolated-host DNS and matching proxy configuration.

GEC SDK 0.1.0 and protocol `gep/1` are unchanged. The 0.1.0 tag, release and Docker attachment remain unchanged. Open findings, legacy-browser conditions, prior SDK/test failures and deployment-specific checks remain documented; this release is not a claim of complete security.

中文：Web 发行与预览分来源，修复 HTTPS Cookie 覆盖问题，收紧上传／归档解析和准入预算，暂禁 Schema 正则，并更新 Docker 运行环境与代理依赖。升级不重新创建账号或数据卷；需先完整备份，再显式迁移。详见[中文升级说明](docs/upgrade_0_1_1.md)／[English upgrade guide](docs/upgrade_0_1_1_en.md)、[审计与开放风险](docs/SECURITY_AUDIT.md)。

## 0.1.0 — 2026-10-20

GEP server delivery uses a Linux amd64 Docker image, with Windows and Linux launchers. This release includes the study workbench, participant portal and GEC integration.

- First-run Owner setup with a user-chosen username/password and optional base domain. Local use needs no domain; entering one saves pending configuration without changing DNS.
- Local Compose and an explicitly enabled Caddy HTTPS configuration for an initialized instance. Application ports are not published in HTTPS mode.
- Study settings, three participation modes, spreadsheet rosters, experiment upload, isolated preview, publication and one current version per study.
- Owner/Admin/member accounts, study visibility, detailed permissions, password confirmation and bulk imports.
- Participation/completion records, fixed data snapshots, CSV overview/events ZIP, JSONL and metadata downloads.
- Chinese/English GEP and GEC interfaces; Chinese/English README, server deployment, user and Godot developer guides.
- Script nonces, response security headers, stricter CSRF cookies, login rate limits, Django 5.2.18 and Gunicorn 25.3.
- Recovery controls remain hidden; existing local records and pending uploads are retained. Existing experiment packages, data formats and database records are not rewritten.

The former source-only `v0.1.0` snapshot is retained as `v0.1.0-source-snapshot-20261020`; the release tag is updated with the owner's confirmation. GitHub's automatic source archives are source references, not separate server installers. The attached deployment artifact is Docker-only.

中文：本版改用 Docker 交付，加入首次 Owner 设置、Linux／Windows 启动脚本、可选 HTTPS 配置和中英教程。后台“发布测试版本”等日常文案改为“发布版本”，不更改旧协议字段或已保存数据。旧源码快照保留单独标签，工程与人工结果分别记录于[验证说明](docs/verification.md)。

Publishing the software does not establish study approval, arbitrary workload capacity or scientific timing accuracy; review the [security notes](docs/security.md) and deployment conditions.
