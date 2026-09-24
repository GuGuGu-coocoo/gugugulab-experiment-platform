#!/usr/bin/env python3
"""R11 remediation acceptance orchestration: the local machine gate and the
aggregate gate that also consumes the real Windows x64 run.

``--verify-local`` runs one complete local acceptance pass and binds every
required T clause to named, freshly executed evidence:

0. this round's artifacts (``artifact_binding.json``): the pinned local Godot
   toolchain exports and packages brand-new Web and macOS bytes under one
   unique ``build/phase03_remediation_20260923/<unique>/`` root, and the
   program/descriptor/current-source digests are recorded in the same evidence;
   the shell, package and browser steps consume exactly those bound bytes and a
   missing, stale or mismatched binding is refused before anything runs;
1. the R11 boundary rehearsal (``tools/remediation_migration.py
   --verify-boundary``): an explicit 0011 source, a SQLite-backup copy upgraded
   to the latest schema, old invitation semantics preserved and the real v2
   binding/deletion contracts exercised through real entry points;
2. the real Web/macOS GEC shell (``tools/phase03_verify_shell.py --verify``):
   the exported Web and macOS programs in three frozen modes, recovery,
   offline/ACK-loss/reopen and the server database / authorized JSONL
   reconciliation, in a brand-new isolated instance;
3. the complete native package (``tools/phase03_verify_package.py --verify``):
   real upload/approve/freeze, authenticated downloads, package member
   integrity, a real packaged run and the tamper/revocation boundaries;
4. the affected legacy browser/native specs against a brand-new isolated
   instance (``storage``, ``native_cleanup``, ``native_transactions``,
   ``native_policy``, ``native_nonblocking``, ``native_shared_device``);
5. the complete remediation suite (``tests/remediation``) with
   ``GEP_T17_BROWSER=1``: real server-side integration plus the real Chrome and
   real Godot/macOS runs those tests own - zero failures and zero skips;
6. the affected legacy suites (the R11 list) with ``GEP_T17_BROWSER=1``: their
   named results are matched per selector; a failure is FAIL and a skip is
   NOT_RUN, never PASS;
7. the public-documentation contract: no ``docs/internal`` references or
   recorded private endpoints/user paths in public guides, and the
   human-testing wording (T17) present in the acceptance templates;
8. a strict fresh Windows preparation report: non-empty member manifests, an
   actual member set equal to the declared set, digests/sizes, the current
   program source digest and the kit/human binding all verified before anything
   is started; a stale preparation is NOT_RUN, never an inherited PASS;
9. the external Windows runtime items (WN01-WN06), which stay NOT_RUN in
   ``--verify-local`` because only the real Windows x64 host may execute them.

``--verify`` is the aggregate R11W gate and is deliberately not an alias of
``--verify-local``: it runs the same local gate *and* requires a real Windows
run (``--windows-run`` for already fetched evidence or ``--windows-kit`` for
the Mac SSH orchestration) bound to the current program source digest. Without
that real device evidence the aggregate verdict is FAIL, never PASS, and a
local pass alone can never stand in for the device run.

Machine coverage is the ``coverage_matrix.json``: every T25-T30 clause and the
affected T03/04/06/07/08/11/13/15/16/18-T24 clauses carry named selectors, each
clause resolved on its own so unrelated green tests can never cover a missing
recovery-code, cleanup or checkpoint clause. A missing, failed or skipped local
selector makes its clause - and therefore its T - not pass and the exit code
non-zero. Preparation, the real Windows run and the human round stay separate:
this tool never produces a human PASS.

Every run creates a brand-new unique evidence root and never overwrites or
cleans an existing one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

EVIDENCE_BASE = ROOT / 'local_data' / 'phase03_remediation_20260923' / 'p03r11a'
# This round's brand-new build root: the Web and macOS artifacts every bound
# verifier consumes are exported/packaged from the current sources under one
# unique subdirectory here, so the historical build/native and
# build/synthetic_web.zip bytes are never silently selected or rewritten.
BUILD_BASE = ROOT / 'build' / 'phase03_remediation_20260923'
ARTIFACT_BINDING_FORMAT = 'gep-remediation-artifact-binding/v1'
WEB_MEMBERS = ('index.html', 'index.js', 'index.wasm', 'index.pck', 'index.png',
               'index.audio.worklet.js', 'index.audio.position.worklet.js',
               'gec/sdk.js', 'gec/bridge.js', 'gec/inputs.js', 'gec/shell.js')
BINDING_ARTIFACTS = ('web_zip', 'native_zip', 'native_descriptor', 'native_binary')
REMEDIATION_SUITE = 'tests/remediation'
LEGACY_SUITES = (
    'tests/test_phase03_acceptance.py',
    'tests/test_phase03_designer_kit.py',
    'tests/test_phase03_windows_acceptance.py',
    'tests/test_phase03_accounts.py',
    'tests/test_phase03_accounts_browser.py',
    'tests/test_phase03_excel.py',
    'tests/test_phase03_permissions.py',
    'tests/test_phase03_permissions_browser.py',
    'tests/test_phase03_queries.py',
    'tests/test_phase03_ui_browser.py',
    'tests/test_phase03_recovery_codes.py',
    'tests/test_phase03_releases.py',
    'tests/test_phase03_releases_browser.py',
    'tests/test_phase03_portal.py',
    'tests/test_phase03_packages.py',
    'tests/test_phase03_windows_packages.py',
    'tests/test_gui_packages.py',
    'tests/test_t17_browser.py',
    'tests/test_phase03_migration.py',
    'tests/test_phase03_shell.py',
)
# The affected legacy browser/native specs, each run against a brand-new
# isolated instance (never the dev instance and never the protected acceptance
# database). The native specs are pointed at the isolated connection/DB through
# environment variables; a spec that cannot use them fails instead of silently
# falling back to the dev instance.
BROWSER_SPECS = (
    'tests/browser/storage.spec.js',
    'tests/browser/native_cleanup.spec.js',
    'tests/browser/native_transactions.spec.js',
    'tests/browser/native_policy.spec.js',
    'tests/browser/native_nonblocking.spec.js',
    'tests/browser/native_shared_device.spec.js',
)
SHELL_VERIFIER = 'tools/phase03_verify_shell.py'
PACKAGE_VERIFIER = 'tools/phase03_verify_package.py'
STATUS_PASS = 'PASS'
STATUS_FAIL = 'FAIL'
STATUS_NOT_RUN = 'NOT_RUN'

# The affected public guides that must not leak internal paths or private
# connection values, and the acceptance templates that must carry the
# human-testing wording (T17) of this round.
PUBLIC_DOCS = ('README.md', 'docs/quickstart.md', 'docs/researcher.md', 'docs/experiment_developer.md',
               'docs/maintainer.md', 'docs/protocol.md', 'docs/verification.md',
               'docs/researcher_acceptance.md', 'docs/designer_acceptance.md',
               'docs/windows_native_acceptance.md')
HUMAN_TEST_DOCS = ('docs/researcher_acceptance.md', 'docs/designer_acceptance.md',
                   'docs/windows_native_acceptance.md')
_INTERNAL_MARKERS = ('docs/internal',)
# A generic, value-free private-endpoint rule: RFC1918, CGNAT, link-local
# addresses and user home directory paths. The scan holds no user address or
# directory, and the pattern itself is public content like the rest of this
# file. Frozen packages legitimately use 127.0.0.1 for their own isolated
# instance, so loopback is not treated as a private leak.
_PRIVATE_IPV4 = re.compile(
    r'(?<![\d.])'
    r'(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}'
    r'|192\.168\.\d{1,3}\.\d{1,3}'
    r'|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}'
    r'|169\.254\.\d{1,3}\.\d{1,3}'
    r'|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})'
    r'(?![\d.])')
_PRIVATE_HOME = re.compile(r'(?:/Users/|/home/)[A-Za-z0-9._-]+/|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+\\')


def private_endpoint_marker(text):
    """One generic marker when public text records a private endpoint/path."""
    if _PRIVATE_IPV4.search(text):
        return 'private network address'
    if _PRIVATE_HOME.search(text):
        return 'user home directory path'
    return None


# ------------------------------------------------------------------ matrix
# requirement -> (description, ((subclause label, (evidence selectors,)), ...))
# Selector syntax:
#   pytest:<node id>            - exact node or prefix (parametrised cases)
#   boundary:<check name>       - tools/remediation_migration.py --verify-boundary
#   shell:<check label>         - tools/phase03_verify_shell.py --verify
#   package:<check label>       - tools/phase03_verify_package.py --verify
#   browser:<spec>::<title>     - the isolated-instance legacy spec run
#   docs:<label>                - public documentation contract
#   windows_preparation:<label> - the strict fresh Windows kit preparation
#   windows_runtime:WN0x        - the real Windows x64 run (R11W only)
# Every subclause resolves on its own: deleting a recovery-code, cleanup or
# checkpoint selector makes exactly that clause (and its T) not pass.
SUBREQUIREMENTS = {
    'T25': ('metadata 附件与撤权/快照；三模式/招募回显；CSV 前导零/引号/回滚；平台入口和非默认端口；真实浏览器下载/错误反馈', (
        ('metadata/快照 附件、撤权与成员清单', (
            'pytest:tests/remediation/test_p03r07.py::test_v2_snapshot_is_byte_frozen_and_roster_views_are_exact',
            'pytest:tests/remediation/test_p03r08.py::test_zip_and_metadata_permission_matrix_and_listing_projection',
            'package:清单成员集合与实际成员完全一致')),
        ('三模式/招募回显', (
            'pytest:tests/test_phase03_shell.py::test_frozen_password_release_keeps_admission_after_study_policy_change',
            'pytest:tests/test_phase03_shell.py::test_frozen_anonymous_release_ignores_study_roster_policy',
            'pytest:tests/test_phase03_shell.py::test_frozen_id_release_requires_roster_id_after_study_policy_change',
            'shell:web: anonymous session committed four records',
            'shell:web: id-mode session committed four records')),
        ('CSV 前导零/引号/回滚', (
            'pytest:tests/remediation/test_p03r06.py::test_csv_and_xlsx_share_the_text_contract',
            'pytest:tests/test_phase03_excel.py::test_roster_import_text_ids_numeric_and_password_staging',
            'pytest:tests/test_phase03_excel.py::test_users_import_rejects_duplicates_mixed_studies_and_rolls_back')),
        ('平台入口与非默认隔离端口', (
            'pytest:tests/remediation/test_p03r01c.py::test_derived_origin_uses_server_port_not_the_host_port',
            'pytest:tests/remediation/test_p03r01c.py::test_explicit_origin_wins_over_host_and_server_ports',
            'pytest:tests/test_phase03_packages.py::test_native_current_release_never_offers_a_web_start')),
        ('真实浏览器下载/错误反馈', (
            'pytest:tests/remediation/test_p03r08.py::test_real_chrome_downloads_the_zip_and_matches_the_independent_golden',
            'pytest:tests/remediation/test_p03r08.py::test_real_chrome_raw_only_main_button_defaults_unmapped_and_reports_zip_failures',
            'pytest:tests/remediation/test_p03r08.py::test_disk_failure_unknown_state_and_deadline_never_serve_a_half_package')),
        ('人工测试口径（T17 记录方式）', (
            'docs:human_testing_wording',)),
    )),
    'T26': ('Owner/Admin/普通用户越权矩阵、邀请/临时密码首次改密、停用会话失效、不可见子权限矛盾拒绝；Excel 预览错误/原子提交；并发 revision、重新认证与脱敏审计；旧库迁移对账', (
        ('Owner/Admin/普通用户越权矩阵与委派边界', (
            'pytest:tests/remediation/test_p03r02c.py::test_matrix_preview_real_requests_all_roles_targets_studies',
            'pytest:tests/remediation/test_p03r02c.py::test_admin_cannot_write_self_or_owner_and_default_cannot_manage_admins',
            'pytest:tests/test_phase03_permissions.py::test_matrix_visibility_contract_reauth_and_atomic_commit',
            'pytest:tests/test_phase03_permissions.py::test_admin_matrix_cannot_exceed_effective_and_delegable_authority')),
        ('邀请/临时密码首次改密与停用会话失效', (
            'pytest:tests/test_phase03_accounts.py::test_temporary_password_forced_change_gate_on_every_endpoint',
            'pytest:tests/test_phase03_accounts.py::test_password_change_and_admin_reset_invalidate_old_sessions',
            'pytest:tests/test_phase03_accounts.py::test_invitation_activation_replay_expiry_revocation_and_rate_limit',
            'pytest:tests/remediation/test_p03r02br.py::test_regrant_survives_the_explicit_adoption')),
        ('不可见子权限矛盾拒绝，绝不扩权', (
            'pytest:tests/remediation/test_p03r05.py::test_v2_restore_visibility_sends_view_only_and_refuses_contradictions',
            'pytest:tests/remediation/test_p03r02c.py::test_revoked_scope_rejects_commit_and_replay',
            'pytest:tests/test_phase03_permissions.py::test_new_visibility_actions_are_explicit_and_never_backfilled')),
        ('Excel 预览错误与原子提交', (
            'pytest:tests/remediation/test_p03r06.py::test_creator_template_xlsx_error_preview_confirm_and_admission',
            'pytest:tests/test_phase03_excel.py::test_roster_preview_binding_expiry_and_scope',
            'pytest:tests/test_phase03_excel.py::test_xlsx_security_limits_and_formula_rejection',
            'pytest:tests/test_phase03_excel.py::test_roster_replay_rechecks_study_configure')),
        ('并发 revision、重新认证与脱敏审计', (
            'pytest:tests/remediation/test_p03r02b.py::test_confirm_reauth_revision_binding_and_repeat_refusal',
            'pytest:tests/remediation/test_p03r02b.py::test_audit_failure_rolls_back_everything',
            'pytest:tests/test_phase03_accounts.py::test_reauth_revision_csrf_and_atomic_audit',
            'pytest:tests/test_phase03_permissions.py::test_concurrent_confirmations_apply_exactly_once')),
        ('旧库迁移对账', (
            'pytest:tests/test_phase03_migration.py::test_legacy_fixture_migration_is_additive_and_preserves_references',
            'pytest:tests/test_phase03_migration.py::test_migration_tool_rehearses_named_copy_and_refuses_existing_destination',
            'boundary:boundary_old_rows_preserved',
            'boundary:boundary_legacy_semantics_kept')),
    )),
    'T27': ('公开默认关闭、私有不泄漏、当前发行归属/批准校验、切换只影响新 session；旧链接/待上传/恢复保持引用；结束无开始按钮；删除标记与墓碑', (
        ('公开发布默认关闭、私有内容不泄漏', (
            'pytest:tests/test_phase03_releases.py::test_publication_migration_defaults_and_preserves_legacy_bindings',
            'pytest:tests/test_phase03_releases.py::test_stable_entry_page_states_for_private_closed_and_unset_studies',
            'pytest:tests/test_phase03_portal.py::test_private_paused_closed_and_no_current_cases_behave_as_03c',
            'pytest:tests/test_phase03_releases.py::test_portal_lists_only_public_open_current_and_hides_roster_and_history')),
        ('当前发行归属、批准与同研究校验', (
            'pytest:tests/test_phase03_releases.py::test_selection_requires_authority_revision_and_same_study_approval',
            'pytest:tests/test_phase03_releases.py::test_selection_accepts_recruitment_or_configuration_authority',
            'pytest:tests/test_phase03_releases.py::test_publication_rejects_deactivated_actor_without_writes')),
        ('切换只影响新 session；旧链接/待上传/恢复保持引用', (
            'pytest:tests/test_phase03_releases.py::test_switch_keeps_old_session_uploads_recovery_config_export_and_resources',
            'pytest:tests/test_phase03_releases.py::test_operation_retry_returns_original_session_before_new_current_policy',
            'pytest:tests/test_phase03_portal.py::test_old_bound_sessions_keep_their_release_after_a_current_switch',
            'shell:web: unrelated tombstone never turns a missing candidate into an uploaded claim')),
        ('结束/私有研究不显示开始按钮', (
            'pytest:tests/test_phase03_releases.py::test_stable_entry_page_binds_current_release_and_stale_admission_fails',
            'pytest:tests/test_phase03_releases.py::test_policy_and_recruitment_never_auto_publish',
            'pytest:tests/test_phase03_windows_packages.py::test_windows_current_release_never_falls_back_to_a_web_start')),
        ('删除标记拒绝写入、清理、审计与墓碑（remediation）', (
            'pytest:tests/remediation/test_p03r04.py::test_marked_and_deleted_study_refuses_every_entry_point',
            'pytest:tests/remediation/test_p03r04.py::test_cleanup_clears_dependencies_files_shared_bytes_and_audit',
            'pytest:tests/remediation/test_p03r04_regressions.py::test_admission_operation_binding_before_and_after_cleanup',
            'pytest:tests/remediation/test_p03r04r.py::test_retained_actions_keep_minimal_study_uuid',
            'pytest:tests/remediation/test_p03r09c.py::test_real_deletion_server_client_boundaries',
            'boundary:boundary_deletion_revocation',
            'boundary:boundary_source_unchanged')),
    )),
    'T28': ('六位码爆破、过期、尝试上限、重放、跨 session/设备、签发人撤权、并发消费；码单独无效、旧长许可兼容', (
        ('签发人权限、绑定与限速', (
            'pytest:tests/test_phase03_recovery_codes.py::test_issue_requires_current_authority_and_binds_the_ticket',
            'pytest:tests/test_phase03_recovery_codes.py::test_issue_is_rate_limited_per_study_and_issuer',
            'pytest:tests/test_phase03_recovery_codes.py::test_redeem_rechecks_live_issuer_authority')),
        ('码单独无效：设备证明与固定绑定必需', (
            'pytest:tests/test_phase03_recovery_codes.py::test_redemption_requires_device_proof_and_fixed_binding',
            'pytest:tests/test_phase03_recovery_codes.py::test_named_continuation_requires_proof_and_configured_credentials',
            'pytest:tests/test_phase03_shell.py::test_recovery_code_needs_device_proof_and_binding')),
        ('爆破、尝试上限、过期与重放', (
            'pytest:tests/test_phase03_recovery_codes.py::test_failed_attempts_commit_and_cap_at_five',
            'pytest:tests/test_phase03_recovery_codes.py::test_expiry_and_newer_issuance_invalidate_older_codes',
            'pytest:tests/test_phase03_recovery_codes.py::test_one_abusive_client_is_bounded_without_locking_another_client',
            'pytest:tests/test_phase03_recovery_codes.py::test_instance_wide_ceiling_bounds_distributed_guessing')),
        ('并发消费恰好一次；码不落日志/审计', (
            'pytest:tests/test_phase03_recovery_codes.py::test_concurrent_redemption_consumes_the_code_once',
            'pytest:tests/test_phase03_recovery_codes.py::test_no_code_or_proof_is_stored_in_throttle_or_audit')),
        ('撤销与旧长许可兼容（真实壳）', (
            'pytest:tests/test_phase03_recovery_codes.py::test_code_cannot_bypass_revocation_and_legacy_permit_coexists',
            'pytest:tests/test_phase03_recovery_codes.py::test_expired_finished_queue_recovers_data_only_with_closed_completion',
            'shell:native long-permit recovery keeps the original session',
            'shell:native six-digit code recovery keeps the original session')),
    )),
    'T29': ('真实 Web/macOS 同一构建动态三模式，GEC 前后壳与无障碍状态；完整包完整性、断网重开与清理墓碑、finish 不更改科学顺序', (
        ('真实 Web/macOS 同一构建三模式', (
            'shell:本轮构建绑定：Web 与 macOS 产物绑定本轮源码摘要',
            'shell:web: anonymous session committed four records',
            'shell:web: id-mode session committed four records',
            'shell:web: code recovery keeps the original session with a second segment',
            'pytest:tests/remediation/test_p03r10.py::test_real_local_auto_run_matches_golden',
            'pytest:tests/remediation/test_p03r10.py::test_real_web_preview_matches_golden')),
        ('GEC 前后壳状态与科学顺序不变', (
            'pytest:tests/remediation/test_p03r09a.py::test_real_native_shell_harness',
            'pytest:tests/remediation/test_p03r09a.py::test_real_chrome_local_preview_finish_download_and_storage_failure',
            'shell:native named continuation never replays trial 1',
            'shell:web: recovered session never replays trial 1',
            'pytest:tests/test_phase03_shell.py::test_bootstrap_is_assembly_only_and_scientific_task_unchanged')),
        ('完整包完整性与平台入口', (
            'package:清单成员集合与实际成员完全一致',
            'package:可执行文件、资源与签名逐字节未改且模式保持',
            'package:清单声明 macOS 应用根',
            'pytest:tests/test_phase03_packages.py::test_approval_publishes_unchanged_frozen_complete_package')),
        ('断网重开、清理墓碑与 finish 不改科学顺序', (
            'shell:web: offline local commit keeps the first trial boundary',
            'shell:web: anonymous session reached the cleaned tombstone',
            'shell:native data-only recovery ends in a cleaned tombstone',
            'package:确认上传后本地留下同一会话的清理墓碑')),
        ('真实 Windows x64 原生运行（外部设备）', (
            'windows_runtime:WN01', 'windows_runtime:WN02', 'windows_runtime:WN03',
            'windows_runtime:WN04', 'windows_runtime:WN05', 'windows_runtime:WN06')),
    )),
    'T30': ('Dashboard/模块导航/搜索筛选分页，三主题两语言、键盘与对比度；不同账号控件与服务端权限一致；portal 实测', (
        ('Dashboard、模块导航、搜索/筛选/分页', (
            'pytest:tests/test_phase03_queries.py::test_dashboard_cards_stable_navigation_and_module_pages',
            'pytest:tests/test_phase03_queries.py::test_sessions_exact_code_search_status_filter_timezone_and_pagination',
            'pytest:tests/test_phase03_queries.py::test_roster_query_identity_gate_and_pagination',
            'pytest:tests/test_phase03_queries.py::test_session_page_loading_bounded_and_exact_on_large_synthetic_data')),
        ('三主题两语言、键盘与对比度', (
            'pytest:tests/test_phase03_ui_browser.py::test_chrome_modules_languages_themes_keyboard_and_narrow_layout',
            'pytest:tests/test_phase03_portal.py::test_language_and_theme_preferences_without_open_redirect')),
        ('不同账号控件与服务端权限一致', (
            'pytest:tests/test_phase03_ui_browser.py::test_chrome_permission_routes_forged_requests_and_cookie_isolation',
            'pytest:tests/test_phase03_queries.py::test_permission_separation_and_forged_requests',
            'pytest:tests/remediation/test_p03r05.py::test_actual_chrome_v2_matrix_one_row_drafts_languages_and_themes',
            'pytest:tests/remediation/test_p03r05r.py::test_actual_chrome_matrix_dialog_cancel_refusal_and_inflight_lock')),
        ('portal 与名单查询真实浏览器实测', (
            'pytest:tests/test_phase03_ui_browser.py::test_chrome_session_roster_queries_and_bilingual_portal',
            'pytest:tests/test_phase03_ui_browser.py::test_chrome_bilingual_matrix_controls_previews_and_reauth_errors',
            'pytest:tests/remediation/test_p03r01c.py::test_actual_chrome_single_link_and_redacted_output')),
    )),
    'T03': ('被试只能提交自己的 session；A 研究者只能访问 A 的对象；改 URL/对象 ID 同样拒绝', (
        ('上传/下载的对象授权与范围', (
            'pytest:tests/remediation/test_p03r02c.py::test_study_visibility_and_creation_use_stored_policy',
            'pytest:tests/remediation/test_p03r07.py::test_v2_permission_matrix_metadata_whitelist_and_revocation',
            'pytest:tests/test_phase03_packages.py::test_native_upload_requires_build_upload_scope',
            'pytest:tests/test_phase03_packages.py::test_artifact_download_denied_outside_the_build_scope')),
    )),
    'T04': ('实验不能取得管理 Cookie/上下文；管理接口拒绝跨界变更', (
        ('管理/实验 host 与 Cookie 隔离', (
            'pytest:tests/remediation/test_p03r01c.py::test_explicit_origin_wins_over_host_and_server_ports',
            'pytest:tests/remediation/test_p03r01c.py::test_malformed_configuration_is_a_controlled_rejection',
            'pytest:tests/test_phase03_releases.py::test_hosts_and_admin_cookie_are_isolated',
            'pytest:tests/test_phase03_releases.py::test_www_host_only_links_to_the_portal',
            'pytest:tests/test_phase03_ui_browser.py::test_chrome_permission_routes_forged_requests_and_cookie_isolation',
            'pytest:tests/remediation/test_p03r02c.py::test_v1_route_boundary_is_unchanged_and_v2_is_kernel_only')),
    )),
    'T06': ('AC 丢失/补传轮次与本地保存状态准确，不把未完成当成功', (
        ('真实 GEP 全链路的 ACK 丢失与补传', (
            'shell:native ACK-loss retransmission stored no duplicates',
            'shell:native dropped ACK left no unacknowledged batch',
            'shell:native retry deadline is persisted and not due',
            'shell:native not-due completion retry sends nothing',
            'pytest:tests/remediation/test_p03r09cr.py::test_real_persisted_retry_counts_and_progress_reset')),
        ('本地保存状态与失败/成功面准确', (
            'pytest:tests/remediation/test_p03r09cr.py::test_real_receipt_and_front_locked_offer_no_failure_export',
            'pytest:tests/remediation/test_p03r10.py::test_real_local_auto_run_matches_golden',
            'shell:native declared completion is not an acknowledgement',
            'shell:native reopened store keeps an unreceived completion and four pending records')),
    )),
    'T07': ('release/schema/实例/用途固定；S1/S2 不串数据；新版发布不重标旧记录', (
        ('release/schema/实例/用途固定与旧绑定', (
            'pytest:tests/remediation/test_p03r07.py::test_v1_format_is_unchanged_and_never_backfilled',
            'pytest:tests/test_phase03_packages.py::test_descriptor_only_and_web_releases_keep_the_legacy_contract',
            'pytest:tests/test_phase03_windows_packages.py::test_windows_descriptor_only_release_keeps_the_legacy_contract',
            'pytest:tests/test_phase03_windows_packages.py::test_windows_tamper_rollback_and_old_release_binding')),
        ('S1/S2 不串数据；新发布不重标旧记录', (
            'pytest:tests/remediation/test_p03r08.py::test_cached_zip_is_bound_to_the_frozen_export_and_bounded',
            'shell:web: cleaned device state starts a fresh session',
            'shell:native unrelated tombstone does not block a new study session')),
        ('大包中断不发布半成品/不覆盖旧发行', (
            'pytest:tests/test_phase03_packages.py::test_interrupted_packaging_never_replaces_the_previous_release',
            'pytest:tests/test_phase03_packages.py::test_content_addressed_storage_never_overwrites',
            'pytest:tests/remediation/test_p03r08.py::test_disk_failure_unknown_state_and_deadline_never_serve_a_half_package',
            'pytest:tests/test_phase03_windows_packages.py::test_windows_program_rejects_undigested_bytes_and_bounds')),
    )),
    'T08': ('固定快照导出时迟到记录不改旧产物；下载时撤权拒绝；产物无多余字段', (
        ('固定快照、迟到记录与撤权', (
            'pytest:tests/remediation/test_p03r07.py::test_v2_snapshot_is_byte_frozen_and_roster_views_are_exact',
            'pytest:tests/remediation/test_p03r07.py::test_v2_permission_matrix_metadata_whitelist_and_revocation',
            'pytest:tests/remediation/test_p03r07r.py::test_v1_rendering_bytes_stay_a_fixed_golden',
            'pytest:tests/test_phase03_packages.py::test_artifact_download_immutable_and_reauthorized')),
        ('真实包导出逐条对账、产物无多余字段', (
            'package:本地记录与授权 JSONL 导出按事件 ID/值一致',
            'pytest:tests/remediation/test_p03r08.py::test_zip_and_metadata_permission_matrix_and_listing_projection',
            'shell:native password: authorized JSONL matches local by event id/value')),
    )),
    'T11': ('暂停/关闭后旧上传按窗口处理；到期重新认证；重开不自动传撤回数据', (
        ('暂停/关闭后旧上传与到期重新认证', (
            'pytest:tests/test_phase03_releases.py::test_policy_and_recruitment_never_auto_publish',
            'pytest:tests/test_phase03_releases.py::test_operation_retry_returns_original_session_before_new_current_policy',
            'pytest:tests/test_phase03_releases.py::test_switch_keeps_old_session_uploads_recovery_config_export_and_resources',
            'shell:native expired-token candidate starts at the first boundary')),
        ('重开不自动传撤回数据、显式确认', (
            'shell:web: finished candidate asks for an explicit data-only confirmation',
            'shell:web: no candidate is adopted before the explicit confirmation',
            'pytest:tests/remediation/test_p03r09b.py::test_real_native_delivery_rounds',
            'pytest:tests/remediation/test_p03r04_regressions.py::test_admission_operation_binding_before_and_after_cleanup')),
    )),
    'T13': ('邀请重复/过期/撤销；有限 Admin 不能委派或恢复接管高权账号', (
        ('实例角色与接管边界', (
            'pytest:tests/test_phase03_accounts.py::test_admin_cannot_manage_owner_or_change_roles',
            'pytest:tests/test_phase03_accounts.py::test_higher_grant_credential_takeover_refused',
            'pytest:tests/test_phase03_accounts.py::test_takeover_guard_is_study_scoped_and_includes_nondelegable',
            'pytest:tests/remediation/test_p03r02a.py::test_takeover_dominates_before_after_and_future_defaults',
            'pytest:tests/remediation/test_p03r02a.py::test_admin_cannot_transfer_owner_controlled_switches',
            'pytest:tests/remediation/test_p03r03ar.py::test_account_activation_refuses_weak_atomically_and_keeps_the_token')),
    )),
    'T14': ('包路径穿越、链接、重名、资源替换、外链 schema、限额拒绝；预览不带管理身份', (
        ('恶意包与预览隔离', (
            'pytest:tests/test_phase03_packages.py::test_native_program_rejects_hostile_archive',
            'pytest:tests/test_phase03_packages.py::test_native_program_rejects_mismatched_digest_and_bounds',
            'pytest:tests/test_phase03_windows_packages.py::test_windows_program_rejects_hostile_archive',
            'pytest:tests/test_phase03_windows_packages.py::test_windows_program_rejects_members_the_freeze_would_generate',
            'pytest:tests/test_phase03_packages.py::test_unknown_artifact_versions_and_self_reference_fail_closed')),
    )),
    'T15': ('名单/XLSX 前导零、空白、公式安全与密码原值', (
        ('CSV/XLSX 文本契约与公式安全', (
            'pytest:tests/remediation/test_p03r06.py::test_csv_and_xlsx_share_the_text_contract',
            'pytest:tests/remediation/test_p03r06r.py::test_csv_whitespace_only_id_keeps_its_real_line_number',
            'pytest:tests/remediation/test_p03r06r.py::test_mixed_batches_write_nothing_and_whitespace_passwords_are_kept',
            'pytest:tests/remediation/test_p03r06t.py::test_windows_roster_step_imports_special_values_exactly',
            'pytest:tests/test_phase03_excel.py::test_xlsx_sparse_dimension_header_formula_and_relationship_rejection')),
    )),
    'T16': ('日志、报告、包、镜像和导出不带秘密/真实数据', (
        ('模板、审计、包与导出无秘密', (
            'pytest:tests/remediation/test_p03r01c.py::test_actual_tokens_never_reach_new_evidence_or_captured_output',
            'pytest:tests/remediation/test_p03r03b.py::test_permanent_delete_invalidates_credentials_and_keeps_history',
            'pytest:tests/remediation/test_p03r09cr.py::test_real_receipt_and_front_locked_offer_no_failure_export',
            'pytest:tests/test_phase03_accounts.py::test_governance_secrets_are_one_time_and_never_audited',
            'pytest:tests/test_phase03_recovery_codes.py::test_no_code_or_proof_is_stored_in_throttle_or_audit',
            'package:公开配置不含密码、名单或凭据',
            'docs:no_internal_paths_or_private_addresses')),
    )),
    'T18': ('SDK 不改变宿主 callback、随机化、评分、按键和 RT', (
        ('仅装配式 bootstrap 与科学任务不变', (
            'pytest:tests/remediation/test_p03r10.py::test_scientific_task_hash_unchanged',
            'pytest:tests/remediation/test_p03r10.py::test_wrapper_never_reads_a_clock_random_or_the_scene_tree',
            'pytest:tests/remediation/test_p03r10.py::test_developer_demo_is_executable',
            'pytest:tests/remediation/test_p03r10r.py::test_scientific_task_hash_unchanged',
            'pytest:tests/test_phase03_shell.py::test_bootstrap_is_assembly_only_and_scientific_task_unchanged')),
    )),
    'T19': ('同一实验主体无 GEC 专用调用；两类数据保持原值/单位/顺序', (
        ('无 GEC 专用科学分支与 golden 对账', (
            'pytest:tests/remediation/test_p03r10.py::test_real_local_auto_run_matches_golden',
            'pytest:tests/remediation/test_p03r10.py::test_real_gep_connection_matches_golden',
            'pytest:tests/remediation/test_p03r10r.py::test_real_gep_rejects_unregistered_schema',
            'pytest:tests/remediation/test_p03r10r.py::test_real_web_defaults_demo',
            'shell:native anonymous: server database matches local by event id/value',
            'shell:native id: authorized JSONL matches local by event id/value')),
    )),
    'T20': ('一个实测 OS 的独立 Godot 原生包，无 Runner/JS；原生事务、终止、ACK 丢失/DB 重启后去重，导出逐条匹配', (
        ('实测 macOS 原生包（新解包，无命令行配置替换）', (
            'package:本轮构建绑定：完整包程序绑定本轮唯一构建与源码摘要',
            'package:解包下载包的程序成员与绑定归档逐成员一致（含 .pck/动态库）',
            'shell:本轮构建绑定：解包 macOS 运行资源与绑定归档逐成员一致',
            'package:解包后程序存在且可执行',
            'package:外置冻结配置与 .app 同级（无需研究者替换）',
            'package:下载的 macOS 程序用自带配置完成同一合成任务',
            'package:未通过命令行替换连接配置',
            'shell:native dropped ACK left no unacknowledged batch',
            'shell:native ACK-loss retransmission stored no duplicates')),
        ('真实 Windows x64 原生运行（外部设备）', (
            'windows_preparation:kit_bytes_bound_to_current_source',
            'windows_runtime:WN01', 'windows_runtime:WN02', 'windows_runtime:WN03',
            'windows_runtime:WN04', 'windows_runtime:WN05', 'windows_runtime:WN06')),
    )),
    'T21': ('后台三种模式：无需 ID 有 UUID；预发名单 ID 保留 001、未知 ID 拒绝；跨研究同 ID 隔离', (
        ('三种准入模式、名单 ID 与跨研究隔离', (
            'pytest:tests/remediation/test_p03r06t.py::test_designer_caller_participants_are_admitted_over_real_http',
            'pytest:tests/remediation/test_p03r06t.py::test_windows_roster_step_duplicate_and_wrong_password_write_nothing',
            'pytest:tests/remediation/test_p03r09a.py::test_real_native_shell_harness',
            'pytest:tests/test_phase03_shell.py::test_frozen_id_release_requires_roster_id_after_study_policy_change',
            'shell:web: anonymous mode hides the ID field',
            'shell:native unknown roster ID is refused',
            'shell:native unrelated tombstone does not block a new study session')),
    )),
    'T22': ('配置导出无秘密、原生外置替换生效、默认/错 study/版本拒绝、Web 自动配置；大包中断不发布半成品', (
        ('配置导出无秘密与冻结配置生效', (
            'pytest:tests/remediation/test_p03r07r.py::test_deadline_covers_late_serialization_and_persistence',
            'pytest:tests/remediation/test_p03r08.py::test_over_limit_cells_keep_jsonl_and_untrusted_names_never_reach_files',
            'pytest:tests/test_phase03_packages.py::test_unknown_artifact_versions_and_self_reference_fail_closed',
            'package:公开配置不含密码、名单或凭据',
            'windows_preparation:human_package_without_credentials')),
        ('大包中断不发布半成品', (
            'pytest:tests/remediation/test_p03r08.py::test_disk_failure_unknown_state_and_deadline_never_serve_a_half_package',
            'pytest:tests/test_phase03_packages.py::test_interrupted_packaging_never_replaces_the_previous_release')),
    )),
    'T23': ('e1/c1 已提交、e2/c2 提交时终止；恢复只取事务完整边界；已完成 trial 不重放；错版本/双窗口拒绝', (
        ('checkpoint 事务边界与已完成 trial 不重放（真实 GEP 壳）', (
            'shell:native checkpoint bound to the first trial',
            'shell:native long-permit recovery continues at the trial boundary',
            'shell:native named continuation never replays trial 1',
            'shell:web: recovered session never replays trial 1',
            'shell:web: continued session opened a second segment')),
        ('提交时终止的完整边界恢复与 data-only', (
            'pytest:tests/remediation/test_p03r09ar.py::test_real_native_finish_guards_harness',
            'pytest:tests/remediation/test_p03r09ar.py::test_real_chrome_bridge_export_guard_and_data_only',
            'pytest:tests/remediation/test_p03r09b.py::test_real_native_delivery_rounds')),
        ('错版本/双窗口拒绝', (
            'shell:native second writer is refused',
            'pytest:tests/remediation/test_p03r10r.py::test_real_gep_rejects_unregistered_schema')),
    )),
    'T24': ('活动任务有恢复依赖时不得删；清理中终止可重试；共享设备新参与锁旧恢复且无泄露', (
        ('清理中止/终止可重试与数据边界（真实 SQLite）', (
            'browser:native_cleanup.spec.js::native durable completion and cleanup: ack_abort',
            'browser:native_cleanup.spec.js::native durable completion and cleanup: completion_abort',
            'browser:native_cleanup.spec.js::native durable completion and cleanup: cleanup_abort',
            'browser:native_cleanup.spec.js::native durable completion and cleanup: cleanup_before_commit',
            'browser:native_cleanup.spec.js::native durable completion and cleanup: cleanup_after_commit',
            'browser:native_transactions.spec.js::native SQLite abort and process kill preserve the prior complete checkpoint',
            'shell:native data-only recovery uploaded the retained records')),
        ('共享设备新参与锁旧恢复且无泄露', (
            'browser:native_shared_device.spec.js::native new participation protects old data and bounded buffer rejects overflow',
            'browser:storage.spec.js::single writer and shared-device new participation locks old front recovery',
            'browser:native_nonblocking.spec.js::native records and checkpoints continue while real upload ACK is held',
            'browser:native_policy.spec.js::native authorized data-only recovery unpauses uploads without replaying trials; finished=false',
            'browser:native_policy.spec.js::native authorized data-only recovery unpauses uploads without replaying trials; finished=true',
            'shell:native new participation front-locks the older session',
            'shell:native second writer is refused')),
    )),
}
EXTERNAL_SELECTOR_PREFIXES = ('windows_runtime:', 'windows_preparation:')

# The new test module that owns the R11A tool/route boundary contracts; it is
# part of the remediation suite and is additionally named here so a deleted or
# uncollected module is refused instead of silently shrinking the gate.
REQUIRED_NEW_TESTS = (
    'tests/remediation/test_p03r11a.py::test_orchestrator_matrix_marks_missing_selector_not_run',
    'tests/remediation/test_p03r11a.py::test_recovery_code_clause_never_passes_without_its_evidence',
    'tests/remediation/test_p03r11a.py::test_cleanup_and_checkpoint_clauses_never_pass_without_their_evidence',
    'tests/remediation/test_p03r11a.py::test_orchestrator_refuses_an_existing_or_outside_root',
    'tests/remediation/test_p03r11a.py::test_boundary_evidence_root_guard_refuses_existing_and_symlink',
    'tests/remediation/test_p03r11a.py::test_windows_prepare_refuses_an_existing_or_outside_root',
    'tests/remediation/test_p03r11a.py::test_windows_verify_refuses_before_any_start_without_ssh_configuration',
    'tests/remediation/test_p03r11a.py::test_strict_manifest_refuses_empty_missing_tampered_extra_and_unsafe_members',
    'tests/remediation/test_p03r11a.py::test_integration_tools_reject_symlinked_inputs_and_keep_canary_bytes',
    'tests/remediation/test_p03r11a.py::test_artifact_binding_refuses_legacy_defaults_and_stale_or_tampered_bytes',
    'tests/remediation/test_p03r11a.py::test_verifiers_refuse_unbound_or_partial_explicit_artifacts',
    'tests/remediation/test_p03r11a.py::test_orchestrator_passes_bound_artifacts_to_the_tools',
    # P03R11AR: the corrected evidence chain (complete Windows evidence, real
    # Web SDK source binding, ancestor-link refusals, unpacked runtime members).
    'tests/remediation/test_p03r11ar.py::test_windows_summary_only_report_is_refused',
    'tests/remediation/test_p03r11ar.py::test_windows_run_evidence_is_re_read_and_bound',
    'tests/remediation/test_p03r11ar.py::test_windows_run_evidence_refuses_stale_tampered_and_external_evidence',
    'tests/remediation/test_p03r11ar.py::test_program_source_binding_includes_web_sdk_and_packager_inputs',
    'tests/remediation/test_p03r11ar.py::test_binding_entry_points_refuse_ancestor_links_with_canary',
    'tests/remediation/test_p03r11ar.py::test_kit_manifest_refuses_parent_path_links_with_canary',
    'tests/remediation/test_p03r11ar.py::test_unpacked_runtime_members_must_match_the_bound_archive',
)


class GateError(Exception):
    pass


# --- guards and helpers -----------------------------------------------------

def guard_evidence_root(explicit):
    """A brand-new unique evidence root inside the project; refusal is a stop."""
    root = Path(explicit).expanduser()
    if not root.is_absolute():
        root = ROOT / root
    root = Path(os.path.abspath(root))
    project = Path(os.path.abspath(ROOT))
    try:
        relative = root.relative_to(project)
    except ValueError:
        raise GateError(f'refusing an evidence root outside the project: {root}') from None
    current = project
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise GateError(f'refusing a symlinked evidence path component: {current}')
    if root.exists():
        raise GateError(f'refusing an existing evidence root; each run needs a new unique root: {root}')
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    return root


def new_unique_root(base):
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    for _ in range(20):
        root = base / f'{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}-{secrets.token_hex(4)}'
        try:
            root.mkdir(mode=0o700, exist_ok=False)
            return root
        except FileExistsError:
            continue
    raise GateError(f'could not create a unique evidence root under {base}')


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    return path


# --- this round's bound artifacts -------------------------------------------

def guard_build_root(raw):
    """This round's brand-new unique build root, never build/ or build/native."""
    if not raw:
        raise GateError('the artifact binding declares no build root')
    root = Path(os.path.abspath(Path(str(raw)).expanduser()))
    link = symlinked_component(root, ROOT)
    if link is not None:
        raise GateError(f'refusing a build root path that contains a symbolic link: {link}')
    if root.is_symlink():
        raise GateError(f'refusing a symlinked build root: {root}')
    try:
        relative = root.relative_to(Path(os.path.abspath(BUILD_BASE)))
    except ValueError:
        raise GateError(f'refusing a build root outside the remediation build base: {root}') from None
    if not relative.parts:
        raise GateError(f'refusing the build base itself as a build root: {root}')
    if not root.is_dir():
        raise GateError(f'the build root is missing: {root}')
    return root


def symlinked_component(path, boundary):
    """First symbolic-link component of ``path`` at or below ``boundary``.

    ``remediation_windows`` owns the lstat-based walk; this thin wrapper keeps
    the acceptance gate's refusals readable. ``None`` means either no link was
    found or the path is outside the boundary.
    """
    import remediation_windows
    return remediation_windows.symlinked_component(path, boundary)


def validate_artifact_binding(document, expected_source_digest, required=BINDING_ARTIFACTS,
                              expected_source_inputs=None):
    """Bind every artifact to this round's unique root and current sources.

    The build root must be a subdirectory of the remediation build base (never
    ``build`` or ``build/native``), no path component may be a symbolic link,
    the source digest must equal the current program-source digest **before and
    after** the build with an identical input set, and every recorded path must
    live inside the root with byte-identical content. Old default artifacts
    therefore cannot be bound to the new sources, and a stale, relocated or
    tampered binding is refused before any verifier starts.
    """
    import remediation_windows

    if not isinstance(document, dict) or document.get('format') != ARTIFACT_BINDING_FORMAT \
            or document.get('verdict') != 'ok':
        raise GateError('the artifact binding is missing or not ok')
    root = guard_build_root(document.get('build_root'))
    if document.get('program_source_digest') != expected_source_digest:
        raise GateError('the artifact binding was produced from different program sources')
    if document.get('program_source_digest_after') != expected_source_digest:
        raise GateError('the artifact binding did not record the same program sources after the build')
    inputs_before = document.get('program_source_inputs')
    inputs_after = document.get('program_source_inputs_after')
    for label, records in (('before the build', inputs_before), ('after the build', inputs_after)):
        problems = remediation_windows.input_set_problems(records, label=f'artifact binding input set {label}')
        if problems:
            raise GateError('; '.join(problems))
    if remediation_windows.input_set_key(inputs_before) != remediation_windows.input_set_key(inputs_after):
        raise GateError('the artifact binding source inputs changed during the build')
    if expected_source_inputs is not None \
            and remediation_windows.input_set_key(inputs_before) \
            != remediation_windows.input_set_key(expected_source_inputs):
        raise GateError('the artifact binding source inputs are not the current source inputs')
    entries = document.get('artifacts') or {}
    resolved = {}
    for key in required:
        entry = entries.get(key)
        if not isinstance(entry, dict) or not entry.get('path'):
            raise GateError(f'the artifact binding has no {key} entry')
        path = Path(str(entry['path'])).expanduser()
        if not path.is_absolute():
            raise GateError(f'the bound {key} path is not absolute: {path}')
        path = Path(os.path.abspath(path))
        try:
            path.relative_to(root)
        except ValueError:
            raise GateError(f'the bound {key} is outside this round\'s build root: {path}') from None
        link = symlinked_component(path, ROOT)
        if link is not None:
            raise GateError(f'the bound {key} path contains a symbolic link: {link}')
        if path.is_symlink() or not path.is_file():
            raise GateError(f'the bound {key} is missing: {path}')
        if sha256_file(path) != entry.get('sha256') or path.stat().st_size != entry.get('size'):
            raise GateError(f'the bound {key} bytes do not match the binding: {path}')
        resolved[key] = str(path)
    return resolved


def build_current_artifacts(build_root, runner):
    """Export and package brand-new Web and macOS bytes into the unique root.

    The pinned local Godot 4.7.2 toolchain runs exactly the export/packaging
    steps of ``tools/build.py``/``tools/package_build.py``, but every output
    stays under ``build_root``: the historical ``build/native`` bytes and
    ``build/synthetic_web.zip`` are never rewritten or reused. The returned
    binding records the program, descriptor and current program-source digests
    for the same evidence set the verifiers then consume.
    """
    import remediation_windows

    # The real program inputs (Godot project, packagers, Web SDK) are recorded
    # before and after the export/package steps: a source change during the
    # build is refused instead of being presented as one stable build.
    sources_before = remediation_windows.program_source_inputs()
    digest_before = remediation_windows.program_source_digest()
    godot = remediation_windows.godot_binary()
    version = subprocess.run([godot, '--version'], capture_output=True, text=True, timeout=120)
    toolchain = (version.stdout or '').strip()
    if version.returncode != 0 or not toolchain.startswith('4.7.2.stable.'):
        raise GateError(f'the pinned Godot 4.7.2 toolchain is unavailable: {toolchain or version.stderr.strip()}')
    build_root = Path(build_root)
    project = ROOT / 'examples' / 'synthetic_experiment'

    web_root = build_root / 'web'
    (web_root / 'gec').mkdir(parents=True, exist_ok=False)
    for name in ('sdk.js', 'bridge.js', 'inputs.js', 'shell.js'):
        shutil.copy2(ROOT / 'packages' / 'gec_web' / name, web_root / 'gec' / name)
    export = runner([godot, '--headless', '--path', str(project), '--export-release', 'Web',
                     str(web_root / 'index.html')], build_root / 'web_export.log', timeout=1800)
    if export.returncode != 0:
        raise GateError(f'the Web export failed ({export.returncode}); see {build_root / "web_export.log"}')
    missing = [name for name in WEB_MEMBERS if not (web_root / name).is_file()]
    if missing:
        raise GateError('the Web export is incomplete: ' + ', '.join(missing))
    digest = hashlib.sha256()
    # The server recomputes the Web program digest over the members in sorted
    # name order (``web/<name>`` then bytes), exactly like the project packager.
    for name in sorted(WEB_MEMBERS):
        digest.update(('web/' + name).encode('utf-8'))
        digest.update((web_root / name).read_bytes())
    document = json.loads((project / 'descriptor.json').read_text(encoding='utf-8'))
    document['platform'] = 'godot_web'
    document['program_sha256'] = digest.hexdigest()
    web_zip = build_root / 'synthetic_web.zip'
    with zipfile.ZipFile(web_zip, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(document))
        for name in WEB_MEMBERS:
            archive.write(web_root / name, f'web/{name}')

    native_root = build_root / 'native'
    native_root.mkdir(parents=True, exist_ok=False)
    export = runner([godot, '--headless', '--path', str(project), '--export-release', 'macOS',
                     str(native_root / 'synthetic.zip')], build_root / 'native_export.log', timeout=1800)
    if export.returncode != 0:
        raise GateError(f'the macOS export failed ({export.returncode}); see {build_root / "native_export.log"}')
    native_zip = native_root / 'synthetic.zip'
    if not native_zip.is_file():
        raise GateError('the macOS export produced no program archive')
    document = json.loads((project / 'descriptor.json').read_text(encoding='utf-8'))
    document['platform'] = 'macos_arm64'
    document['program_sha256'] = sha256_file(native_zip)
    native_descriptor = native_root / 'descriptor.json'
    native_descriptor.write_text(json.dumps(document), encoding='utf-8')
    with zipfile.ZipFile(native_zip) as source:
        for entry in source.infolist():
            destination = native_root / entry.filename
            if entry.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read(entry))
            destination.chmod((entry.external_attr >> 16) & 0o777 or 0o644)
    native_binary = native_root / 'GEP Synthetic Experiment.app' / 'Contents' / 'MacOS' / 'GEP Synthetic Experiment'
    if not native_binary.is_file():
        raise GateError(f'the extracted macOS program is missing: {native_binary}')
    # Member-by-member binding of the actual runtime resources the shell and
    # package steps will run: the .pck, dynamic libraries and helper files must
    # match the archive, not only the executable.
    extracted_problems = remediation_windows.verify_extracted_archive(
        native_zip, native_root, label='this round\'s macOS program archive')
    if extracted_problems:
        raise GateError('the extracted macOS runtime does not match the bound archive: '
                        + '; '.join(extracted_problems[:3]))
    sources_after = remediation_windows.program_source_inputs()
    digest_after = remediation_windows.program_source_digest()
    if digest_before != digest_after or remediation_windows.input_set_key(sources_before) \
            != remediation_windows.input_set_key(sources_after):
        raise GateError('the program sources changed during this round\'s Web/macOS build; '
                        'the build is not a stable build')

    entries = {key: {'path': str(path.resolve()), 'sha256': sha256_file(path), 'size': path.stat().st_size}
               for key, path in (('web_zip', web_zip), ('native_zip', native_zip),
                                 ('native_descriptor', native_descriptor), ('native_binary', native_binary))}
    binding = {'format': ARTIFACT_BINDING_FORMAT, 'verdict': 'ok',
               'build_root': str(build_root.resolve()),
               'program_source_digest': digest_before,
               'program_source_digest_after': digest_after,
               'program_source_inputs': sources_before,
               'program_source_inputs_after': sources_after,
               'godot': toolchain, 'artifacts': entries,
               'created_at': datetime.now(timezone.utc).isoformat()}
    write_json(build_root / 'artifact_binding.json', binding)
    return binding


def verifier_command(name, script, target, artifacts):
    """The explicit bound invocation of one existing verifier tool."""
    command = [str(ROOT / '.venv' / 'bin' / 'python'), script, '--verify', '--root', str(target)]
    if artifacts:
        command += ['--binding', str(artifacts['binding_path'])]
        if name == 'shell':
            command += ['--web-zip', str(artifacts['web_zip'])]
        command += ['--native-zip', str(artifacts['native_zip'])]
        command += ['--native-descriptor', str(artifacts['native_descriptor']),
                    '--native-binary', str(artifacts['native_binary'])]
    return command


def parse_junit(path):
    """node id -> 'passed' | 'failed' | 'error' | 'skipped'."""
    if not Path(path).is_file():
        raise GateError(f'the JUnit report is missing: {path}')
    document = ElementTree.parse(path)
    results = {}
    for case in document.iter('testcase'):
        file_part = case.get('file')
        if not file_part:
            file_part = (case.get('classname') or '').replace('.', '/') + '.py'
        name = case.get('name') or ''
        node = f'{file_part}::{name}'
        outcome = 'passed'
        for child in case:
            if child.tag == 'failure':
                outcome = 'failed'
            elif child.tag == 'error':
                outcome = 'error'
            elif child.tag == 'skipped':
                outcome = 'skipped'
        results[node] = outcome
    return results


def selector_outcome(selector, node_results):
    """Resolve one pytest selector against recorded node outcomes."""
    key = selector.split(':', 1)[1].strip()
    matches = [outcome for node, outcome in node_results.items()
               if node == key or node.startswith(key + '[')]
    if not matches:
        return STATUS_NOT_RUN, 'selector was not collected'
    if any(outcome in ('failed', 'error') for outcome in matches):
        return STATUS_FAIL, 'a named case failed'
    if any(outcome == 'skipped' for outcome in matches):
        return STATUS_NOT_RUN, 'a named case was skipped'
    return STATUS_PASS, f'{len(matches)} named case(s) passed'


def _check_status(checks, key):
    """Resolve one shell/package check label; a missing label is never a pass."""
    if key.endswith('*'):
        matches = {label: status for label, status in checks.items() if label.startswith(key[:-1])}
    else:
        matches = {label: status for label, status in checks.items() if label == key}
    if not matches:
        return STATUS_NOT_RUN, 'no matching check label was produced'
    if STATUS_FAIL in matches.values():
        return STATUS_FAIL, 'a matched check failed'
    return STATUS_PASS, f'{len(matches)} matched check(s) passed'


def _playwright_json(log_path):
    if not Path(log_path).is_file():
        return None
    text = Path(log_path).read_text(encoding='utf-8', errors='replace')
    start = text.find('{')
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
        for spec in node.get('specs') or []:
            outcome = 'passed' if spec.get('ok') else 'failed'
            statuses = {result.get('status') for test in spec.get('tests') or []
                        for result in test.get('results') or []}
            if 'skipped' in statuses and statuses == {'skipped'}:
                outcome = 'skipped'
            found.append((str(spec.get('title')), outcome))
        for child in node.get('suites') or []:
            walk(child)

    for suite in (document or {}).get('suites') or []:
        walk(suite)
    return found


# --- step runners -----------------------------------------------------------

def run_boundary(runner, evidence_root):
    step = evidence_root / 'migration' / f'boundary-{secrets.token_hex(4)}'
    command = [str(ROOT / '.venv' / 'bin' / 'python'), 'tools/remediation_migration.py',
               '--verify-boundary', '--evidence-root', str(step)]
    log = evidence_root / 'migration.log'
    result = runner(command, log, timeout=1800)
    report = {'status': STATUS_FAIL, 'exit': result.returncode, 'evidence_root': str(step)}
    report_path = step / 'report.json'
    if report_path.is_file():
        try:
            document = read_json(report_path)
        except (OSError, ValueError) as error:
            report['error'] = f'the boundary report is unreadable: {type(error).__name__}'
            return report
        checks = document.get('checks') or {}
        report['checks'] = checks
        report['verdict'] = document.get('verdict')
        report['facts'] = document.get('boundary') or {}
        report['status'] = STATUS_PASS if document.get('verdict') == 'ok' and checks and all(checks.values()) \
            and result.returncode == 0 else STATUS_FAIL
    else:
        report['error'] = 'the boundary report was not written'
    return report


def run_pytest(runner, evidence_root, name, targets, env, timeout, forbid_skips=True):
    junit = evidence_root / f'{name}.junit.xml'
    log = evidence_root / f'{name}.log'
    command = [str(ROOT / '.venv' / 'bin' / 'python'), '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
               f'--junitxml={junit}', *targets]
    result = runner(command, log, timeout=timeout, env=env)
    nodes = {}
    if junit.is_file():
        nodes = parse_junit(junit)
    counts = {'passed': 0, 'failed': 0, 'error': 0, 'skipped': 0}
    for outcome in nodes.values():
        counts[outcome] = counts.get(outcome, 0) + 1
    ok = result.returncode == 0 and counts['failed'] == 0 and counts['error'] == 0
    if forbid_skips:
        ok = ok and counts['skipped'] == 0
    return {'name': name, 'exit': result.returncode, 'nodes': nodes, 'counts': counts,
            'junit': str(junit), 'log': str(log),
            'status': STATUS_PASS if ok else STATUS_FAIL}


def run_verifier_tool(runner, evidence_root, name, script, timeout, artifacts=None):
    """Run one existing phase03 verifier and read its per-check evidence.

    With ``artifacts`` the verifier is invoked with the explicit this-round
    binding and artifact paths; without them the verifier keeps its legacy
    unbound defaults, which the gate itself never selects.
    """
    target = evidence_root / name
    command = verifier_command(name, script, target, artifacts)
    log = evidence_root / f'{name}.log'
    result = runner(command, log, timeout=timeout)
    checks = {}
    error = None
    evidence_file = target / 'evidence.json'
    if evidence_file.is_file():
        try:
            document = read_json(evidence_file)
        except (OSError, ValueError) as problem:
            error = f'evidence.json is unreadable: {type(problem).__name__}'
            document = {}
        for check in document.get('checks') or []:
            checks[str(check.get('label'))] = STATUS_PASS if check.get('ok') else STATUS_FAIL
    else:
        error = 'evidence.json missing'
    failed = sorted(label for label, status in checks.items() if status == STATUS_FAIL)
    ok = result.returncode == 0 and bool(checks) and not failed and error is None
    return {'status': STATUS_PASS if ok else STATUS_FAIL, 'exit': result.returncode,
            'checks': checks, 'failed_checks': failed, 'error': error,
            'log': str(log), 'evidence': str(evidence_file) if evidence_file.is_file() else None}


def run_browser(runner, evidence_root, artifacts):
    """The affected legacy specs against one brand-new isolated instance.

    The instance, the Web archive and the native descriptor/program are exactly
    this round's bound artifacts, and the native specs' scratch storage lives
    under this run's unique evidence root (never the system temporary
    directory).
    """
    import phase03_verify_shell as shell_verify

    target = evidence_root / 'browser'
    target.mkdir(parents=True, exist_ok=True)
    scratch = target / 'native_scratch'
    scratch.mkdir(parents=True, exist_ok=True)
    verifier = shell_verify.Verify(target / 'instance')
    results = {'status': STATUS_FAIL, 'tests': {}, 'specs': [], 'error': None, 'instance': {},
               'artifacts': {key: artifacts[key] for key in BINDING_ARTIFACTS},
               'binding': artifacts['binding_path']}
    verifier.init_instance()
    verifier.start_server()
    try:
        job = {
            'admin_url': f'http://admin.localhost:{verifier.port}',
            'experiment_url': f'http://experiment.localhost:{verifier.port}',
            'credentials': {'username': 'synthetic_owner', 'password': verifier.owner_password},
            'web_zip': str(artifacts['web_zip']),
            'native_descriptor': str(artifacts['native_descriptor']),
            'connection_out': str(target / 'connection.json'),
            'study_url_file': str(target / 'study_url.txt'),
            'run_url_file': str(target / 'run_url.txt'),
        }
        job_path = target / 'setup_job.json'
        job_path.write_text(json.dumps(job), encoding='utf-8')
        setup_log = target / 'setup.log'
        with setup_log.open('w', encoding='utf-8') as stream:
            setup = subprocess.run(
                ['node', str(ROOT / 'tests' / 'browser' / 'phase03_acceptance_setup.mjs'), str(job_path)],
                cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, text=True, timeout=1800,
                env={**os.environ, 'GEP_T17_BROWSER': '1'})
        payload = None
        for line in setup_log.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                payload = json.loads(line)
                break
            except ValueError:
                continue
        if setup.returncode != 0 or not payload or not payload.get('ok'):
            results['error'] = 'the isolated instance setup failed'
            return results
        evidence = payload['evidence']
        results['instance'] = {key: evidence.get(key)
                               for key in ('study_id', 'release_id', 'build_id', 'run_url')}
        env = {
            **os.environ,
            'GEP_ISO_ADMIN_URL': job['admin_url'],
            'GEP_ISO_EXPERIMENT_URL': job['experiment_url'],
            'GEP_ISO_USERNAME': job['credentials']['username'],
            'GEP_ISO_PASSWORD': job['credentials']['password'],
            'GEP_ISO_CONNECTION': str(target / 'connection.json'),
            'GEP_ISO_DB': str(verifier.db_path),
            'GEP_ISO_DESCRIPTOR': str(artifacts['native_descriptor']),
            'GEP_ISO_WEB_ZIP': str(artifacts['web_zip']),
            'GEP_ISO_RUN_URL_FILE': str(target / 'run_url.txt'),
            'GEP_ISO_STUDY_URL_FILE': str(target / 'study_url.txt'),
            # The native specs run the current sources with the real Godot
            # editor and consume the same bound round artifacts as the shell
            # and package steps; their scratch storage stays under this run's
            # unique root, never the system temporary directory.
            'GEP_ISO_NATIVE_EDITOR': '1',
            'GEP_ISO_NATIVE_APP': str(artifacts['native_binary']),
            'GEP_ISO_SCRATCH': str(scratch),
        }
        for spec in BROWSER_SPECS:
            name = 'spec_' + Path(spec).stem
            record = runner(['node_modules/.bin/playwright', 'test', spec, '--reporter=json'],
                            target / f'{name}.log', 1800, env=env)
            results['specs'].append({'spec': spec, 'exit': record.returncode})
            document = _playwright_json(target / f'{name}.log')
            for title, outcome in _playwright_specs(document):
                results['tests'][f'{Path(spec).name}::{title}'] = outcome
    finally:
        verifier.stop_server()
    failed = [spec['spec'] for spec in results['specs'] if spec['exit'] != 0]
    if not results['specs'] or failed:
        results['status'] = STATUS_FAIL
        if failed:
            results['error'] = 'spec(s) failed: ' + ', '.join(failed)
    else:
        results['status'] = STATUS_PASS
    return results


def run_docs_check(evidence_root):
    internal = []
    private = []
    for name in PUBLIC_DOCS:
        path = ROOT / name
        if not path.is_file():
            internal.append({'file': name, 'marker': 'missing guide'})
            continue
        text = path.read_text(encoding='utf-8', errors='ignore')
        for marker in _INTERNAL_MARKERS:
            if marker in text:
                internal.append({'file': name, 'marker': marker})
        marker = private_endpoint_marker(text)
        if marker:
            private.append({'file': name, 'marker': marker})
    wording = []
    for name in HUMAN_TEST_DOCS:
        path = ROOT / name
        if not path.is_file():
            wording.append({'file': name, 'marker': 'missing template'})
            continue
        text = path.read_text(encoding='utf-8', errors='ignore')
        if '人工测试' not in text:
            wording.append({'file': name, 'marker': '人工测试 wording missing'})
        if 'T17' not in text:
            wording.append({'file': name, 'marker': 'T17 记录口径 missing'})
    report = {'internal_paths': internal, 'private_addresses': private, 'human_wording': wording}
    report['status'] = STATUS_PASS if not (internal or private or wording) else STATUS_FAIL
    write_json(evidence_root / 'docs_check.json', report)
    return report


def windows_preparation(base=None):
    """The newest fresh prepare report bound to the current program source.

    A preparation is never a run: ``windows_verified`` stays false here. A
    stale preparation (different program source digest) is NOT_RUN, never an
    inherited PASS. Every member manifest is verified strictly and fail-closed
    (non-empty required members, an actual member set equal to the declared set,
    digests/sizes, source binding and safe relative names); an unreadable report
    is an explicit failure, never a silent skip.
    """
    import remediation_windows

    expected_digest = remediation_windows.program_source_digest()
    expected_inputs = remediation_windows.program_source_inputs()
    base = Path(base) if base else ROOT / 'local_data' / 'phase03_remediation_20260923' / 'p03r11a_windows'
    candidates = sorted(base.glob('*/prepare_report.json'))
    newest = None
    malformed = []
    for path in candidates:
        try:
            document = read_json(path)
        except (OSError, ValueError):
            malformed.append(str(path))
            continue
        if not isinstance(document, dict) or document.get('verdict') != 'ok':
            continue
        if document.get('build', {}).get('program_source_digest') != expected_digest:
            continue
        newest = (path, document)
    if newest is None and malformed:
        return {'status': STATUS_FAIL, 'reason': 'a preparation report is unreadable',
                'problems': [f'unreadable prepare report: {name}' for name in malformed[-3:]],
                'expected_program_source_digest': expected_digest}
    if newest is None:
        return {'status': STATUS_NOT_RUN,
                'reason': 'no fresh preparation bound to the current program source digest',
                'expected_program_source_digest': expected_digest}
    path, document = newest
    root = path.parent
    problems = remediation_windows.verify_kit_strict(
        root / 'kit', expected_source_digest=expected_digest,
        expected_program_sha256=document.get('build', {}).get('program_sha256'),
        expected_source_inputs=expected_inputs)
    problems += remediation_windows.verify_human_strict(
        root / 'human', expected_source_digest=expected_digest)
    if document.get('build', {}).get('program_source_digest_after') != expected_digest:
        problems.append('the preparation did not record stable program sources before and after the build')
    human = root / 'human'
    for member in sorted(human.rglob('*')) if human.is_dir() else ():
        if member.is_file() and member.suffix in ('.md', '.json'):
            marker = private_endpoint_marker(member.read_text(encoding='utf-8', errors='ignore'))
            if marker:
                problems.append(f'human test package contains a recorded private endpoint ({marker})')
    kit_releases = {}
    kit_runtime = {}
    try:
        kit_releases = read_json(root / 'kit' / 'releases.json')
        kit_runtime = read_json(root / 'kit' / 'operator' / 'runtime.json')
    except (OSError, ValueError):
        pass
    return {'status': STATUS_FAIL if problems else STATUS_PASS,
            'prepare_root': str(root), 'prepare_report': str(path),
            'kit_root': str(root / 'kit'),
            'instance_id': kit_releases.get('instance_id') or kit_runtime.get('instance_id'),
            'integrity_sha256': (sha256_file(root / 'kit' / 'integrity.json')
                                 if (root / 'kit' / 'integrity.json').is_file() else None),
            'program_sha256': document.get('build', {}).get('program_sha256'),
            'program_source_digest': expected_digest,
            'program_source_inputs': expected_inputs,
            'windows_verified': False, 'problems': problems}


# ------------------------------------------------------------------ evaluation

def resolve_selector(selector, evidence):
    """Resolve one named selector against the freshly executed evidence."""
    source, _, key = selector.partition(':')
    if source == 'pytest':
        return selector_outcome(selector, {**(evidence.get('remediation') or {}).get('nodes', {}),
                                           **(evidence.get('legacy') or {}).get('nodes', {})})
    if source == 'boundary':
        checks = (evidence.get('boundary') or {}).get('checks') or {}
        if key not in checks:
            return STATUS_NOT_RUN, 'boundary check was not produced'
        return (STATUS_PASS if checks[key] else STATUS_FAIL), f'boundary check {key}'
    if source in ('shell', 'package'):
        checks = (evidence.get(source) or {}).get('checks') or {}
        return _check_status(checks, key)
    if source == 'browser':
        tests = (evidence.get('browser') or {}).get('tests') or {}
        outcome = tests.get(key)
        if outcome is None:
            return STATUS_NOT_RUN, 'spec title was not collected'
        if outcome == 'failed':
            return STATUS_FAIL, 'the named spec failed'
        if outcome == 'skipped':
            return STATUS_NOT_RUN, 'the named spec was skipped'
        return STATUS_PASS, 'the named spec passed'
    if source == 'docs':
        docs = evidence.get('docs') or {}
        if key == 'human_testing_wording':
            problems = list(docs.get('human_wording') or [])
        elif key == 'no_internal_paths_or_private_addresses':
            problems = list(docs.get('internal_paths') or []) + list(docs.get('private_addresses') or [])
        else:
            problems = list(docs.get('internal_paths') or [])
        return (STATUS_PASS if not problems else STATUS_FAIL), key
    if source == 'windows_preparation':
        preparation = evidence.get('windows_preparation') or {}
        if preparation.get('status') == STATUS_PASS:
            return STATUS_PASS, f'{key} (preparation only; not a run; fresh source digest)'
        return preparation.get('status', STATUS_NOT_RUN), \
            preparation.get('reason') or '; '.join(preparation.get('problems') or [])
    if source == 'windows_runtime':
        runtime = evidence.get('windows_runtime') or {}
        cases = runtime.get('cases') or {}
        status = cases.get(key)
        if status == STATUS_PASS:
            return STATUS_PASS, 'the real Windows x64 run passed this case'
        if status == STATUS_FAIL:
            return STATUS_FAIL, 'the real Windows x64 run failed this case'
        return STATUS_NOT_RUN, runtime.get('reason') or 'real Windows x64 runtime evidence missing (R11W)'
    return STATUS_FAIL, f'unknown selector source {source}'


def requirement_matrix(evidence):
    """Per-clause coverage matrix.

    Each subclause resolves on its own selectors, so an unrelated green test can
    never cover a missing recovery-code, cleanup or checkpoint clause. The
    requirement carries a strict ``status`` (external device clauses count as
    NOT_RUN) and a ``local_status`` that only reflects the selectors this
    machine can execute.
    """
    matrix = {}
    for requirement, (clause_text, clauses) in SUBREQUIREMENTS.items():
        clause_entries = []
        statuses = []
        local_statuses = []
        for label, selectors in clauses:
            entries = []
            clause_statuses = []
            clause_local = []
            for selector in selectors:
                external = selector.startswith(EXTERNAL_SELECTOR_PREFIXES)
                status, detail = resolve_selector(selector, evidence)
                entries.append({'selector': selector, 'status': status, 'detail': detail, 'external': external})
                clause_statuses.append(status)
                if not external:
                    clause_local.append(status)
            if STATUS_FAIL in clause_statuses:
                clause_status = STATUS_FAIL
            elif all(status == STATUS_PASS for status in clause_statuses):
                clause_status = STATUS_PASS
            else:
                clause_status = STATUS_NOT_RUN
            if clause_local:
                if STATUS_FAIL in clause_local:
                    clause_local_status = STATUS_FAIL
                elif all(status == STATUS_PASS for status in clause_local):
                    clause_local_status = STATUS_PASS
                else:
                    clause_local_status = STATUS_NOT_RUN
            else:
                clause_local_status = STATUS_NOT_RUN
            clause_entries.append({'clause': label, 'status': clause_status,
                                   'local_status': clause_local_status, 'evidence': entries})
            statuses.append(clause_status)
            if clause_local:
                local_statuses.append(clause_local_status)
        if STATUS_FAIL in statuses:
            overall = STATUS_FAIL
        elif all(status == STATUS_PASS for status in statuses):
            overall = STATUS_PASS
        else:
            overall = STATUS_NOT_RUN
        if STATUS_FAIL in local_statuses:
            local = STATUS_FAIL
        elif local_statuses and all(status == STATUS_PASS for status in local_statuses):
            local = STATUS_PASS
        else:
            local = STATUS_NOT_RUN
        matrix[requirement] = {'clause': clause_text, 'status': overall, 'local_status': local,
                               'clauses': clause_entries}
    return matrix


def overall_status(matrix, steps, step_errors=None, require_windows=False):
    """Local gate verdict; ``require_windows`` adds the real device run."""
    errors = step_errors or {}
    local_failures = [name for name, status in steps.items() if status == STATUS_FAIL]
    if errors:
        local_failures.extend(errors)
    failed_requirements = sorted(key for key, entry in matrix.items() if entry['local_status'] == STATUS_FAIL)
    missing_local = sorted(key for key, entry in matrix.items() if entry['local_status'] == STATUS_NOT_RUN)
    external_pending = sorted(key for key, entry in matrix.items() if entry['status'] == STATUS_NOT_RUN)
    if require_windows:
        if steps.get('windows_runtime') != STATUS_PASS:
            return STATUS_FAIL, ('the real Windows x64 runtime is not PASS; the aggregate gate never '
                                 'substitutes the local gate: ' + str(steps.get('windows_runtime')))
        not_passed = sorted(key for key, entry in matrix.items() if entry['status'] != STATUS_PASS)
        if local_failures or not_passed:
            return STATUS_FAIL, 'local or device failures: ' + ', '.join(sorted(set(local_failures + not_passed)))
        return STATUS_PASS, 'every local and real Windows x64 requirement is backed by fresh evidence'
    if local_failures or failed_requirements:
        reason = 'local failures: ' + ', '.join(sorted(set(local_failures + failed_requirements)))
        return STATUS_FAIL, reason
    if missing_local:
        return STATUS_FAIL, 'local requirement evidence is missing: ' + ', '.join(missing_local)
    return STATUS_PASS, ('every local requirement is backed by freshly executed evidence'
                         + (f'; external classes pending: {", ".join(external_pending)}' if external_pending else ''))


def run_windows_runtime(evidence_root, windows_kit, windows_run, preparation=None):
    """The real Windows x64 evidence: fetched run or the Mac SSH orchestration.

    Neither branch trusts a summary, a boolean or six case strings: the fetched
    evidence is re-read from its raw files (report format, kit/program/source
    identity, the raw harness document, device facts and every WN01-WN06 case
    with its bound artifacts) and the run must be bound to the same fresh
    preparation the local gate just verified. A hash-only ``PASS`` summary is an
    explicit failure, never a substituted device result.
    """
    result = {'status': STATUS_NOT_RUN, 'cases': {}, 'reason': None, 'report': None}
    preparation = preparation or {}
    import remediation_windows

    expected_digest = remediation_windows.program_source_digest()
    expected_inputs = remediation_windows.program_source_inputs()
    if preparation.get('status') != STATUS_PASS or not preparation.get('program_sha256'):
        result.update({'status': STATUS_FAIL,
                       'reason': 'the real Windows x64 run needs a fresh preparation bound to the current '
                                 'program sources; without it no run evidence is accepted'})
        return result
    expected_program = preparation.get('program_sha256')
    preparation_kit = preparation.get('kit_root')
    if windows_run:
        path = Path(windows_run).expanduser().resolve()
        problems = remediation_windows.validate_windows_run_evidence(
            path, expected_source_digest=expected_digest, expected_program_sha256=expected_program,
            kit_root=preparation_kit, expected_source_inputs=expected_inputs)
        report = None
        candidate = path / 'verify_report.json'
        if candidate.is_file():
            try:
                report = read_json(candidate)
            except (OSError, ValueError):
                report = None
        cases = {}
        if isinstance(report, dict) and isinstance(report.get('cases'), dict):
            cases = {key: value for key, value in report['cases'].items()
                     if key in remediation_windows.WINDOWS_CASES}
        if problems:
            result.update({'status': STATUS_FAIL, 'cases': cases, 'report': str(path),
                           'reason': 'the selected Windows evidence is not complete and bound: '
                                     + '; '.join(problems[:4]),
                           'problems': problems})
            return result
        result.update({'status': STATUS_PASS, 'cases': cases, 'report': str(path),
                       'reason': 'complete real Windows x64 run evidence re-read and bound to the fresh '
                                 'preparation'})
        return result
    if windows_kit:
        target = evidence_root / 'windows-runtime'
        try:
            code, report = remediation_windows.verify_kit(windows_kit, evidence_root=target)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            result.update({'status': STATUS_FAIL,
                           'reason': f'the Windows orchestration failed: {type(error).__name__}: {error}'})
            return result
        cases = report.get('cases') if isinstance(report, dict) else {}
        cases = cases if isinstance(cases, dict) else {}
        if code == 0 and report.get('windows_verified'):
            # Re-read the produced evidence exactly like a fetched run: the
            # orchestration's own verdict is never the only evidence.
            problems = remediation_windows.validate_windows_run_evidence(
                target, expected_source_digest=expected_digest, expected_program_sha256=expected_program,
                kit_root=windows_kit, expected_source_inputs=expected_inputs)
            if problems:
                result.update({'status': STATUS_FAIL, 'cases': cases,
                               'report': str(target / 'verify_report.json'),
                               'reason': 'the orchestrated Windows evidence failed the independent re-read: '
                                         + '; '.join(problems[:4]),
                               'problems': problems})
                return result
            result.update({'status': STATUS_PASS, 'cases': cases,
                           'report': str(target / 'verify_report.json'),
                           'reason': 'real Windows x64 run via the Mac orchestration, re-read and bound'})
        elif code == 2:
            result.update({'status': STATUS_NOT_RUN, 'cases': cases,
                           'report': str(target / 'verify_report.json'),
                           'reason': str(report.get('reason') or 'the Windows orchestration was refused')})
        else:
            result.update({'status': STATUS_FAIL, 'cases': cases,
                           'report': str(target / 'verify_report.json'),
                           'reason': ', '.join(report.get('problems') or ['the Windows run failed'])})
        return result
    result['reason'] = ('no real Windows x64 run was selected: pass --windows-run (fetched evidence) or '
                        '--windows-kit (Mac SSH orchestration) / GEP_WINDOWS_RUN_DIR / GEP_WINDOWS_KIT_DIR')
    return result


def verify_local(evidence_root=None, require_windows=False, windows_kit=None, windows_run=None):
    root = guard_evidence_root(evidence_root) if evidence_root else new_unique_root(EVIDENCE_BASE)
    print(f'evidence root: {root}', flush=True)
    env = {**os.environ, 'GEP_T17_BROWSER': '1'}

    def runner(command, log, timeout, env=None):
        log = Path(log)
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('w', encoding='utf-8') as stream:
            stream.write('$ ' + ' '.join(command) + '\n')
            stream.flush()
            result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                    text=True, timeout=timeout, env=env or {**os.environ})
        return result

    steps = {}
    errors = {}
    evidence = {}
    artifacts = None
    # This round's bound artifacts: brand-new Web/macOS bytes under one unique
    # build root, never the historical build/native defaults. A failed build or
    # an invalid/stale binding refuses the shell, package and browser steps
    # before any of them starts.
    try:
        build_root = new_unique_root(BUILD_BASE)
        binding_document = build_current_artifacts(build_root, runner)
        import remediation_windows
        resolved = validate_artifact_binding(binding_document, remediation_windows.program_source_digest(),
                                             expected_source_inputs=remediation_windows.program_source_inputs())
        resolved.update({'binding_path': str(build_root / 'artifact_binding.json'),
                         'binding': binding_document, 'build_root': str(build_root)})
        write_json(root / 'artifact_binding.json',
                   {'binding': binding_document, 'resolved': {key: resolved[key] for key in BINDING_ARTIFACTS}})
        evidence['artifacts'] = {'status': STATUS_PASS, 'build_root': str(build_root),
                                 'binding': resolved['binding'], 'binding_path': resolved['binding_path'],
                                 'resolved': {key: resolved[key] for key in BINDING_ARTIFACTS}}
        artifacts = resolved
    except (GateError, OSError, subprocess.SubprocessError, ImportError) as error:
        evidence['artifacts'] = {'status': STATUS_FAIL, 'error': f'{type(error).__name__}: {error}'}
    steps['artifacts'] = evidence['artifacts']['status']
    print(f"artifacts step: {steps['artifacts']}"
          + (f" :: {evidence['artifacts'].get('error')}" if steps['artifacts'] == STATUS_FAIL else ''), flush=True)

    try:
        evidence['boundary'] = run_boundary(runner, root)
    except (GateError, OSError, subprocess.SubprocessError) as error:
        evidence['boundary'] = {'status': STATUS_FAIL, 'error': f'{type(error).__name__}: {error}'}
    steps['boundary'] = evidence['boundary']['status']
    print(f"boundary step: {steps['boundary']}", flush=True)

    for name, script, timeout in (('shell', SHELL_VERIFIER, 3600),
                                  ('package', PACKAGE_VERIFIER, 3600)):
        if artifacts is None:
            evidence[name] = {'status': STATUS_FAIL, 'checks': {},
                              'error': 'this round\'s artifact binding is missing or invalid'}
        else:
            try:
                evidence[name] = run_verifier_tool(runner, root, name, script, timeout, artifacts=artifacts)
            except (GateError, OSError, subprocess.SubprocessError) as error:
                evidence[name] = {'status': STATUS_FAIL, 'checks': {},
                                  'error': f'{type(error).__name__}: {error}'}
        steps[name] = evidence[name]['status']
        failed = evidence[name].get('failed_checks') or []
        print(f"{name} step: {steps[name]}"
              + (f" ({len(evidence[name].get('checks') or {})} checks, failed: {', '.join(failed[:3])})"
                 if failed else ''), flush=True)

    try:
        if artifacts is None:
            evidence['browser'] = {'status': STATUS_FAIL, 'tests': {},
                                   'error': 'this round\'s artifact binding is missing or invalid'}
        else:
            evidence['browser'] = run_browser(runner, root, artifacts)
    except (GateError, OSError, subprocess.SubprocessError, ImportError) as error:
        evidence['browser'] = {'status': STATUS_FAIL, 'tests': {},
                               'error': f'{type(error).__name__}: {error}'}
    steps['browser'] = evidence['browser']['status']
    print(f"browser step: {steps['browser']}", flush=True)

    for name, targets, timeout, forbid_skips in (('remediation', [REMEDIATION_SUITE], 7200, True),
                                                 ('legacy', list(LEGACY_SUITES), 7200, False)):
        try:
            evidence[name] = run_pytest(runner, root, name, targets, env, timeout, forbid_skips=forbid_skips)
            steps[name] = evidence[name]['status']
            counts = evidence[name]['counts']
            print(f"{name} step: {steps[name]} ({counts['passed']} passed, {counts['failed']} failed, "
                  f"{counts['error']} errors, {counts['skipped']} skipped)", flush=True)
        except (GateError, OSError, subprocess.SubprocessError) as error:
            evidence[name] = {'status': STATUS_FAIL, 'nodes': {}, 'counts': {},
                              'error': f'{type(error).__name__}: {error}'}
            steps[name] = STATUS_FAIL
            errors[name] = str(error)
            print(f'{name} step: FAIL :: {error}', flush=True)

    evidence['docs'] = run_docs_check(root)
    steps['docs'] = evidence['docs']['status']
    print(f"docs step: {steps['docs']}", flush=True)

    try:
        evidence['windows_preparation'] = windows_preparation()
    except (ImportError, OSError, ValueError) as error:
        evidence['windows_preparation'] = {'status': STATUS_FAIL,
                                           'reason': f'{type(error).__name__}: {error}'}
    print(f"windows preparation: {evidence['windows_preparation']['status']}", flush=True)

    if require_windows:
        try:
            evidence['windows_runtime'] = run_windows_runtime(root, windows_kit, windows_run,
                                                             preparation=evidence.get('windows_preparation'))
        except (ImportError, OSError, ValueError) as error:
            evidence['windows_runtime'] = {'status': STATUS_FAIL, 'cases': {},
                                           'reason': f'{type(error).__name__}: {error}'}
        steps['windows_runtime'] = evidence['windows_runtime']['status']
        print(f"windows runtime: {steps['windows_runtime']} "
              f":: {evidence['windows_runtime'].get('reason')}", flush=True)
    else:
        evidence['windows_runtime'] = {'status': STATUS_NOT_RUN, 'cases': {},
                                       'reason': 'external Windows x64 runtime; R11W owns the real run'}
        steps['windows_runtime'] = STATUS_NOT_RUN

    missing_new = [node for node in REQUIRED_NEW_TESTS
                   if selector_outcome('pytest:' + node, evidence['remediation']['nodes'])[0] != STATUS_PASS]
    if missing_new:
        steps['new_suite'] = STATUS_FAIL
        errors['new_suite'] = 'required new R11A cases are missing or not passing: ' + ', '.join(missing_new)
        print(f'new_suite step: FAIL :: {errors["new_suite"]}', flush=True)
    else:
        steps['new_suite'] = STATUS_PASS
        print('new_suite step: PASS', flush=True)

    matrix = requirement_matrix(evidence)
    verdict, reason = overall_status(matrix, steps, errors, require_windows=require_windows)
    external_pending = sorted(key for key, entry in matrix.items() if entry['status'] == STATUS_NOT_RUN)
    exit_code = 0 if verdict == STATUS_PASS else 1
    document = {'task': 'p03r11a-verify' if require_windows else 'p03r11a-verify-local',
                'verdict': verdict, 'reason': reason,
                'exit': exit_code, 'evidence_root': str(root),
                'steps': steps, 'step_errors': errors,
                'matrix': matrix,
                'external_pending': external_pending,
                'counts': {name: evidence[name].get('counts') for name in ('remediation', 'legacy')},
                'artifacts': evidence['artifacts'],
                'boundary_checks': evidence['boundary'].get('checks'),
                'docs': evidence['docs'],
                'windows_preparation': evidence['windows_preparation'],
                'windows_runtime': evidence['windows_runtime'],
                'windows_verified': evidence['windows_runtime'].get('status') == STATUS_PASS,
                'human_testing': 'NOT_RUN: the human round is recorded by people, never by this tool',
                'run_at': datetime.now(timezone.utc).isoformat()}
    write_json(root / 'gate_summary.json', document)
    write_json(root / 'coverage_matrix.json', matrix)
    evidence_index = {name: {'status': entry.get('status'), 'counts': entry.get('counts'),
                             'log': entry.get('log'), 'junit': entry.get('junit'),
                             'evidence_root': entry.get('evidence_root')}
                      for name, entry in evidence.items() if isinstance(entry, dict)}
    write_json(root / 'evidence_index.json', evidence_index)
    print(json.dumps({'verdict': verdict, 'reason': reason, 'exit': exit_code,
                      'evidence_root': str(root),
                      'failed_requirements': sorted(key for key, entry in matrix.items()
                                                    if entry['status'] == STATUS_FAIL),
                      'not_run_requirements': sorted(key for key, entry in matrix.items()
                                                     if entry['status'] == STATUS_NOT_RUN),
                      'windows_verified': evidence['windows_runtime'].get('status') == STATUS_PASS},
                     ensure_ascii=False, indent=2))
    return exit_code, document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-local', action='store_true',
                        help='run the complete local acceptance gate (required unless --verify)')
    parser.add_argument('--verify', action='store_true',
                        help='aggregate gate: the local gate plus a real Windows x64 run (R11W)')
    parser.add_argument('--windows-kit', default=None,
                        help='prepared kit for the Mac SSH orchestration (--verify)')
    parser.add_argument('--windows-run', default=None,
                        help='already fetched real Windows run evidence root (--verify)')
    parser.add_argument('--evidence-root', default=None,
                        help='brand-new unique evidence root inside the project; defaults to the dedicated base')
    args = parser.parse_args(argv)
    if args.verify and args.verify_local:
        parser.error('choose exactly one of --verify-local or --verify')
    if not args.verify and not args.verify_local:
        parser.error('--verify-local or --verify is required')
    try:
        if args.verify:
            code, _ = verify_local(
                args.evidence_root, require_windows=True,
                windows_kit=args.windows_kit or os.environ.get('GEP_WINDOWS_KIT_DIR'),
                windows_run=args.windows_run or os.environ.get('GEP_WINDOWS_RUN_DIR'))
        else:
            code, _ = verify_local(args.evidence_root)
    except GateError as error:
        print(f'GATE REFUSED :: {error}', file=sys.stderr, flush=True)
        print(json.dumps({'verdict': 'refused', 'error': str(error)}, ensure_ascii=False))
        return 2
    return code


if __name__ == '__main__':
    sys.exit(main())
