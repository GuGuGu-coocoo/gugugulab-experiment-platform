# 维护者说明

仅在明确合成环境运行。[启动指南](quickstart.md)说明实例初始化与构建；不得重新初始化已有实例。管理站与实验资源维持信任边界，GEP_PUBLIC_API应包含实验服务实际协议、hostname和端口。旧session/下载/队列不因修改此配置自动迁移。

本轮GUI与附件修复无数据库迁移。metadata下载保留no-store与下载时授权。权限UI不替代后端检查，Owner不可通过研究成员撤销操作降权。完整Owner/Admin矩阵、账号重置及迁移工具仍未交付。

定向组件检查：`.venv/bin/pytest -q tests/test_t17_foundation.py tests/test_gui_packages.py tests/test_http.py`。隔离真实浏览器检查：`GEP_T17_BROWSER=1 .venv/bin/pytest -q tests/test_t17_browser.py`，需本机Chrome和Node依赖，仅启动pytest临时库和回环服务，不连接现有验收实例。浏览器工程检查不能代替独立T17。

真实研究、VPS/DNS/TLS、独立备份恢复与科学验收仍需另行授权。发布不得包含内部开发文档、数据库、凭据、下载或恢复文件。
