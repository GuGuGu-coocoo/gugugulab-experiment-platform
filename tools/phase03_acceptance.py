#!/usr/bin/env python3
"""03F acceptance orchestrator (test tool, not an agent loop).

``--verify`` runs the affected 03F integration matrix on this machine and writes
one machine-readable evidence document. Every requirement (T25-T30 and the
affected T03/04/07/08/11/13/14/16/18-24 regressions) is split into named
subcases, and every subcase is bound to *named executable evidence*: one pytest
node id, one Playwright spec title, one check label of the shell/package
verifiers, or one Windows preparation/runtime item. A missing or skipped
subcase is ``NOT_RUN``, a failing subcase is ``FAIL``, and a requirement passes
only when every one of its subcases passes - there is no blanket inheritance
from an unrelated shell/package/browser run.

Steps reuse the existing phase03 verification scripts instead of re-implementing
them:

* ``integrity``          - current source/build/toolchain digests, Django
                           migration coverage on a temporary database, and a
                           before/after content digest of the protected local
                           data (nothing here may write to it);
* ``pytest``             - the affected server/GUI contract suite (including the
                           isolated real-Chrome checks), recorded per test node
                           through a JUnit XML report;
* ``shell``              - real exported Godot Web + macOS arm64 builds through
                           the reusable GEC shell (three modes, recovery,
                           offline/ACK-loss/reopen, storage, exports);
* ``package``            - frozen complete-package checks and platform entries;
* ``browser``            - the affected legacy browser specs, executed against a
                           NEW isolated instance (never the dev instance on port
                           8000 and never the protected acceptance database);
* ``windows_preparation``- the real Windows readiness kit (isolated instance,
                           frozen releases, authenticated downloads) plus the
                           read-only device probe. This local preparation is a
                           separate step and can never stand in for a device run;
* ``windows_runtime``    - the strict Windows WN01-WN06 gate for an
                           **explicitly selected** device run *and* preparation
                           pair (``--windows-run``/``--windows-prep`` or
                           ``GEP_WINDOWS_RUN_DIR``/``GEP_WINDOWS_PREP_DIR``).
                           The newest preparation directory is never discovered
                           or trusted, and the freshly prepared local kit is
                           never silently substituted for a selected pair.
                           Without a selected pair the runtime stays ``NOT_RUN``
                           with the exact external blocker and the overall
                           acceptance is ``BLOCKED`` (never ``PASS``).

Human designer QA and independent T17 stay ``NOT_RUN`` in every case: this tool
produces engineering evidence only. Historical platform coverage that is not
re-run here is listed separately as retained evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
PHASE_ROOT = ROOT / "local_data" / "phase03_20260920"
RUN_ROOT = PHASE_ROOT / "p0308"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
PROTECTED_DB = ROOT / "local_data" / "gep.sqlite3"
PROTECTED_VOLUME = ROOT / "local_data" / "independent_acceptance_20260912"
WEB_ZIP = ROOT / "build" / "synthetic_web.zip"
NATIVE_DESCRIPTOR = ROOT / "build" / "native" / "descriptor.json"
SETUP_DRIVER = ROOT / "tests" / "browser" / "phase03_acceptance_setup.mjs"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED = "BLOCKED"
STEP_ERROR = "ERROR"

# The affected 03F suite. Unchanged Phase 01/02 tests are deliberately not
# re-run here; their retained results live in the historical section.
PYTEST_FILES = (
    "tests/test_phase03_accounts.py",
    "tests/test_phase03_accounts_browser.py",
    "tests/test_phase03_excel.py",
    "tests/test_phase03_migration.py",
    "tests/test_phase03_packages.py",
    "tests/test_phase03_permissions.py",
    "tests/test_phase03_permissions_browser.py",
    "tests/test_phase03_portal.py",
    "tests/test_phase03_queries.py",
    "tests/test_phase03_recovery_codes.py",
    "tests/test_phase03_releases.py",
    "tests/test_phase03_releases_browser.py",
    "tests/test_phase03_shell.py",
    "tests/test_phase03_ui_browser.py",
    "tests/test_phase03_windows_build.py",
    "tests/test_phase03_windows_packages.py",
    "tests/test_phase03_windows_acceptance.py",
    "tests/test_phase03_acceptance.py",
    "tests/test_phase03_designer_kit.py",
)
BROWSER_SPECS = ("web_e2e.spec.js", "web_recovery.spec.js", "storage.spec.js",
                 "admission_modes.spec.js", "native_release.spec.js", "web_release.spec.js")
WINDOWS_ITEMS = ("WN01", "WN02", "WN03", "WN04", "WN05", "WN06")
WINDOWS_EXTERNAL_NOTE = ("需要真实 Windows x64 桌面上的 WN01–WN06 运行证据；"
                         "macOS、交叉编译或 Windows 浏览器证据不能替代")

# ------------------------------------------------------------------ subcases
# requirement -> (description, ((subcase name, (evidence selectors,)), ...))
# Selector syntax:  pytest:<node id> | browser:<spec>::<title> | shell:<label>
#                   package:<label> | windows_preparation:<label>
#                   windows_runtime:<WN item>
# A selector ending in ``*`` is a prefix match (used for per-member labels).
SUBREQUIREMENTS = {
    "T25": ("metadata 附件与撤权/快照；三模式/招募回显；CSV 前导零/引号/回滚；平台入口和非默认端口；真实浏览器下载/错误反馈", (
        ("metadata attachment download and revocation",
         ("package:gui: sidecar artifact_manifest.json downloadable by the build scope",
          "package:gui: revoked member is denied the artifact",
          "package:gui: revoked member is denied the sidecars",
          "pytest:tests/test_phase03_packages.py::test_artifact_download_immutable_and_reauthorized",
          "pytest:tests/test_phase03_packages.py::test_artifact_download_denied_outside_the_build_scope")),
        ("three frozen modes and recruitment echo",
         ("pytest:tests/test_phase03_shell.py::test_frozen_password_release_keeps_admission_after_study_policy_change",
          "pytest:tests/test_phase03_shell.py::test_frozen_anonymous_release_ignores_study_roster_policy",
          "pytest:tests/test_phase03_shell.py::test_frozen_id_release_requires_roster_id_after_study_policy_change",
          "browser:admission_modes.spec.js::GUI roster policies and real API enforce all three admission modes")),
        ("CSV roster leading zeros, quotes and rollback",
         ("pytest:tests/test_phase03_excel.py::test_roster_import_text_ids_numeric_and_password_staging",
          "pytest:tests/test_phase03_excel.py::test_users_import_rejects_duplicates_mixed_studies_and_rolls_back")),
        ("platform entry and non-default isolated port",
         ("pytest:tests/test_phase03_packages.py::test_native_current_release_never_offers_a_web_start",
          "browser:web_release.spec.js::upload, isolated preview and publish real Godot Web package")),
        ("real browser download and error feedback",
         ("browser:web_e2e.spec.js::real Godot Web input through IndexedDB API database and authorized export",
          "browser:storage.spec.js::ACK persistence failure retains raw and interrupted cleanup resumes")),
    )),
    "T26": ("Owner/Admin/普通用户越权矩阵、邀请/临时密码首次改密、停用会话失效、不可见子权限矛盾拒绝；Excel 预览错误/原子提交；并发 revision、重新认证与脱敏审计；旧库迁移对账", (
        ("owner/admin/user permission matrix and delegation bounds",
         ("pytest:tests/test_phase03_permissions.py::test_matrix_visibility_contract_reauth_and_atomic_commit",
          "pytest:tests/test_phase03_permissions.py::test_admin_matrix_cannot_exceed_effective_and_delegable_authority",
          "pytest:tests/test_phase03_permissions.py::test_legacy_permission_routes_enforce_revision_visibility_and_authority",
          "pytest:tests/test_phase03_permissions_browser.py::test_actual_chrome_permission_matrix_journeys")),
        ("invitations, temporary password first change, disabled sessions",
         ("pytest:tests/test_phase03_accounts.py::test_invitation_activation_replay_expiry_revocation_and_rate_limit",
          "pytest:tests/test_phase03_accounts.py::test_temporary_password_forced_change_gate_on_every_endpoint",
          "pytest:tests/test_phase03_accounts.py::test_password_change_and_admin_reset_invalidate_old_sessions",
          "pytest:tests/test_phase03_accounts_browser.py::test_actual_chrome_instance_account_governance")),
        ("invisible sub-permission conflicts are refused, never widened",
         ("pytest:tests/test_phase03_accounts.py::test_study_view_is_prerequisite_and_owner_reconciliation_is_previewed",
          "pytest:tests/test_phase03_permissions.py::test_new_visibility_actions_are_explicit_and_never_backfilled",
          "pytest:tests/test_phase03_permissions.py::test_matrix_visibility_contract_reauth_and_atomic_commit")),
        ("Excel preview errors and atomic commit",
         ("pytest:tests/test_phase03_excel.py::test_roster_preview_binding_expiry_and_scope",
          "pytest:tests/test_phase03_excel.py::test_roster_staged_hash_binding_and_bounded_purge",
          "pytest:tests/test_phase03_excel.py::test_roster_replay_rechecks_study_configure",
          "pytest:tests/test_phase03_excel.py::test_users_import_commit_rechecks_owner_pointer",
          "pytest:tests/test_phase03_excel.py::test_xlsx_security_limits_and_formula_rejection")),
        ("concurrent revision, reauthentication and redacted audit",
         ("pytest:tests/test_phase03_accounts.py::test_reauth_revision_csrf_and_atomic_audit",
          "pytest:tests/test_phase03_accounts.py::test_governance_secrets_are_one_time_and_never_audited",
          "pytest:tests/test_phase03_permissions.py::test_concurrent_confirmations_apply_exactly_once")),
        ("legacy database migration reconciliation",
         ("pytest:tests/test_phase03_migration.py::test_legacy_fixture_migration_is_additive_and_preserves_references",
          "pytest:tests/test_phase03_migration.py::test_migration_tool_rehearses_named_copy_and_refuses_existing_destination")),
    )),
    "T27": ("公开默认关闭、私有不泄漏、当前发行归属/批准校验、切换只影响新 session；旧链接/待上传/恢复保持引用；结束无开始按钮", (
        ("public off by default, private never leaked",
         ("pytest:tests/test_phase03_releases.py::test_publication_migration_defaults_and_preserves_legacy_bindings",
          "pytest:tests/test_phase03_releases.py::test_stable_entry_page_states_for_private_closed_and_unset_studies",
          "pytest:tests/test_phase03_portal.py::test_private_paused_closed_and_no_current_cases_behave_as_03c")),
        ("current release authority, approval and same-study ownership",
         ("pytest:tests/test_phase03_releases.py::test_selection_requires_authority_revision_and_same_study_approval",
          "pytest:tests/test_phase03_releases.py::test_selection_accepts_recruitment_or_configuration_authority",
          "pytest:tests/test_phase03_releases.py::test_publication_rejects_deactivated_actor_without_writes",
          "pytest:tests/test_phase03_releases_browser.py::test_actual_chrome_publication_and_stable_entry")),
        ("switching only affects new sessions; old links/pending/recovery keep references",
         ("pytest:tests/test_phase03_releases.py::test_switch_keeps_old_session_uploads_recovery_config_export_and_resources",
          "pytest:tests/test_phase03_releases.py::test_operation_retry_returns_original_session_before_new_current_policy",
          "pytest:tests/test_phase03_portal.py::test_old_bound_sessions_keep_their_release_after_a_current_switch")),
        ("finished studies never show a start button",
         ("pytest:tests/test_phase03_releases.py::test_stable_entry_page_binds_current_release_and_stale_admission_fails",
          "pytest:tests/test_phase03_releases.py::test_policy_and_recruitment_never_auto_publish")),
    )),
    "T28": ("六位码爆破、过期、尝试上限、重放、跨 session/设备、签发人撤权、并发消费；码单独无效、旧长许可兼容", (
        ("issuer authority, binding and rate limits",
         ("pytest:tests/test_phase03_recovery_codes.py::test_issue_requires_current_authority_and_binds_the_ticket",
          "pytest:tests/test_phase03_recovery_codes.py::test_issue_is_rate_limited_per_study_and_issuer",
          "pytest:tests/test_phase03_recovery_codes.py::test_redeem_rechecks_live_issuer_authority")),
        ("code alone is invalid: device proof and fixed binding required",
         ("pytest:tests/test_phase03_recovery_codes.py::test_redemption_requires_device_proof_and_fixed_binding",
          "pytest:tests/test_phase03_recovery_codes.py::test_named_continuation_requires_proof_and_configured_credentials")),
        ("brute force, attempt cap, expiry and replay",
         ("pytest:tests/test_phase03_recovery_codes.py::test_failed_attempts_commit_and_cap_at_five",
          "pytest:tests/test_phase03_recovery_codes.py::test_expiry_and_newer_issuance_invalidate_older_codes",
          "pytest:tests/test_phase03_recovery_codes.py::test_one_abusive_client_is_bounded_without_locking_another_client",
          "pytest:tests/test_phase03_recovery_codes.py::test_instance_wide_ceiling_bounds_distributed_guessing")),
        ("concurrent consumption is exactly once; codes never logged",
         ("pytest:tests/test_phase03_recovery_codes.py::test_concurrent_redemption_consumes_the_code_once",
          "pytest:tests/test_phase03_recovery_codes.py::test_no_code_or_proof_is_stored_in_throttle_or_audit")),
        ("revocation and legacy long-permit compatibility",
         ("pytest:tests/test_phase03_recovery_codes.py::test_code_cannot_bypass_revocation_and_legacy_permit_coexists",
          "pytest:tests/test_phase03_recovery_codes.py::test_expired_finished_queue_recovers_data_only_with_closed_completion",
          "shell:native long-permit recovery keeps the original session")),
    )),
    "T29": ("真实 Web/macOS 同一构建动态三模式，GEC 前后壳与无障碍状态；完整包完整性、断网重开与清理墓碑、finish 不更改科学顺序", (
        ("same build runs all three frozen modes on real Web and macOS",
         ("shell:web: anonymous session committed four records",
          "shell:web: id-mode session committed four records",
          "shell:web: code recovery keeps the original session with a second segment",
          "shell:native anonymous: server database matches local by event id/value",
          "shell:native password: authorized JSONL matches local by event id/value")),
        ("GEC shell front/back states and scientific order unchanged",
         ("pytest:tests/test_phase03_shell.py::test_headless_shell_contract_harness",
          "pytest:tests/test_phase03_shell.py::test_shell_module_and_web_companion_contract",
          "shell:native named continuation never replays trial 1",
          "shell:web: recovered session never replays trial 1")),
        ("complete package integrity and platform entries",
         ("package:清单成员集合与实际成员完全一致",
          "package:可执行文件、资源与签名逐字节未改且模式保持",
          "package:清单声明 macOS 应用根",
          "pytest:tests/test_phase03_packages.py::test_approval_publishes_unchanged_frozen_complete_package")),
        ("offline reopen, cleanup tombstones and finish without reordering",
         ("shell:web: offline local commit keeps the first trial boundary",
          "shell:web: anonymous session reached the cleaned tombstone",
          "shell:native data-only recovery ends in a cleaned tombstone",
          "package:确认上传后本地留下同一会话的清理墓碑")),
    )),
    "T30": ("Dashboard/模块导航/搜索筛选分页，三主题两语言、键盘与对比度；不同账号控件与服务端权限一致；portal 实测", (
        ("dashboard, module navigation, search/filter/pagination",
         ("pytest:tests/test_phase03_queries.py::test_dashboard_cards_stable_navigation_and_module_pages",
          "pytest:tests/test_phase03_queries.py::test_sessions_exact_code_search_status_filter_timezone_and_pagination",
          "pytest:tests/test_phase03_queries.py::test_roster_query_identity_gate_and_pagination",
          "pytest:tests/test_phase03_queries.py::test_session_page_loading_bounded_and_exact_on_large_synthetic_data")),
        ("three themes, two languages, keyboard and measured contrast",
         ("pytest:tests/test_phase03_ui_browser.py::test_chrome_modules_languages_themes_keyboard_and_narrow_layout",
          "pytest:tests/test_phase03_portal.py::test_language_and_theme_preferences_without_open_redirect")),
        ("controls match server permissions for different accounts",
         ("pytest:tests/test_phase03_ui_browser.py::test_chrome_permission_routes_forged_requests_and_cookie_isolation",
          "pytest:tests/test_phase03_queries.py::test_permission_separation_and_forged_requests",
          "pytest:tests/test_phase03_permissions.py::test_preview_is_bound_to_actor_state_and_target_grants")),
        ("portal and roster queries exercised in the real browser",
         ("pytest:tests/test_phase03_ui_browser.py::test_chrome_session_roster_queries_and_bilingual_portal",
          "pytest:tests/test_phase03_ui_browser.py::test_chrome_bilingual_matrix_controls_previews_and_reauth_errors")),
    )),
    "T03": ("被试只能提交自己的 session；A 研究者只能访问 A 的对象；改 URL/对象 ID 同样拒绝", (
        ("object authorization for upload/download scopes",
         ("pytest:tests/test_phase03_packages.py::test_native_upload_requires_build_upload_scope",
          "pytest:tests/test_phase03_packages.py::test_artifact_download_denied_outside_the_build_scope",
          "pytest:tests/test_phase03_releases.py::test_selection_requires_authority_revision_and_same_study_approval",
          "pytest:tests/test_phase03_queries.py::test_permission_separation_and_forged_requests")),
    )),
    "T04": ("实验不能取得管理 Cookie/上下文；管理接口拒绝跨界变更", (
        ("admin/experiment host and cookie isolation",
         ("pytest:tests/test_phase03_releases.py::test_hosts_and_admin_cookie_are_isolated",
          "pytest:tests/test_phase03_releases.py::test_www_host_only_links_to_the_portal",
          "pytest:tests/test_phase03_ui_browser.py::test_chrome_permission_routes_forged_requests_and_cookie_isolation")),
    )),
    "T07": ("release/schema/实例/用途固定；S1/S2 不串数据；新版发布不重标旧记录", (
        ("platform type, immutable complete package and old bindings",
         ("pytest:tests/test_phase03_packages.py::test_descriptor_only_and_web_releases_keep_the_legacy_contract",
          "pytest:tests/test_phase03_packages.py::test_unknown_artifact_versions_and_self_reference_fail_closed",
          "pytest:tests/test_phase03_windows_packages.py::test_windows_artifact_download_sidecars_and_permissions",
          "pytest:tests/test_phase03_windows_packages.py::test_windows_tamper_rollback_and_old_release_binding",
          "pytest:tests/test_phase03_releases.py::test_switch_keeps_old_session_uploads_recovery_config_export_and_resources")),
    )),
    "T08": ("固定快照导出时迟到记录不改旧产物；下载时撤权拒绝；产物无多余字段", (
        ("snapshot attachment, late records and revocation",
         ("pytest:tests/test_phase03_packages.py::test_artifact_download_immutable_and_reauthorized",
          "package:gui: revoked member is denied the artifact",
          "shell:web anonymous: authorized JSONL matches local by event id/value",
          "browser:web_e2e.spec.js::real Godot Web input through IndexedDB API database and authorized export")),
    )),
    "T11": ("暂停/关闭后旧上传按窗口处理；到期重新认证；重开不自动传撤回数据", (
        ("recruitment state transitions and old uploads",
         ("pytest:tests/test_phase03_releases.py::test_policy_and_recruitment_never_auto_publish",
          "pytest:tests/test_phase03_releases.py::test_operation_retry_returns_original_session_before_new_current_policy",
          "pytest:tests/test_phase03_releases.py::test_switch_keeps_old_session_uploads_recovery_config_export_and_resources",
          "shell:web: finished candidate asks for an explicit data-only confirmation")),
    )),
    "T13": ("邀请重复/过期/撤销；有限 Admin 不能委派或恢复接管高权账号", (
        ("instance role and takeover boundaries",
         ("pytest:tests/test_phase03_accounts.py::test_admin_cannot_manage_owner_or_change_roles",
          "pytest:tests/test_phase03_accounts.py::test_higher_grant_credential_takeover_refused",
          "pytest:tests/test_phase03_accounts.py::test_takeover_guard_is_study_scoped_and_includes_nondelegable",
          "pytest:tests/test_phase03_accounts.py::test_stale_actor_credentials_status_and_role_cannot_mutate",
          "pytest:tests/test_phase03_accounts.py::test_activation_rechecks_live_issuer_authority")),
    )),
    "T14": ("包路径穿越、链接、重名、资源替换、外链 schema、限额拒绝；预览不带管理身份", (
        ("hostile package and preview isolation",
         ("pytest:tests/test_phase03_packages.py::test_native_program_rejects_hostile_archive",
          "pytest:tests/test_phase03_packages.py::test_native_program_rejects_mismatched_digest_and_bounds",
          "pytest:tests/test_phase03_windows_packages.py::test_windows_program_rejects_hostile_archive",
          "pytest:tests/test_phase03_windows_packages.py::test_windows_program_rejects_members_the_freeze_would_generate",
          "browser:web_release.spec.js::upload, isolated preview and publish real Godot Web package")),
    )),
    "T16": ("日志、报告、包、镜像和导出不带秘密/真实数据", (
        ("no secrets in templates, audits, packages or exports",
         ("pytest:tests/test_phase03_excel.py::test_templates_are_bounded_and_contain_no_secrets",
          "pytest:tests/test_phase03_accounts.py::test_governance_secrets_are_one_time_and_never_audited",
          "pytest:tests/test_phase03_recovery_codes.py::test_no_code_or_proof_is_stored_in_throttle_or_audit",
          "package:公开配置不含密码、名单或凭据",
          "browser:storage.spec.js::authorized recovery without task policy exports data without resuming trials or secrets")),
    )),
    "T18": ("SDK 不改变宿主 callback、随机化、评分、按键和 RT", (
        ("assembly-only bootstrap and unchanged scientific task",
         ("pytest:tests/test_phase03_shell.py::test_bootstrap_is_assembly_only_and_scientific_task_unchanged",
          "pytest:tests/test_phase03_shell.py::test_shell_module_and_web_companion_contract",
          "browser:web_e2e.spec.js::real Godot Web input through IndexedDB API database and authorized export")),
    )),
    "T19": ("同一实验主体无 GEC 专用调用；两类数据保持原值/单位/顺序", (
        ("no GEC-specific scientific branch and golden value reconciliation",
         ("pytest:tests/test_phase03_shell.py::test_bootstrap_is_assembly_only_and_scientific_task_unchanged",
          "shell:native anonymous: server database matches local by event id/value",
          "shell:native id: authorized JSONL matches local by event id/value",
          "shell:web id: local records present")),
    )),
    "T20": ("一个实测 OS 的独立 Godot 原生包，无 Runner/JS；原生事务、终止、ACK 丢失/DB 重启后去重，导出逐条匹配", (
        ("one real OS native package without a runner (macOS, fresh run)",
         ("package:解包后程序存在且可执行",
          "package:外置冻结配置与 .app 同级（无需研究者替换）",
          "package:下载的 macOS 程序用自带配置完成同一合成任务",
          "package:未通过命令行替换连接配置",
          "shell:native dropped ACK left no unacknowledged batch",
          "shell:native ACK-loss retransmission stored no duplicates")),
        ("real Windows x64 native runtime (external device required)",
         ("windows_runtime:WN01", "windows_runtime:WN02", "windows_runtime:WN03",
          "windows_runtime:WN04", "windows_runtime:WN05", "windows_runtime:WN06")),
    )),
    "T21": ("后台三种模式：无需 ID 有 UUID；预发名单 ID 保留 001、未知 ID 拒绝；跨研究同 ID 隔离", (
        ("three admission modes, roster ids and cross-study isolation",
         ("browser:admission_modes.spec.js::GUI roster policies and real API enforce all three admission modes",
          "shell:web: anonymous mode hides the ID field",
          "shell:native unknown roster ID is refused",
          "shell:native unrelated tombstone does not block a new study session",
          "pytest:tests/test_phase03_shell.py::test_frozen_id_release_requires_roster_id_after_study_policy_change")),
    )),
    "T22": ("配置导出无秘密、原生外置替换生效、默认/错 study/版本拒绝、Web 自动配置；大包中断不发布半成品", (
        ("config export without secrets and packaged default configuration",
         ("pytest:tests/test_phase03_packages.py::test_descriptor_only_and_web_releases_keep_the_legacy_contract",
          "package:公开配置不含密码、名单或凭据",
          "package:外置冻结配置与 .app 同级（无需研究者替换）",
          "package:未通过命令行替换连接配置")),
        ("wrong version/study refused and interrupted packaging never replaces the previous release",
         ("pytest:tests/test_phase03_packages.py::test_interrupted_packaging_never_replaces_the_previous_release",
          "pytest:tests/test_phase03_packages.py::test_content_addressed_storage_never_overwrites",
          "pytest:tests/test_phase03_windows_packages.py::test_windows_program_rejects_undigested_bytes_and_bounds")),
    )),
    "T23": ("e1/c1 已提交、e2/c2 提交时终止；恢复只取事务完整边界；已完成 trial 不重放；错版本/双窗口拒绝", (
        ("checkpoint boundaries and no trial replay",
         ("shell:native checkpoint bound to the first trial",
          "shell:native long-permit recovery continues at the trial boundary",
          "shell:native named continuation never replays trial 1",
          "shell:native expired-token continuation never replays trial 1",
          "shell:web: continued session opened a second segment")),
    )),
    "T24": ("活动任务有恢复依赖时不得删；清理中终止可重试；共享设备新参与锁旧恢复且无泄露", (
        ("cleanup retention and shared-device lock",
         ("shell:native writer lock holder reached its boundary",
          "shell:native second writer is refused",
          "shell:native new participation front-locks the older session",
          "shell:native data-only recovery uploaded the retained records",
          "browser:storage.spec.js::single writer and shared-device new participation locks old front recovery")),
    )),
}

# Subcases whose evidence can only come from the external Windows device. When
# these are the only NOT_RUN subcases the overall verdict is BLOCKED, never PASS.
EXTERNAL_SUBCASES = {"T20": {"real Windows x64 native runtime (external device required)"}}
# Steps whose evidence can only come from the external Windows device.
EXTERNAL_STEPS = frozenset({"windows_runtime"})

HISTORICAL_COVERAGE = (
    {"scope": "Phase 01/02 平台矩阵（T01/02/05/06/09）", "status": "RETAINED",
     "evidence": "docs/verification.md", "note": "未受影响，不在本次重跑范围"},
    {"scope": "Windows 浏览器 LAN 验收", "status": "RETAINED",
     "evidence": "docs/internal/completed/reports/lan_windows_acceptance.md",
     "note": "Windows 浏览器证据不等于 Windows 原生 WN01–06"},
    {"scope": "macOS arm64 原生完整包（T20 平台覆盖）", "status": "FRESH",
     "evidence": "package 步骤", "note": "本次重新运行完整包验证"},
    {"scope": "历史 14 项浏览器通过记录", "status": "RETAINED",
     "evidence": "docs/internal/completed/reports/phase_01_03_acceptance_20260912.md",
     "note": "本次按新 GUI 契约重跑受影响的 6 个规格"},
)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def protected_digest():
    """Real content digest of the protected local data (never a size summary).

    The acceptance run must not change a single protected byte. WAL/SHM sidecars
    are part of the content, and the volume files are hashed by content, so a
    same-length mutation is detected too.
    """
    digest = hashlib.sha256()
    for candidate in (PROTECTED_DB, Path(str(PROTECTED_DB) + "-wal"), Path(str(PROTECTED_DB) + "-shm")):
        if candidate.is_file():
            digest.update(candidate.name.encode())
            digest.update(sha256_file(candidate).encode())
    if PROTECTED_VOLUME.is_dir():
        for path in sorted(PROTECTED_VOLUME.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(PROTECTED_VOLUME).as_posix().encode())
                digest.update(sha256_file(path).encode())
    return digest.hexdigest()


class Runner:
    """Runs real commands, keeps their logs and never swallows a failure."""

    def __init__(self, root: Path, quiet=False):
        self.root = root
        self.quiet = quiet
        self.log = []
        self.steps = {}

    def say(self, message):
        line = f"[phase03-acceptance] {message}"
        self.log.append(line)
        if not self.quiet:
            print(line, flush=True)

    def step_dir(self, name):
        target = self.root / name
        target.mkdir(parents=True, exist_ok=True)
        return target

    def run_step(self, name, command, timeout, env=None, cwd=ROOT):
        """Run one real command, keep its log and the observed exit code."""
        target = self.step_dir(name)
        log_path = target / "command.log"
        self.say(f"{name}: {' '.join(str(part) for part in command)}")
        started = time.time()
        try:
            with open(log_path, "w") as stream:
                result = subprocess.run([str(part) for part in command], cwd=str(cwd),
                                        env={**os.environ, **(env or {})}, stdout=stream,
                                        stderr=subprocess.STDOUT, timeout=timeout)
            code, timed_out, error = result.returncode, False, None
        except subprocess.TimeoutExpired:
            code, timed_out, error = None, True, "timeout"
        except OSError as problem:
            code, timed_out, error = None, False, repr(problem)
        elapsed = round(time.time() - started, 1)
        tail = ""
        if log_path.is_file():
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        record = {"name": name, "command": [str(part) for part in command], "exit": code,
                  "timed_out": timed_out, "error": error, "seconds": elapsed,
                  "log": str(log_path.relative_to(self.root)), "tail": tail}
        self.steps[name] = record
        self.say(f"{name}: exit={code} timed_out={timed_out} in {elapsed}s")
        return record

    def status_of(self, name):
        record = self.steps.get(name)
        if record is None:
            return STATUS_NOT_RUN
        if record["exit"] == 0 and not record["timed_out"]:
            return STATUS_PASS
        return STATUS_FAIL


# --------------------------------------------------------------------- steps
def integrity_step(runner: Runner, baseline):
    """Source/build/toolchain integrity plus migration coverage on a temp database."""
    checks = []

    def check(ok, label, detail=None):
        checks.append({"ok": bool(ok), "label": label, "detail": detail})
        runner.say(("ok   " if ok else "FAIL ") + label + (f" :: {detail}" if detail is not None else ""))
        return bool(ok)

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    check(head.returncode == 0, "git HEAD recorded", head.stdout.strip())
    dirty = [line for line in status.stdout.splitlines() if line.strip()]
    check(True, "worktree state recorded", {"entries": len(dirty)})
    for path, label in ((WEB_ZIP, "exported Web archive"), (NATIVE_DESCRIPTOR, "native descriptor"),
                        (ROOT / "build" / "native" / "GEP Synthetic Experiment.app", "macOS arm64 build"),
                        (ROOT / "build" / "windows" / "synthetic_windows.zip", "Windows x64 archive"),
                        (ROOT / "build" / "windows" / "descriptor.json", "Windows descriptor")):
        check(path.exists(), f"{label} present", str(path.relative_to(ROOT)))
    check(shutil.which("godot") is not None, "pinned Godot toolchain present", shutil.which("godot"))
    check(shutil.which("node") is not None, "node toolchain present", shutil.which("node"))
    check(VENV_PYTHON.exists(), "project virtualenv present", str(VENV_PYTHON))
    temp_data = runner.step_dir("integrity") / "migration-data"
    temp_data.mkdir(parents=True, exist_ok=True)
    env = {"GEP_DATA_DIR": str(temp_data), "PYTHONPATH": str(ROOT / "server"),
           "DJANGO_SETTINGS_MODULE": "gep.settings", "GEP_SECRET_KEY": "synthetic-integrity-only"}
    record = runner.run_step("integrity_migrations",
                             [str(VENV_PYTHON), "-m", "django", "makemigrations", "--check", "--dry-run"],
                             timeout=300, env=env)
    check(record["exit"] == 0, "committed migrations fully cover the models", {"exit": record["exit"]})
    record = runner.run_step("integrity_schema",
                             [str(VENV_PYTHON), "-c",
                              "import os,django;django.setup();"
                              "from django.core.management import call_command;"
                              "call_command('migrate',verbosity=0);"
                              "import sqlite3;"
                              "c=sqlite3.connect(os.path.join(os.environ['GEP_DATA_DIR'],'gep.sqlite3'));"
                              "names={row[0] for row in c.execute(\"select name from sqlite_master where type='table'\")};"
                              "required={'core_instance','core_study','core_release','core_session','core_event'};"
                              "missing=sorted(required-names);"
                              "print('SCHEMA_OK' if not missing else 'SCHEMA_MISSING '+str(missing))"],
                             timeout=300, env=env)
    check(record["exit"] == 0 and "SCHEMA_OK" in record["tail"], "temporary instance schema is complete",
          {"exit": record["exit"]})
    check(protected_digest() == baseline["protected"], "protected local data content digest unchanged before the run",
          {"digest": baseline["protected"][:16]})
    failures = [entry for entry in checks if not entry["ok"]]
    record = {"name": "integrity", "checks": checks, "failures": failures}
    (runner.step_dir("integrity") / "checks.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
    runner.steps["integrity"] = {"name": "integrity", "exit": 0 if not failures else 1, "timed_out": False,
                                 "seconds": None, "log": "integrity/checks.json", "tail": "", "checks": checks}
    return record


def pytest_step(runner: Runner):
    """The affected contract suite with per-node results from a JUnit XML report.

    ``GEP_TEST_DB_FILE`` selects the project's documented real-file test database
    (the same mechanism the concurrency probes use). The live-server browser tests
    share one SQLite connection across threads; with the default in-memory
    shared-cache database a row written by the test thread can stay invisible to
    the serving thread for a moment, which made a real-Chrome test flaky. A real
    file database gives the same commit visibility as production.
    """
    target = runner.step_dir("pytest")
    report = target / "pytest.xml"
    if report.exists():
        report.unlink()
    record = runner.run_step(
        "pytest", [str(VENV_PYTHON), "-m", "pytest", "-q", "--junitxml", str(report), *PYTEST_FILES],
        timeout=3600, env={"GEP_T17_BROWSER": "1", "GEP_TEST_DB_FILE": str(target / "test-db.sqlite3")})
    results = {}
    counts = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "error": 0}
    if report.is_file():
        try:
            tree = ElementTree.parse(report)
        except ElementTree.ParseError as error:
            return {"status": STATUS_FAIL, "error": f"junit xml unreadable: {error}",
                    "tests": {}, "counts": counts, "record": record}
        for case in tree.iter("testcase"):
            classname = case.get("classname") or ""
            module = classname.replace(".", "/") + ".py"
            node = f"{module}::{case.get('name')}"
            outcome = "passed"
            if case.find("failure") is not None:
                outcome = "failed"
            elif case.find("error") is not None:
                outcome = "error"
            elif case.find("skipped") is not None:
                outcome = "skipped"
            results[node] = outcome
            counts["total"] += 1
            counts[outcome] = counts.get(outcome, 0) + 1
    status = STATUS_PASS if record["exit"] == 0 and not record["timed_out"] else STATUS_FAIL
    return {"status": status, "tests": results, "counts": counts, "record": record,
            "report": str(report.relative_to(runner.root))}


def verifier_step(runner: Runner, name, script):
    """Run one phase03 verifier and read its per-check evidence document."""
    target = runner.step_dir(name) / "run"
    record = runner.run_step(name, [str(VENV_PYTHON), str(TOOLS / script), "--verify", "--root", str(target)],
                             timeout=5400 if name == "shell" else 3600)
    evidence = target / "evidence.json"
    checks = {}
    error = None
    if evidence.is_file():
        try:
            document = json.loads(evidence.read_text(encoding="utf-8"))
        except ValueError as problem:
            error = f"evidence.json unreadable: {problem}"
            document = {}
        for check in document.get("checks") or []:
            checks[str(check.get("label"))] = STATUS_PASS if check.get("ok") else STATUS_FAIL
        if document.get("error"):
            error = str(document["error"])
    else:
        error = "evidence.json missing"
    status = STATUS_PASS if record["exit"] == 0 and not record["timed_out"] else STATUS_FAIL
    return {"status": status, "checks": checks, "error": error, "record": record,
            "evidence": str(evidence.relative_to(runner.root)) if evidence.is_file() else None}


def browser_step(runner: Runner):
    """Run the affected legacy specs against a NEW isolated instance."""
    import phase03_verify_shell as shell_verify

    target = runner.step_dir("browser")
    verifier = shell_verify.Verify(target / "instance")
    results = {"specs": [], "tests": {}, "instance": {}}
    verifier.init_instance()
    verifier.start_server()
    try:
        job = {"admin_url": f"http://admin.localhost:{verifier.port}",
               "experiment_url": f"http://experiment.localhost:{verifier.port}",
               "credentials": {"username": "synthetic_owner", "password": verifier.owner_password},
               "web_zip": str(WEB_ZIP),
               "connection_out": str(target / "connection.json"),
               "study_url_file": str(target / "study_url.txt"),
               "run_url_file": str(target / "run_url.txt")}
        job_path = target / "setup_job.json"
        job_path.write_text(json.dumps(job))
        setup_log = target / "setup.log"
        with open(setup_log, "w") as stream:
            setup = subprocess.run(["node", str(SETUP_DRIVER), str(job_path)], cwd=ROOT,
                                   stdout=stream, stderr=subprocess.STDOUT, timeout=900)
        raw = setup_log.read_text(encoding="utf-8", errors="replace")
        payload = None
        for line in raw.splitlines():
            try:
                payload = json.loads(line)
                break
            except ValueError:
                continue
        results["setup"] = {"exit": setup.returncode, "payload": payload}
        runner.say(f"browser setup exit={setup.returncode} ok={bool(payload and payload.get('ok'))}")
        if setup.returncode != 0 or not payload or not payload.get("ok"):
            results["error"] = "isolated instance setup failed"
            results["status"] = STATUS_FAIL
            return results
        evidence = payload["evidence"]
        results["instance"] = {"port": verifier.port, "study_id": evidence.get("study_id"),
                               "release_id": evidence.get("release_id")}
        env = {
            "GEP_ISO_ADMIN_URL": job["admin_url"], "GEP_ISO_EXPERIMENT_URL": job["experiment_url"],
            "GEP_ISO_USERNAME": job["credentials"]["username"],
            "GEP_ISO_PASSWORD": job["credentials"]["password"],
            "GEP_ISO_CONNECTION": str(target / "connection.json"),
            "GEP_ISO_DB": str(verifier.db_path),
            "GEP_ISO_DESCRIPTOR": str(NATIVE_DESCRIPTOR),
            "GEP_ISO_WEB_ZIP": str(WEB_ZIP),
            "GEP_ISO_RUN_URL_FILE": str(target / "run_url.txt"),
            "GEP_ISO_STUDY_URL_FILE": str(target / "study_url.txt"),
            "GEP_WEB_E2E_URL": evidence["run_url"],
            "GEP_WEB_E2E_STUDY_URL": evidence["study_url"],
            "GEP_WEB_E2E_ADMIN_URL": job["admin_url"],
            "GEP_WEB_E2E_USERNAME": job["credentials"]["username"],
            "GEP_WEB_E2E_PASSWORD": job["credentials"]["password"],
        }
        for spec in BROWSER_SPECS:
            name = f"browser_{spec}"
            record = runner.run_step(name,
                                     ["node_modules/.bin/playwright", "test", f"tests/browser/{spec}",
                                      "--reporter=json"],
                                     timeout=1800, env=env)
            results["specs"].append({"spec": spec, "exit": record["exit"], "timed_out": record["timed_out"],
                                     "log": record["log"]})
            log_path = runner.root / record["log"]
            document = _playwright_json(log_path)
            for title, outcome in _playwright_specs(document):
                results["tests"][f"{spec}::{title}"] = outcome
    finally:
        verifier.stop_server()
    results["status"] = STATUS_PASS if (results["specs"] and all(
        spec["exit"] == 0 and not spec["timed_out"] for spec in results["specs"])) else STATUS_FAIL
    return results


def _playwright_json(log_path):
    if not log_path.is_file():
        return None
    text = log_path.read_text(encoding="utf-8", errors="replace")
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except ValueError:
        return None


def _playwright_specs(document):
    """Flatten a Playwright JSON report into (spec title, outcome) pairs."""
    found = []

    def walk(node):
        for spec in node.get("specs") or []:
            outcome = "passed" if spec.get("ok") else "failed"
            statuses = {result.get("status") for test in spec.get("tests") or []
                        for result in test.get("results") or []}
            if "skipped" in statuses and statuses == {"skipped"}:
                outcome = "skipped"
            found.append((str(spec.get("title")), outcome))
        for child in node.get("suites") or []:
            walk(child)

    for suite in (document or {}).get("suites") or []:
        walk(suite)
    return found


def windows_preparation_step(runner: Runner):
    """The local readiness kit plus the read-only device probe (never a run)."""
    target = runner.step_dir("windows_preparation")
    record = runner.run_step("windows_preparation",
                             [str(VENV_PYTHON), str(TOOLS / "phase03_verify_windows_native.py"),
                              "--verify-preparation", "--probe-device", "--quiet", "--root", str(target)],
                             timeout=3600)
    checks = {}
    results = {"record": record, "status": runner.status_of("windows_preparation")}
    readiness = target / "readiness.json"
    if readiness.is_file():
        try:
            document = json.loads(readiness.read_text(encoding="utf-8"))
        except ValueError as problem:
            results["error"] = f"readiness.json unreadable: {problem}"
            results["status"] = STATUS_FAIL
            return results
        for check in document.get("checks") or []:
            checks[str(check.get("label"))] = STATUS_PASS if check.get("ok") else STATUS_FAIL
        results.update({"preparation_status": document.get("preparation_status"),
                        "runtime_status": document.get("runtime_status"),
                        "device": document.get("runtime", {}).get("device"),
                        "readiness": str(readiness.relative_to(runner.root))})
    else:
        results["error"] = "readiness.json missing"
    results["checks"] = checks
    return results


def windows_runtime_step(runner: Runner, run_dir, prep_dir, validator=None):
    """The strict WN01-WN06 gate for one explicitly selected run+prep pair.

    The pair is never discovered: both paths must be given, must exist and must
    belong together. The freshly prepared local kit of this run is never used as
    a substitute for the selected preparation.
    """
    results = {"selected_run": run_dir, "selected_prep": prep_dir, "items": {},
               "status": STATUS_NOT_RUN, "verdict": None, "detail": None}
    if not run_dir and not prep_dir:
        results["detail"] = "no explicit --windows-run/--windows-prep pair selected; " + WINDOWS_EXTERNAL_NOTE
        results["items"] = {name: STATUS_NOT_RUN for name in WINDOWS_ITEMS}
        return results
    if not run_dir or not prep_dir:
        results["status"] = STATUS_FAIL
        results["detail"] = ("refused: an explicit selection must give both --windows-run and --windows-prep; "
                             "a partial pair is never completed by discovery")
        results["items"] = {name: STATUS_NOT_RUN for name in WINDOWS_ITEMS}
        return results
    run_path, prep_path = Path(run_dir).resolve(), Path(prep_dir).resolve()
    if not run_path.is_dir() or not prep_path.is_dir():
        results["status"] = STATUS_FAIL
        results["detail"] = f"refused: selected pair does not exist ({run_path} / {prep_path})"
        results["items"] = {name: STATUS_NOT_RUN for name in WINDOWS_ITEMS}
        return results
    if validator is None:
        import phase03_verify_windows_native as native
        validator = native.validate_run
    try:
        verdict = validator(run_path, prep_path)
    except Exception as problem:  # an invalid run must be a FAIL, not a crash
        results["status"] = STATUS_FAIL
        results["verdict"] = "EVIDENCE_INVALID"
        results["detail"] = f"selected run could not be validated: {problem!r}"
        results["items"] = {name: STATUS_NOT_RUN for name in WINDOWS_ITEMS}
        return results
    results["verdict"] = verdict.get("verdict")
    results["detail"] = verdict.get("detail")
    results["problems"] = verdict.get("problems") or []
    cases = verdict.get("cases") or {}
    for name in WINDOWS_ITEMS:
        results["items"][name] = cases.get(name, STATUS_NOT_RUN)
    if verdict.get("verdict") == "RUNTIME_PASS":
        results["status"] = STATUS_PASS
    else:
        results["status"] = STATUS_FAIL
    return results


# ------------------------------------------------------------------ evaluation
def _selector_status(source, key, evidence):
    if source == "pytest":
        tests = (evidence.get("pytest") or {}).get("tests") or {}
        matches = [node for node in tests if node == key or (not key.endswith("]") and node.startswith(key + "["))]
        if not matches:
            return STATUS_NOT_RUN, "no matching pytest node"
        outcomes = {tests[node] for node in matches}
        unknown = sorted(outcome for outcome in outcomes
                         if outcome not in ("passed", "failed", "error", "skipped"))
        if unknown:
            return STATUS_NOT_RUN, f"unknown pytest outcome(s): {unknown}"
        if "failed" in outcomes or "error" in outcomes:
            return STATUS_FAIL, sorted(outcomes)
        if "skipped" in outcomes:
            return STATUS_NOT_RUN, "skipped"
        return STATUS_PASS, f"{len(matches)} node(s)"
    if source == "browser":
        tests = (evidence.get("browser") or {}).get("tests") or {}
        outcome = tests.get(key)
        if outcome is None:
            return STATUS_NOT_RUN, "no matching spec title"
        if outcome == "failed":
            return STATUS_FAIL, outcome
        if outcome == "passed":
            return STATUS_PASS, outcome
        return STATUS_NOT_RUN, f"unknown browser outcome: {outcome!r}"
    if source == "shell":
        return _check_status((evidence.get("shell") or {}).get("checks") or {}, key)
    if source == "package":
        return _check_status((evidence.get("package") or {}).get("checks") or {}, key)
    if source == "windows_preparation":
        return _check_status((evidence.get("windows_preparation") or {}).get("checks") or {}, key)
    if source == "windows_runtime":
        status = ((evidence.get("windows_runtime") or {}).get("items") or {}).get(key)
        if status == STATUS_PASS:
            return STATUS_PASS, "selected pair gate"
        if status == STATUS_FAIL:
            return STATUS_FAIL, "selected pair gate"
        return STATUS_NOT_RUN, "selected pair gate"
    return STATUS_NOT_RUN, f"unknown evidence source {source!r}"


def _check_status(checks, key):
    if key.endswith("*"):
        matches = {label: status for label, status in checks.items() if label.startswith(key[:-1])}
    else:
        matches = {label: status for label, status in checks.items() if label == key}
    if not matches:
        return STATUS_NOT_RUN, "check label absent"
    failed = sorted(label for label, status in matches.items() if status == STATUS_FAIL)
    if failed:
        return STATUS_FAIL, failed
    unknown = sorted(f"{label}={status!r}" for label, status in matches.items() if status != STATUS_PASS)
    if unknown:
        return STATUS_NOT_RUN, f"unknown check status: {unknown}"
    return STATUS_PASS, f"{len(matches)} check(s)"


def requirement_matrix(evidence):
    matrix = {}
    for name, (description, subcases) in SUBREQUIREMENTS.items():
        entries = []
        statuses = []
        for subcase_name, selectors in subcases:
            subcase_status = STATUS_PASS
            details = []
            for selector in selectors:
                source, _, key = selector.partition(":")
                status, detail = _selector_status(source, key, evidence)
                details.append({"selector": selector, "status": status, "detail": detail})
                if status == STATUS_FAIL:
                    subcase_status = STATUS_FAIL
                elif status == STATUS_NOT_RUN and subcase_status != STATUS_FAIL:
                    subcase_status = STATUS_NOT_RUN
            statuses.append(subcase_status)
            entries.append({"name": subcase_name, "status": subcase_status, "evidence": details})
        if STATUS_FAIL in statuses:
            status = STATUS_FAIL
        elif STATUS_NOT_RUN in statuses:
            status = STATUS_NOT_RUN
        else:
            status = STATUS_PASS
        matrix[name] = {"status": status, "description": description, "subcases": entries}
    return matrix


def overall_status(matrix, steps, step_errors=None):
    """FAIL beats BLOCKED beats NOT_RUN; PASS needs every subcase and every step.

    Only an explicitly ``PASS`` step counts. A step that is ``NOT_RUN``,
    ``BLOCKED`` or has an unknown status keeps the run out of ``PASS``, and any
    step that raised is ``FAIL``. ``BLOCKED`` is reserved for the single case
    where the only missing evidence is the external Windows device gate.
    """
    errors = sorted(step_errors or {})
    step_failures = sorted(name for name, status in steps.items()
                           if status in (STATUS_FAIL, STEP_ERROR))
    if step_failures or errors:
        parts = []
        if step_failures:
            parts.append(f"steps failed: {', '.join(step_failures)}")
        if errors:
            parts.append(f"steps raised: {', '.join(errors)}")
        return STATUS_FAIL, "; ".join(parts)
    unknown_steps = sorted(name for name, status in steps.items()
                           if status not in (STATUS_PASS, STATUS_NOT_RUN, STATUS_BLOCKED))
    if unknown_steps:
        return STATUS_FAIL, f"steps with an unknown status: {', '.join(unknown_steps)}"
    subcase_statuses = {name: entry["status"] for name, entry in matrix.items()}
    failed = sorted(name for name, status in subcase_statuses.items() if status == STATUS_FAIL)
    if failed:
        return STATUS_FAIL, f"requirements with a failed subcase: {', '.join(failed)}"
    not_run = sorted(name for name, status in subcase_statuses.items() if status == STATUS_NOT_RUN)
    external_only = True
    external_missing = []
    for name in not_run:
        allowed = EXTERNAL_SUBCASES.get(name, set())
        for subcase in matrix[name]["subcases"]:
            if subcase["status"] == STATUS_PASS:
                continue
            if subcase["name"] in allowed:
                external_missing.append(f"{name}/{subcase['name']}")
            else:
                # A requirement with any non-external missing subcase is not
                # "only externally blocked", whatever else is missing too.
                external_only = False
    non_pass_steps = sorted(name for name, status in steps.items() if status != STATUS_PASS)
    if external_only and external_missing and set(non_pass_steps) <= EXTERNAL_STEPS:
        return STATUS_BLOCKED, ("external device gate only: real Windows x64 WN01-WN06 evidence is missing; "
                                + WINDOWS_EXTERNAL_NOTE)
    if not_run:
        return STATUS_NOT_RUN, f"required evidence not run: {', '.join(not_run)}"
    if non_pass_steps:
        return STATUS_NOT_RUN, f"required steps not explicitly passed: {', '.join(non_pass_steps)}"
    return STATUS_PASS, "every required subcase and step passed"


def windows_selection(args, environ=None):
    """Resolve the explicit run+prep pair. Discovery of the newest prep never happens."""
    environ = environ if environ is not None else os.environ
    run_dir = args.windows_run or environ.get("GEP_WINDOWS_RUN_DIR")
    prep_dir = args.windows_prep or environ.get("GEP_WINDOWS_PREP_DIR")
    return (run_dir or None), (prep_dir or None)


class EvidenceRootError(Exception):
    """The requested evidence root is refused before anything is written."""


def guard_evidence_root(root):
    """Refuse protected, non-dedicated, symlinked or already existing roots.

    Every run uses a *new* unique directory under the dedicated run root
    :data:`RUN_ROOT`. An existing tree is never refreshed, cleaned or deleted in
    place: a failed attempt keeps its exact files, paths and logs (including
    database/WAL/SHM sidecars, JSONL queues and command logs), and a retry uses
    a new root. The check runs in the CLI *and* inside :func:`orchestrate`, so a
    programmatic caller cannot bypass it either.
    """
    requested = Path(root)
    if requested.is_symlink():
        raise EvidenceRootError(f"refusing a symlinked evidence root: {requested}")
    resolved = requested.resolve()
    reserved = (ROOT, ROOT / "local_data", PHASE_ROOT, RUN_ROOT, PROTECTED_VOLUME,
                PROTECTED_DB, PROTECTED_DB.parent, ROOT / "build", ROOT / "tools", ROOT / "server")
    for candidate in reserved:
        if resolved == Path(candidate).resolve():
            raise EvidenceRootError(f"refusing reserved root: {resolved}")
    requested_abs = Path(os.path.abspath(requested))
    base = Path(RUN_ROOT).resolve()
    if base not in requested_abs.parents:
        raise EvidenceRootError(
            f"evidence root must live under the dedicated run root {base}: {requested_abs}")
    current = base
    for part in requested_abs.relative_to(base).parts:
        current = current / part
        if current.is_symlink():
            raise EvidenceRootError(f"refusing a symlinked path component: {current}")
    if resolved.exists():
        for marker in ("readiness.json", "kit", "operator", "evidence.json"):
            if (resolved / marker).exists():
                raise EvidenceRootError(
                    f"refusing a non-dedicated evidence directory (contains {marker}): {resolved}")
        raise EvidenceRootError(
            f"evidence root already exists; each run needs a new unique root (previous evidence is kept): {resolved}")
    return resolved


def orchestrate(root, quiet=False, windows_run=None, windows_prep=None, runner=None, validator=None):
    """Run every step, collect named evidence and write the report.

    The evidence root is verified by :func:`guard_evidence_root` before any
    write, cleanup or service start; an existing root is refused instead of
    refreshed. A step that raises is recorded as an error, the report is still
    written, and the overall verdict can never be PASS in that case.
    """
    root = guard_evidence_root(root)
    root.mkdir(parents=True, exist_ok=True)
    runner = runner or Runner(root, quiet=quiet)
    runner.say(f"evidence root {root}")
    baseline = {"protected": protected_digest(), "head": subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()}
    runner.say(f"baseline HEAD {baseline['head']} protected {baseline['protected'][:16]}")
    evidence = {}
    errors = {}

    def guard(name, function):
        try:
            return function()
        except BaseException as problem:  # keep the report even when a step throws
            runner.say(f"{name}: unexpected failure {problem!r}")
            errors[name] = repr(problem)
            return None

    integrity = guard("integrity", lambda: integrity_step(runner, baseline))
    evidence["pytest"] = guard("pytest", lambda: pytest_step(runner))
    evidence["shell"] = guard("shell", lambda: verifier_step(runner, "shell", "phase03_verify_shell.py"))
    evidence["package"] = guard("package", lambda: verifier_step(runner, "package", "phase03_verify_package.py"))
    evidence["browser"] = guard("browser", lambda: browser_step(runner))
    evidence["windows_preparation"] = guard("windows_preparation", lambda: windows_preparation_step(runner))
    evidence["windows_runtime"] = guard(
        "windows_runtime", lambda: windows_runtime_step(runner, windows_run, windows_prep, validator=validator))

    steps = {"integrity": STATUS_PASS if integrity and not integrity["failures"] else STATUS_FAIL,
             "pytest": (evidence["pytest"] or {}).get("status", STEP_ERROR),
             "shell": (evidence["shell"] or {}).get("status", STEP_ERROR),
             "package": (evidence["package"] or {}).get("status", STEP_ERROR),
             "browser": (evidence["browser"] or {}).get("status", STEP_ERROR),
             "windows_preparation": (evidence["windows_preparation"] or {}).get("status", STEP_ERROR),
             "windows_runtime": (evidence["windows_runtime"] or {}).get("status", STATUS_NOT_RUN)}
    # A step that raised is an internal error, never a NOT_RUN/BLOCKED default.
    for name in errors:
        if name in steps:
            steps[name] = STEP_ERROR
    matrix = requirement_matrix(evidence)
    protected_after = protected_digest()
    protected_ok = protected_after == baseline["protected"]
    if not protected_ok:
        steps["integrity"] = STATUS_FAIL
    overall, reason = overall_status(matrix, steps, errors)
    if not protected_ok and overall != STATUS_FAIL:
        overall, reason = STATUS_FAIL, "protected local data digest changed during the run"

    report = {
        "format": "gep-phase03-acceptance/v2",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "head": baseline["head"],
        "root": str(root),
        "steps": steps,
        "step_records": {name: {key: value for key, value in record.items() if key != "checks"}
                         for name, record in runner.steps.items()},
        "step_errors": errors,
        "requirements": matrix,
        "evidence": {
            "pytest": {key: value for key, value in (evidence.get("pytest") or {}).items()
                       if key in ("status", "counts", "report")},
            "shell": {key: value for key, value in (evidence.get("shell") or {}).items()
                      if key in ("status", "error", "evidence")},
            "package": {key: value for key, value in (evidence.get("package") or {}).items()
                        if key in ("status", "error", "evidence")},
            "browser": {key: value for key, value in (evidence.get("browser") or {}).items()
                        if key in ("status", "specs", "error", "instance")},
            "windows_preparation": {key: value for key, value in (evidence.get("windows_preparation") or {}).items()
                                    if key in ("status", "preparation_status", "runtime_status", "device",
                                               "readiness", "error")},
            "windows_runtime": {key: value for key, value in (evidence.get("windows_runtime") or {}).items()
                                if key not in ("items",)},
        },
        "windows": {"runtime_items": (evidence.get("windows_runtime") or {}).get("items", {}),
                    "external_note": WINDOWS_EXTERNAL_NOTE},
        "integrity": {"checks": (integrity or {}).get("checks", []), "protected_unchanged": protected_ok,
                      "protected_before": baseline["protected"], "protected_after": protected_after},
        "historical_coverage": list(HISTORICAL_COVERAGE),
        "human_status": {"designer_autonomous_qa": STATUS_NOT_RUN, "independent_t17": STATUS_NOT_RUN},
        "overall": overall,
        "overall_reason": reason,
        "notes": [
            "本工具只产生工程证据；不等于原设计者自主体验或独立 T17。",
            "每个子项绑定命名的可执行证据；缺失或跳过记为 NOT_RUN，失败记为 FAIL，不会整体继承无关运行的 PASS。",
            "整体 PASS 要求每个必需 step 与每个子项都明确 PASS；步骤内部异常一律 FAIL 并保留报告。",
            "每次运行都使用新的唯一证据根；已存在根、受保护根、专用运行根之外的路径与符号链接组件一律在写入前拒绝，失败现场原样保留。",
            "Windows x64 WN01-WN06 需要真实设备运行证据，且必须显式选择 --windows-run/--windows-prep；缺失时整体为 BLOCKED，永不 PASS。",
            "受保护的旧验收库与 8030 实例在本运行前后内容摘要必须一致。",
        ],
    }
    (root / "acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    lines = ["# 03F 验收编排证据", "", f"- HEAD: `{baseline['head']}`", f"- 整体: **{overall}**",
             f"- 原因: {reason}", "", "| 步骤 | 状态 |", "|---|---|"]
    lines += [f"| {name} | {status} |" for name, status in steps.items()]
    lines += ["", "| 需求 | 状态 | 未通过子项 |", "|---|---|---|"]
    for name, entry in matrix.items():
        missing = ", ".join(subcase["name"] for subcase in entry["subcases"]
                            if subcase["status"] != STATUS_PASS) or "-"
        lines += [f"| {name} | {entry['status']} | {missing} |"]
    lines += ["", f"- Windows 运行时: {(evidence.get('windows_runtime') or {}).get('verdict')} - "
                  f"{(evidence.get('windows_runtime') or {}).get('detail')}", ""]
    (root / "ACCEPTANCE.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"PHASE03_ACCEPTANCE {overall} root={root} steps="
          + ",".join(f"{name}={status}" for name, status in steps.items()))
    return 0 if overall == STATUS_PASS else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="03F acceptance orchestrator (engineering evidence only)")
    parser.add_argument("--verify", action="store_true", help="run the affected 03F integration matrix")
    parser.add_argument("--root", default=None,
                        help="evidence root (defaults to a new stamp under p0308); must not exist yet")
    parser.add_argument("--quiet", action="store_true", help="only print the final line")
    parser.add_argument("--windows-run", default=None,
                        help="explicit real Windows run directory to gate (never auto-discovered)")
    parser.add_argument("--windows-prep", default=None,
                        help="explicit preparation directory bound to --windows-run (never substituted)")
    args = parser.parse_args(argv)
    if not args.verify:
        parser.error("nothing to do: pass --verify")

    requested = Path(args.root) if args.root else RUN_ROOT / f"acceptance_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    try:
        root = guard_evidence_root(requested)
    except EvidenceRootError as error:
        print(f"PHASE03_ACCEPTANCE REFUSED {error}", file=sys.stderr)
        return 2
    windows_run, windows_prep = windows_selection(args)
    return orchestrate(root, quiet=args.quiet, windows_run=windows_run, windows_prep=windows_prep)


if __name__ == "__main__":
    sys.exit(main())
