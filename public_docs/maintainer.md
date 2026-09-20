# 维护者说明

仅在明确合成环境运行。[启动指南](quickstart.md)说明实例初始化与构建；不得重新初始化已有实例。管理站与实验资源维持信任边界，GEP_PUBLIC_API应包含实验服务实际协议、hostname和端口。旧session/下载/队列不因修改此配置自动迁移。

03A的GUI与附件修复无数据库迁移；03B为纯新增账号治理迁移。metadata下载保留no-store与下载时授权。权限UI不替代后端检查，Owner不可通过研究成员撤销操作降权。Owner/Admin/普通用户角色、账号重置与安全迁移演练工具已按03B实现，并通过服务端与真实Chrome工程检查（T26工程证据；人工T17仍待独立非开发者执行）。数据库变更为纯新增：旧账号默认普通用户，Owner仍由实例指针唯一决定，旧研究授权不会被自动改写或扩大。接管限制按（研究，权限）逐项比较：操作者必须在同一研究可行使且可委派目标账号的每项权限（含目标的不可委派权限），否则拒绝改密、停用与启用；Admin可管理其他Admin的账号生命周期，但任命/降级Admin与Owner行仅Owner可操作。

目标组件检查：`.venv/bin/pytest -q tests/test_t17_foundation.py tests/test_gui_packages.py tests/test_http.py`。账号治理与迁移演练检查：`.venv/bin/pytest -q tests/test_phase03_accounts.py tests/test_phase03_migration.py`。隔离真实浏览器检查：`GEP_T17_BROWSER=1 .venv/bin/pytest -q tests/test_t17_browser.py` 或 `GEP_T17_BROWSER=1 .venv/bin/pytest -q tests/test_phase03_accounts_browser.py`，需本机Chrome和Node依赖，仅启动pytest临时库和回环服务，不连接现有验收实例。浏览器工程检查不能代替独立T17。

迁移演练只允许在指定演练目录的新副本上进行：`.venv/bin/python tools/phase03_migration.py --verify --source <合成副本目录> --evidence-root <新演练目录>`。工具用SQLite备份接口只读复制源卷，私有复制instance/secret，只迁移副本，拒绝覆盖已存在目标，并在报告中给出迁移前后计数、引用摘要、矛盾授权与源文件哈希不变结论；禁止对真实验收卷直接执行migrate。

真实研究、VPS/DNS/TLS、独立备份恢复与科学验收仍需另行授权。发布不得包含内部开发文档、数据库、凭据、下载或恢复文件。
