# Changelog / 版本记录

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
