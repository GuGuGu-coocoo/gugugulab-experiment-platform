extends SceneTree
## Headless shell contract harness: drives the reusable GEC shell through its
## real Control surface with a scripted backend. It verifies the frozen-mode
## field set, fail-closed capability handling, explicit continuation and the
## cleaned/front-locked/ambiguous boundaries without touching any server or
## participant store.

class FakeBackend:
	extends Node
	var config: Dictionary = {}
	var prepared: Array = []
	var confirmed: Array = []
	var prepared_result: Dictionary = {"state": "active", "checkpoint": null}
	var code_result: Dictionary = {"state": "confirm", "session_id": "local-1", "token": "t", "can_resume": true}
	var named_result: Dictionary = {"state": "none"}
	var recovered: Dictionary = {}
	func initialize_queue() -> Dictionary:
		return {"state": "ready"}
	func prepare(options: Dictionary = {}) -> Dictionary:
		prepared.append(options)
		return prepared_result
	func summary() -> Dictionary:
		return {"state": "active"}
	func recover_code(code: String) -> Dictionary:
		return code_result
	func recover_named(participant_code: String, password: String) -> Dictionary:
		recovered = {"participant_code": participant_code, "password": password}
		return named_result
	func confirm_recovery(session_id: String, token: String, can_resume: bool) -> Dictionary:
		confirmed.append({"session_id": session_id, "token": token, "can_resume": can_resume})
		if not can_resume: return {"state": "data_only", "checkpoint": null}
		return {"state": "active", "checkpoint": {"version": 1, "strategy": "trial_boundary_v1", "next_trial": 1, "dependencies": []}}
	func recovery_export() -> Dictionary:
		return {"format_version": 1, "records": []}
	func download_recovery() -> Dictionary:
		return {"state": "download_requested"}

const Shell = preload("res://addons/gec/shell.gd")
var failures: Array = []

func _initialize() -> void:
	call_deferred("run")

func check(condition: bool, label: String) -> void:
	if not condition:
		failures.append(label)

func build(backend: FakeBackend) -> Node:
	var shell = Shell.new()
	# The harness owns its scripted backend, so freeing a shell also frees it.
	shell.add_child(backend)
	root.add_child(shell)
	shell.setup(backend)
	return shell

func run() -> void:
	await process_frame
	# Frozen anonymous mode renders no ID/password fields.
	var anonymous := FakeBackend.new()
	anonymous.config = {"config_version": "1", "purpose": "synthetic", "mode": "anonymous", "shell_capability": "gec-shell/v1"}
	var shell = build(anonymous)
	check(shell.mode == "anonymous", "anonymous mode resolved")
	check(shell.code == null and shell.password == null, "anonymous hides ID and password")
	check(shell.short_code != null and shell.recovery != null and shell.permit != null, "recovery panel always available")
	check(shell.start != null and not shell.start.disabled, "anonymous start enabled")
	await shell.submit_entry()
	check(anonymous.prepared.size() == 1 and anonymous.prepared[0].expected_version == "synthetic-1", "legacy host default still prepares the synthetic build version")
	shell.queue_free()

	# Frozen roster modes render exactly the required fields.
	var roster := FakeBackend.new()
	roster.config = {"config_version": "1", "purpose": "synthetic", "mode": "id", "shell_capability": "gec-shell/v1"}
	var shell_id = build(roster)
	check(shell_id.code != null and shell_id.password == null, "id mode shows only the ID field")
	shell_id.queue_free()
	var roster_pw := FakeBackend.new()
	roster_pw.config = {"config_version": "1", "purpose": "synthetic", "mode": "password", "shell_capability": "gec-shell/v1"}
	var shell_pw = build(roster_pw)
	check(shell_pw.code != null and shell_pw.password != null, "password mode shows ID and password")
	shell_pw.queue_free()

	# Unknown capability or mode fails closed instead of guessing.
	var unknown_capability := FakeBackend.new()
	unknown_capability.config = {"config_version": "1", "purpose": "synthetic", "mode": "id", "shell_capability": "gec-shell/v99"}
	var shell_cap = build(unknown_capability)
	check(shell_cap.state == "unsupported" and shell_cap.start.disabled, "unknown capability fails closed")
	shell_cap.queue_free()
	var unknown_mode := FakeBackend.new()
	unknown_mode.config = {"config_version": "1", "purpose": "synthetic", "mode": "invite", "shell_capability": "gec-shell/v1"}
	var shell_mode = build(unknown_mode)
	check(shell_mode.state == "unsupported" and shell_mode.start.disabled, "unknown mode fails closed")
	shell_mode.queue_free()

	# Legacy configuration keeps the pre-shell field set.
	var legacy := FakeBackend.new()
	legacy.config = {"config_version": "1", "purpose": "synthetic"}
	var shell_legacy = build(legacy)
	check(shell_legacy.mode == "legacy" and shell_legacy.code != null and shell_legacy.password != null, "legacy config keeps both fields")
	shell_legacy.queue_free()

	# Six-digit recovery validates the server proof and asks before resuming.
	var code_backend := FakeBackend.new()
	code_backend.config = {"config_version": "1", "purpose": "synthetic", "mode": "id", "shell_capability": "gec-shell/v1"}
	var shell_code = build(code_backend)
	shell_code.short_code.text = "123456"
	var pending = await shell_code.submit_entry()
	check(pending.get("state", "") == "confirm" and shell_code.state == "confirm", "code recovery waits for explicit continue")
	check(code_backend.confirmed.is_empty(), "no session is resumed before confirmation")
	await shell_code.confirm_session()
	check(code_backend.confirmed == [{"session_id": "local-1", "token": "t", "can_resume": true}], "explicit continue adopts the redeemed credential")
	check(shell_code.state == "active", "continued session becomes active")
	shell_code.queue_free()

	# Cancelling a pending continuation adopts nothing.
	var cancel_backend := FakeBackend.new()
	cancel_backend.config = {"config_version": "1", "purpose": "synthetic", "mode": "id", "shell_capability": "gec-shell/v1"}
	var shell_cancel = build(cancel_backend)
	shell_cancel.short_code.text = "123456"
	await shell_cancel.submit_entry()
	shell_cancel.cancel_confirmation()
	check(cancel_backend.confirmed.is_empty() and shell_cancel.state == "ready", "cancel keeps the session unrecovered")
	shell_cancel.queue_free()

	# Named continuation: cleaned, front-locked and ambiguous all avoid silent resume.
	var cleaned_backend := FakeBackend.new()
	cleaned_backend.config = {"config_version": "1", "purpose": "synthetic", "mode": "password", "shell_capability": "gec-shell/v1"}
	cleaned_backend.named_result = {"state": "cleaned"}
	var shell_cleaned = build(cleaned_backend)
	shell_cleaned.code.text = "001"
	shell_cleaned.password.text = "synthetic-password"
	await shell_cleaned.submit_entry()
	check(cleaned_backend.confirmed.is_empty(), "cleaned candidate is not resumed")
	check(cleaned_backend.prepared.size() == 1, "cleaned candidate falls back to a new admission")
	shell_cleaned.queue_free()
	var locked_backend := FakeBackend.new()
	locked_backend.config = {"config_version": "1", "purpose": "synthetic", "mode": "password", "shell_capability": "gec-shell/v1"}
	locked_backend.named_result = {"state": "front_locked"}
	var shell_locked = build(locked_backend)
	shell_locked.code.text = "001"
	shell_locked.password.text = "synthetic-password"
	await shell_locked.submit_entry()
	check(locked_backend.prepared.is_empty() and locked_backend.confirmed.is_empty(), "front-locked candidate is refused without a new admission")
	shell_locked.queue_free()
	var ambiguous_backend := FakeBackend.new()
	ambiguous_backend.config = {"config_version": "1", "purpose": "synthetic", "mode": "id", "shell_capability": "gec-shell/v1"}
	ambiguous_backend.named_result = {"state": "ambiguous"}
	var shell_ambiguous = build(ambiguous_backend)
	shell_ambiguous.code.text = "001"
	await shell_ambiguous.submit_entry()
	check(ambiguous_backend.prepared.is_empty() and ambiguous_backend.confirmed.is_empty(), "ambiguous candidates never continue or disclose")
	shell_ambiguous.queue_free()

	# A declared completion is verified and then continued as data only.
	var data_only_backend := FakeBackend.new()
	data_only_backend.config = {"config_version": "1", "purpose": "synthetic", "mode": "password", "shell_capability": "gec-shell/v1"}
	data_only_backend.named_result = {"state": "confirm", "session_id": "local-2", "token": "t2", "can_resume": false, "task_finished": true}
	var shell_data_only = build(data_only_backend)
	shell_data_only.set_locale("en")
	shell_data_only.code.text = "001"
	shell_data_only.password.text = "synthetic-password"
	await shell_data_only.submit_entry()
	check(shell_data_only.message.text == shell_data_only.t("confirm_data_only"), "finished candidate asks for explicit data-only confirmation")
	check(data_only_backend.confirmed.is_empty(), "finished candidate is not adopted before confirmation")
	await shell_data_only.confirm_session()
	check(data_only_backend.confirmed == [{"session_id": "local-2", "token": "t2", "can_resume": false}], "data-only confirmation never resumes a trial")
	check(shell_data_only.state == "data_only", "confirmed finished candidate is data only")
	shell_data_only.queue_free()

	# Missing a task checkpoint also means data-only, even before completion.
	var interrupted_backend := FakeBackend.new()
	interrupted_backend.config = {"config_version": "1", "purpose": "synthetic", "mode": "id", "shell_capability": "gec-shell/v1"}
	interrupted_backend.named_result = {"state": "confirm", "session_id": "local-interrupted", "token": "it", "can_resume": false, "task_finished": false}
	var interrupted = build(interrupted_backend)
	var lifecycle: Array = []
	interrupted.session_started.connect(func(_checkpoint): lifecycle.append("task"))
	interrupted.session_data_only.connect(func(): lifecycle.append("data"))
	interrupted.code.text = "001"
	await interrupted.submit_entry()
	check(interrupted_backend.confirmed.is_empty(), "unfinished data-only candidate waits for confirmation")
	await interrupted.confirm_session()
	check(lifecycle == ["data"] and interrupted.state == "data_only", "unfinished no-checkpoint recovery emits only data lifecycle")
	check(interrupted_backend.confirmed == [{"session_id": "local-interrupted", "token": "it", "can_resume": false}], "unfinished data-only recovery retains its authorized identity")
	interrupted.queue_free()

	# No matching candidate: the shell must not claim an uploaded/cleaned session.
	var none_backend := FakeBackend.new()
	none_backend.config = {"config_version": "1", "purpose": "synthetic", "mode": "password", "shell_capability": "gec-shell/v1"}
	none_backend.named_result = {"state": "none"}
	var shell_none = build(none_backend)
	shell_none.set_locale("en")
	shell_none.code.text = "001"
	shell_none.password.text = "synthetic-password"
	await shell_none.submit_entry()
	check(none_backend.prepared.size() == 1, "no matching candidate still starts a normal admission")
	check(shell_none.message.text != shell_none.t("cleaned"), "no matching candidate is never announced as uploaded")
	shell_none.queue_free()

	# Finish announcement is localized and never re-announces a trial.
	var finish_backend := FakeBackend.new()
	finish_backend.config = {"config_version": "1", "purpose": "synthetic", "mode": "anonymous", "shell_capability": "gec-shell/v1"}
	var shell_finish = build(finish_backend)
	shell_finish.set_locale("zh")
	shell_finish.announce_finished({"state": "local_committed"})
	check(shell_finish.message.text.contains("已完成") and not shell_finish.message.text.contains("试次"), "finish announcement is localized and not a trial")
	shell_finish.set_locale("en")
	check(shell_finish.t("start") == "Start participation" and shell_finish.t("trial", {"n": 2}).contains("Trial 2"), "english locale strings")
	shell_finish.set_locale("zh")
	check(shell_finish.t("start") == "开始参与", "chinese locale strings")
	shell_finish.queue_free()

	# A host build version travels through actual entry and fresh-session paths.
	var version_backend := FakeBackend.new()
	version_backend.config = {"config_version": "1", "purpose": "synthetic", "mode": "password", "shell_capability": "gec-shell/v1"}
	var shell_version = build(version_backend)
	shell_version.program_version = "moss-duel-1"
	shell_version.code.text = "moss-001"
	shell_version.password.text = "synthetic-test-password"
	shell_version.code.grab_focus()
	await process_frame
	check(root.gui_get_focus_owner() == shell_version.code, "real native entry field can hold focus")
	var state_before: String = shell_version.state
	shell_version.set_overlay_visible(false)
	check(not shell_version.visible and not shell_version.shell_box.is_visible_in_tree(), "host hides the actual native Control tree")
	check(root.gui_get_focus_owner() != shell_version.code, "hiding native shell releases entry focus")
	check(not shell_version.entry_locked and shell_version.state == state_before and version_backend.prepared.is_empty(), "visibility does not admit or retire entry")
	shell_version.announce("Synthetic upload status")
	check(not shell_version.visible and not shell_version.overlay_visible, "native status updates do not reveal hidden overlay")
	shell_version.set_overlay_visible(true)
	check(shell_version.visible and shell_version.code.editable and not shell_version.entry_locked, "revealing unused entry keeps it available")
	await shell_version.submit_entry()
	check(version_backend.prepared.size() == 1 and version_backend.prepared[0].expected_version == "moss-duel-1", "custom program version is passed to real prepare")
	check(shell_version.entry_locked, "successful custom-version admission retires entry")
	shell_version.set_overlay_visible(false)
	check(shell_version.entry_locked and shell_version.state == "active", "hiding admitted overlay preserves entry retirement and state")
	shell_version.announce_finished({"state": "local_committed"})
	check(shell_version.visible and shell_version.overlay_visible and shell_version.finished_announced, "finish reveals the native shell again")
	check(shell_version.entry_locked and shell_version.start.disabled and not shell_version.code.visible, "finish never unlocks retired native entry")
	var stale_result: Dictionary = await shell_version.submit_entry()
	check(stale_result.get("error") == "already_started" and version_backend.prepared.size() == 1, "re-shown native shell rejects stale admission")
	shell_version.queue_free()

	var new_backend := FakeBackend.new()
	new_backend.config = {"config_version": "1", "purpose": "synthetic", "mode": "password", "shell_capability": "gec-shell/v1"}
	new_backend.named_result = {"state": "confirm", "session_id": "previous-session", "token": "synthetic-proof", "can_resume": true}
	var shell_new = build(new_backend)
	shell_new.program_version = "moss-duel-1"
	shell_new.code.text = "moss-002"
	shell_new.password.text = "synthetic-test-password"
	await shell_new.submit_entry()
	check(shell_new.state == "confirm" and new_backend.prepared.is_empty(), "named candidate awaits the fresh-session choice")
	await shell_new.confirm_new_session()
	check(new_backend.prepared.size() == 1 and new_backend.prepared[0].expected_version == "moss-duel-1" and new_backend.prepared[0].participant_code == "moss-002", "explicit new session carries the custom program version and issued ID")
	check(new_backend.confirmed.is_empty() and shell_new.entry_locked, "fresh-session choice never silently recovers the previous session")
	shell_new.queue_free()
	await process_frame
	await process_frame

	if failures.is_empty():
		print("SHELL_UNIT_VERIFIED")
		quit(0)
	else:
		print("SHELL_UNIT_FAILED ", JSON.stringify(failures))
		quit(1)
