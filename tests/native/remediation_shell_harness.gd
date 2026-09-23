extends SceneTree
## P03R09C harness: the real native backend (real SQLite transactions and real
## HTTP against the synthetic fault server, tests/remediation/delivery_fault_app.py)
## driven through the real reusable GEC shell.
##
## Phases (GEP_SHELL_PHASE):
##   main           - admission through the real shell retires the entry form
##                    (no front controls during the stimulus); the persisted
##                    summary drives the failure surface after the first and the
##                    1st/2nd/3rd real retry failures (same state string, new
##                    announcement); the legal failure export has no secrets and
##                    keeps the queue; a shell retry that succeeds hides the
##                    surface; a real SQLite save failure never claims a saved
##                    finish; the permanent study_deleted terminal stops sending
##                    and keeps the legal export. One unfinished session with
##                    three persisted failures is left for reopen_pending.
##   reopen_cleaned - a fresh process over the same store: the received tombstone
##                    is announced as uploaded and never shows the failure
##                    surface or old answers.
##   reopen_pending - a fresh process over the same store: the unfinished session
##                    keeps its persisted retry count and shows the retry/export
##                    surface after the data-only continuation.
##   export_prepare - seeds a real session with a large synthetic payload and
##                    exports it once (unlimited process) for the R09CR
##                    restricted-write comparison; prints the session id, the
##                    document size and the durable queue hash.
##   export_limited - over the same real store, exports to an existing target
##                    under an external file-size limit: the export must report
##                    an error, keep the original target bytes and leave no
##                    temporary file. The pytest parent imposes the real limit.
##   export_recover - over the same real store after the failed export, exports
##                    again: the document and the durable queue must be unchanged.
##   export_gui     - the real FileDialog callback (its file_selected/canceled
##                    signals) and the automation entry share one atomic write:
##                    saved/cancelled/failed feedback, target protection and an
##                    untouched queue.
##   counting       - the persisted retry_failures is displayed exactly: the
##                    initial send failure has no retry count, a validated ACK
##                    reset clears the display, and counted failures show 1/2/3.
##   receipt_surface - a stored completion receipt or a front-locked session with
##                    stale failure counters offers no failure export.
##
## Environment: GEP_SYNTHETIC_STORAGE, GEP_DELIVERY_FAULT_URL, GEP_SHELL_PHASE,
## GEP_SHELL_INSTANCE, GEP_SHELL_STUDY, GEP_SHELL_BUILD, plus the export-phase
## GEP_SHELL_EXPORT_TARGET / GEP_SHELL_EXPORT_PAYLOAD / GEP_SHELL_SESSION.

const NativeBackend = preload("res://addons/gec/native_backend.gd")
const Shell = preload("res://addons/gec/shell.gd")
const CODE_CLEANED := "synthetic-cleaned"
const CODE_DELETED := "synthetic-deleted"
const CODE_PENDING := "synthetic-pending"

var failures: Array = []
var fault_url := ""
var storage_root := ""


func check(condition: bool, label: String) -> void:
	if not condition:
		failures.append(label)


func _initialize() -> void:
	call_deferred("run")


func random_id() -> String:
	return Crypto.new().generate_random_bytes(16).hex_encode()


func make_config() -> Dictionary:
	return {"config_version": "1", "protocol_version": "gep/1", "purpose": "synthetic", "mode": "id",
			"api_url": fault_url, "instance_id": OS.get_environment("GEP_SHELL_INSTANCE"),
			"study_id": OS.get_environment("GEP_SHELL_STUDY"), "release_id": "release-1",
			"build_id": OS.get_environment("GEP_SHELL_BUILD")}


func fault_post(path: String, body: Dictionary) -> Dictionary:
	var request = HTTPRequest.new()
	request.timeout = 10.0
	root.add_child(request)
	var error = request.request(fault_url + path, PackedStringArray(["Content-Type: application/json"]),
			HTTPClient.METHOD_POST, JSON.stringify(body))
	if error != OK:
		request.queue_free()
		return {}
	var result = await request.request_completed
	request.queue_free()
	if result[0] != HTTPRequest.RESULT_SUCCESS or int(result[1]) != 200:
		return {}
	var parsed = JSON.parse_string(result[3].get_string_from_utf8())
	return parsed if parsed is Dictionary else {}


func fault_state() -> Dictionary:
	var request = HTTPRequest.new()
	request.timeout = 10.0
	root.add_child(request)
	var error = request.request(fault_url + "/control/state")
	if error != OK:
		request.queue_free()
		return {}
	var result = await request.request_completed
	request.queue_free()
	if result[0] != HTTPRequest.RESULT_SUCCESS or int(result[1]) != 200:
		return {}
	var parsed = JSON.parse_string(result[3].get_string_from_utf8())
	return parsed if parsed is Dictionary else {}


func script(sessions: Dictionary) -> void:
	var result = await fault_post("/control/script", {"sessions": sessions})
	check(str(result.get("state", "")) == "scripted", "fault script accepted: " + JSON.stringify(result))


func batches_for(session_id: String) -> int:
	return int((await fault_state()).get("sessions", {}).get(session_id, {}).get("batch_requests", -1))


func new_shell() -> Array:
	## Real backend on the real store plus the real shell; the periodic uploader
	## is stopped so every round below is an explicit, observable flush.
	OS.set_environment("GEP_SYNTHETIC_STORAGE", storage_root)
	var backend = NativeBackend.new()
	root.add_child(backend)
	backend.config = make_config()
	var shell = Shell.new()
	root.add_child(shell)
	var prepared: Dictionary = shell.setup(backend)
	check(not prepared.has("error"), "the shell prepared against the real backend: " + JSON.stringify(prepared))
	if backend.timer != null:
		backend.timer.stop()
	return [backend, shell]


func settle() -> void:
	## The shell refreshes from the persisted summary on its 0.5s timer.
	await create_timer(0.9).timeout


func snapshot(shell) -> Dictionary:
	return shell.status_snapshot()


func delivery_of(s: Dictionary) -> Dictionary:
	var delivery = s.get("delivery")
	return delivery if delivery is Dictionary else {}


func failure_entries(count: int) -> Array:
	var entries: Array = []
	for _i in range(count):
		entries.append({"kind": "fail", "status": 503, "code": "unavailable", "retryable": true})
	return entries


func admit(shell, code: String) -> Dictionary:
	var result: Dictionary = await shell.auto_submit({"code": code})
	check(str(result.get("state", "")) == "active", "admission with " + code + " became active: " + JSON.stringify(result))
	return result


func record_two(backend) -> Array:
	var first: Dictionary = await backend.record("exp.rt", {"trial_id": "t1", "choice": "left", "rt_ms": 321.5}, {"id": "rt", "version": "1"})
	var second: Dictionary = await backend.record("exp.interaction", {"action": "revise", "confidence": null}, {"id": "interaction", "version": "1"})
	return [first, second]


func record_and_finish(backend) -> Array:
	var recorded := await record_two(backend)
	var committed: Dictionary = await backend.commit({"version": 1, "strategy": "trial_boundary_v1", "next_trial": 1,
			"dependencies": [recorded[0].event_id, recorded[1].event_id]})
	check(str(committed.get("state", "")) == "local_committed", "records committed with a trial checkpoint: " + JSON.stringify(committed))
	var finished: Dictionary = await backend.finish()
	check(str(finished.get("state", "")) == "local_committed", "finish declared the completion set: " + JSON.stringify(finished))
	return recorded


func make_due(backend, id: String) -> void:
	## The persisted retry schedule is real; a scenario that wants the next real
	## retry round advances the stored retry_at to now (test manipulation only).
	var s = backend.read_session(id)
	if not s.is_empty() and s.get("kind") == "session":
		s.retry_at = 0
		backend.save(s)


func force_flush(backend, id: String) -> void:
	await make_due(backend, id)
	await backend.flush()


func _exec_raw_sql(path: String, sql: String) -> bool:
	var raw = ClassDB.instantiate("SQLite")
	raw.path = path
	if not raw.open_db():
		return false
	var ok = raw.query(sql)
	raw.close_db()
	return ok


func run_main() -> void:
	var pair := await new_shell()
	var backend = pair[0]
	var shell = pair[1]
	await admit(shell, CODE_CLEANED)
	var session_id: String = backend.session_id
	check(not session_id.is_empty(), "the admitted session has a real id")

	# ---- successful admission retires every front control -------------------
	var snap := snapshot(shell)
	check(bool(snap.get("entry_locked", false)), "the entry form is retired after admission")
	check(not bool(snap.get("failure_surface", false)), "no failure surface before any failure")
	for control in [shell.code, shell.password, shell.short_code, shell.recovery, shell.permit, shell.start,
			shell.recover_button, shell.permit_button, shell.advanced_toggle]:
		check(control == null or not control.visible, "a retired front control stays hidden during the stimulus")
	check(shell.confirm_box == null or not shell.confirm_box.visible, "the confirmation box is hidden during the stimulus")
	check(shell.get_viewport() == null or shell.get_viewport().gui_get_focus_owner() == null,
			"no front control holds the keyboard focus during the stimulus")

	# ---- the science records and a durable finish ---------------------------
	await record_and_finish(backend)

	# ---- the first normal failure and three real retry failures -------------
	await script({session_id: {"events": failure_entries(4)}})
	await backend.flush()
	await settle()
	var s = backend.read_session(session_id)
	var delivery := delivery_of(s)
	check(bool(delivery.get("initial_failed", false)) and int(delivery.get("retry_failures", 0)) == 0,
			"the first normal failure only set initial_failed: " + JSON.stringify(delivery))
	snap = snapshot(shell)
	check(str(snap.get("state", "")) == "active", "the shell state stays active while retrying: " + JSON.stringify(snap.get("state", "")))
	check(str(snap.get("last_announcement", "")) == shell.t("retry"), "the first send failure announces the generic retry state without a retry count: " + str(snap.get("last_announcement", "")))
	check(not bool(snap.get("failure_surface", false)), "the failure surface is not shown before three retry failures")

	for expected in [1, 2, 3]:
		await force_flush(backend, session_id)
		await settle()
		s = backend.read_session(session_id)
		check(int(delivery_of(s).get("retry_failures", 0)) == expected, "retry failure " + str(expected) + " is persisted: " + JSON.stringify(delivery_of(s)))
		snap = snapshot(shell)
		check(str(snap.get("state", "")) == "active", "the state string is still active at retry " + str(expected))
		if expected < 3:
			check(not bool(snap.get("failure_surface", false)), "retry " + str(expected) + " keeps the retry-in-progress surface")
			check(str(snap.get("last_announcement", "")) == shell.t("retrying", {"n": expected}), "the same-state refresh announced the persisted count at retry " + str(expected) + ": " + str(snap.get("last_announcement", "")))
		else:
			check(bool(snap.get("failure_surface", false)), "the third retry failure shows the failure surface")
			check(bool(shell.retry_button.visible) and bool(shell.failure_export_button.visible), "both failure actions are visible at the third failure")
			check(str(snap.get("last_announcement", "")) == shell.t("retry_ready", {"n": 3}), "the failure announcement carries the persisted count: " + str(snap.get("last_announcement", "")))

	# ---- the legal failure export carries no secrets ------------------------
	var export_path := storage_root.path_join("failure-export.json")
	var exported: Dictionary = await shell.export_failure_data(export_path)
	check(str(exported.get("state", "")) == "exported", "the failure export wrote a real file: " + JSON.stringify(exported))
	var text := FileAccess.get_file_as_string(export_path)
	check(text.contains("exp.rt") and text.contains("exp.interaction"), "the export contains the original records")
	check(not text.contains("proof") and not text.contains("token") and not text.contains("password"), "the export contains no credentials")
	check(text.contains(session_id), "the export is bound to the real session")
	check(backend.read_session(session_id).pending.size() == 2, "the export never deletes or rewrites the local queue")

	# ---- a shell retry that succeeds hides the surface ----------------------
	await script({session_id: {"events": [{"kind": "ack"}]}})
	await make_due(backend, session_id)
	await shell._request_retry()
	await settle()
	check(str(snapshot(shell).get("state", "")) != "busy", "the shell is not left busy after the retry")
	check(backend.read_session(session_id).get("kind") == "cleaned", "the received session is a cleaned tombstone")
	snap = snapshot(shell)
	check(str(snap.get("state", "")) == "uploaded", "the shell announces the uploaded state: " + JSON.stringify(snap.get("state", "")))
	check(not bool(snap.get("failure_surface", false)), "the received tombstone hides the failure surface")
	check(not bool(shell.retry_button.visible) and not bool(shell.failure_export_button.visible), "the failure actions are hidden after receipt")
	check(str(snap.get("summary", {}).get("kind", "")) == "cleaned", "the summary is rebuilt from the persisted tombstone")
	backend.queue_free()
	shell.queue_free()
	await process_frame

	# ---- a real local save failure never claims a saved finish --------------
	pair = await new_shell()
	backend = pair[0]
	shell = pair[1]
	await admit(shell, CODE_DELETED)
	session_id = backend.session_id
	var queue_path: String = storage_root.path_join("queue.sqlite")
	check(_exec_raw_sql(queue_path, "CREATE TRIGGER IF NOT EXISTS block_shell_finish BEFORE UPDATE ON sessions BEGIN SELECT RAISE(ABORT,'synthetic_shell_finish'); END"),
			"the real save-failure trigger is installed")
	await record_two(backend)
	var failed_finish: Dictionary = await backend.finish()
	check(failed_finish.has("error"), "the blocked finish reports a real local save failure: " + JSON.stringify(failed_finish))
	shell.announce_finished(failed_finish)
	check(not bool(shell.finished_announced), "a failed finish is never announced as saved")
	check(shell.last_announcement == shell.t("storage_error"), "the failed finish announces the local save failure: " + shell.last_announcement)
	check(_exec_raw_sql(queue_path, "DROP TRIGGER IF EXISTS block_shell_finish"), "the save-failure trigger is removed")
	var finished: Dictionary = await backend.finish()
	check(str(finished.get("state", "")) == "local_committed", "the real finish succeeded after the injection was removed: " + JSON.stringify(finished))

	# ---- the permanent deletion terminal stops sending ----------------------
	await script({session_id: {"events": [{"kind": "fail", "status": 403, "code": "study_deleted", "retryable": false}]}})
	await backend.flush()
	await settle()
	s = backend.read_session(session_id)
	delivery = delivery_of(s)
	check(str(delivery.get("terminal_reason", "")) == "study_deleted", "study_deleted is a persisted terminal: " + JSON.stringify(delivery))
	check(bool(s.get("paused", false)), "the deletion terminal is safely paused")
	check(s.get("complete_ack") == null and s.get("kind") == "session" and s.pending.size() == 2 and s.records.size() == 2,
			"no fake receipt: the raw records and pending stay local: " + JSON.stringify([s.get("kind"), s.get("complete_ack"), s.pending.size(), s.records.size()]))
	snap = snapshot(shell)
	check(bool(snap.get("failure_surface", false)), "the deletion terminal keeps the legal export surface")
	check(bool(shell.retry_button.disabled), "the deletion terminal disables the retry action")
	var batches_before := await batches_for(session_id)
	await shell._request_retry()
	await settle()
	check(await batches_for(session_id) == batches_before, "the deletion terminal sends no further request")
	var deleted_export := storage_root.path_join("deleted-export.json")
	var deleted_result: Dictionary = await shell.export_failure_data(deleted_export)
	check(str(deleted_result.get("state", "")) == "exported", "the deletion terminal still allows the legal export")
	var deleted_text := FileAccess.get_file_as_string(deleted_export)
	check(deleted_text.contains("exp.rt") and not deleted_text.contains("proof") and not deleted_text.contains("token"),
			"the deletion export keeps the records and no credentials")
	print("P03R09C_DELETED ", session_id)
	backend.queue_free()
	shell.queue_free()
	await process_frame

	# ---- leave one unfinished session with three persisted failures ---------
	pair = await new_shell()
	backend = pair[0]
	shell = pair[1]
	await admit(shell, CODE_PENDING)
	session_id = backend.session_id
	await record_and_finish(backend)
	await script({session_id: {"events": failure_entries(4)}})
	await backend.flush()
	for _i in range(3):
		await force_flush(backend, session_id)
	await settle()
	s = backend.read_session(session_id)
	check(int(delivery_of(s).get("retry_failures", 0)) == 3, "the pending session keeps three persisted failures: " + JSON.stringify(delivery_of(s)))
	check(s.get("kind") == "session" and s.get("complete_ack") == null, "the pending session stays unacknowledged")
	print("P03R09C_PENDING ", session_id)


func run_reopen_cleaned() -> void:
	var pair := await new_shell()
	var shell = pair[1]
	var result: Dictionary = await shell.auto_submit({"code": CODE_CLEANED})
	# A cleaned tombstone is announced as already uploaded and the shell then
	# falls back to a fresh admission (existing product behavior); the point here
	# is the presentation: no failure surface and no old answers.
	check(str(result.get("state", "")) == "active", "the cleaned fallback admission is active: " + JSON.stringify(result))
	await settle()
	var snap := snapshot(shell)
	check(str(snap.get("last_announcement", "")) == shell.t("cleaned"), "the reopened tombstone is announced as already uploaded: " + str(snap.get("last_announcement", "")))
	check(not bool(snap.get("failure_surface", false)), "the reopened tombstone never shows the failure surface")
	check(int(snap.get("summary", {}).get("records", 0)) == 0, "the new admission shows no old answers")


func run_reopen_pending() -> void:
	var pair := await new_shell()
	var shell = pair[1]
	var result: Dictionary = await shell.auto_submit({"code": CODE_PENDING})
	check(str(result.get("state", "")) == "data_only", "the declared completion continues as data only: " + JSON.stringify(result))
	await settle()
	var snap := snapshot(shell)
	check(str(snap.get("summary", {}).get("kind", "")) == "session", "the reopened summary is the real session: " + JSON.stringify(snap.get("summary", {})))
	check(int(snap.get("summary", {}).get("retry_failures", 0)) == 3, "the reopened summary keeps the persisted failures")
	check(int(snap.get("summary", {}).get("pending", 0)) == 2, "the reopened summary keeps the unconfirmed pending records")
	check(bool(snap.get("failure_surface", false)), "the reopened unfinished session shows the retry/export surface")
	check(str(snap.get("last_announcement", "")) == shell.t("retry_ready", {"n": 3}), "the reopened session announces the persisted failure count: " + str(snap.get("last_announcement", "")))
	check(not str(snap.get("last_announcement", "")).contains(shell.t("uploaded")), "an unfinished session is never announced as uploaded")
	check(not str(snap.get("last_announcement", "")).contains(shell.t("cleaned")), "an unfinished session is never announced as already uploaded")


func delivery_defaults() -> Dictionary:
	return {"initial_failed": false, "retry_failures": 0, "round_id": 0, "last_error": "", "last_error_kind": "",
			"ack_progress": 0, "terminal_reason": "", "inflight": false}


func make_export_row(id: String, payload_size: int) -> Dictionary:
	## A real session row with one large synthetic payload, no credentials
	## outside the private fields the export must never copy.
	var record = {"protocol_version": "gep/1", "event_id": random_id(), "session_id": id, "segment_id": random_id(),
			"sequence": 1, "event_type": "exp.rt", "schema_id": "rt", "schema_version": "1",
			"payload": {"synthetic": "x".repeat(payload_size)}}
	var row := {"id": id, "kind": "session", "config": make_config(),
			"context": {"token": "SYNTHETIC-TOKEN-MUST-NOT-BE-EXPORTED"},
			"proof": "SYNTHETIC-PROOF-MUST-NOT-BE-EXPORTED", "records": [record], "pending": [record.event_id],
			"segments": [record.segment_id],
			"checkpoint": {"version": 1, "strategy": "trial_boundary_v1", "next_trial": 1, "dependencies": []},
			"completion": null, "complete_ack": null, "front_locked": false, "attempts": 0, "retry_at": 0,
			"paused": false, "delivery_version": 2, "delivery": delivery_defaults()}
	return row


func make_surface_row(id: String, front_locked: bool, complete_ack: bool, failures: int) -> Dictionary:
	## A durable row that already carries stale failure counters, so the failure
	## surface is decided by the persisted receipt/lock state, not by a counter
	## that happens to read zero.
	var first = {"protocol_version": "gep/1", "event_id": random_id(), "session_id": id, "segment_id": random_id(),
			"sequence": 1, "event_type": "exp.rt", "schema_id": "rt", "schema_version": "1", "payload": {"choice": "left"}}
	var second = {"protocol_version": "gep/1", "event_id": random_id(), "session_id": id, "segment_id": random_id(),
			"sequence": 2, "event_type": "exp.rt", "schema_id": "rt", "schema_version": "1", "payload": {"choice": "right"}}
	var delivery := delivery_defaults()
	delivery.initial_failed = true
	delivery.retry_failures = failures
	delivery.last_error = "http_503"
	delivery.last_error_kind = "http"
	var row := {"id": id, "kind": "session", "config": make_config(), "context": {"token": random_id()},
			"proof": random_id(), "records": [first, second], "pending": [first.event_id, second.event_id],
			"segments": [first.segment_id, second.segment_id], "checkpoint": null,
			"completion": {"event_ids": [first.event_id, second.event_id], "segment_ids": [first.segment_id, second.segment_id]},
			"complete_ack": null, "front_locked": front_locked, "attempts": 4, "retry_at": 0, "paused": false,
			"delivery_version": 2, "delivery": delivery}
	if complete_ack:
		row.complete_ack = {"protocol_version": "gep/1", "state": "complete", "instance_id": make_config()["instance_id"],
				"session_id": id, "missing": [], "declaration": row.completion}
		row.pending = []
	return row


func run_export_prepare() -> void:
	var pair := await new_shell()
	var backend = pair[0]
	var shell = pair[1]
	var payload := OS.get_environment("GEP_SHELL_EXPORT_PAYLOAD")
	var payload_size := int(payload) if payload.is_valid_int() else 16384
	var session_id := random_id()
	check(backend.save(make_export_row(session_id, payload_size)), "the large-payload session is committed to the real store")
	backend.session_id = session_id
	var target := OS.get_environment("GEP_SHELL_EXPORT_TARGET")
	var result: Dictionary = await shell.export_failure_data(target)
	check(str(result.get("state", "")) == "exported", "the unlimited export succeeded: " + JSON.stringify(result))
	var text := FileAccess.get_file_as_string(target)
	check(text.length() > payload_size, "the exported document carries the whole payload: " + str(text.length()))
	check(not text.contains("SYNTHETIC-TOKEN-MUST-NOT-BE-EXPORTED")
			and not text.contains("SYNTHETIC-PROOF-MUST-NOT-BE-EXPORTED"), "the export carries no credential")
	print("P03R09C_EXPORT_PREPARED ", JSON.stringify({"session_id": session_id, "bytes": text.length(),
			"queue_sha": JSON.stringify(backend.read_session(session_id)).sha256_text(), "payload": payload_size}))


func run_export_limited() -> void:
	var pair := await new_shell()
	var backend = pair[0]
	var shell = pair[1]
	backend.session_id = OS.get_environment("GEP_SHELL_SESSION")
	var target := OS.get_environment("GEP_SHELL_EXPORT_TARGET")
	var before := FileAccess.get_file_as_string(target)
	var result: Dictionary = await shell.export_failure_data(target)
	var after := FileAccess.get_file_as_string(target)
	var leftovers: Array = []
	var directory := DirAccess.open(target.get_base_dir())
	if directory != null:
		for name in directory.get_files():
			if name.contains(".part-"):
				leftovers.append(name)
	check(result.has("error") and str(result.get("state", "")) != "exported",
			"a restricted write is never reported as exported: " + JSON.stringify(result))
	check(before == after, "the restricted write kept the previous target bytes")
	check(leftovers.is_empty(), "the failed export left no temporary file: " + JSON.stringify(leftovers))
	print("P03R09C_EXPORT_RESULT ", JSON.stringify({"result": result, "target_unchanged": before == after,
			"leftovers": leftovers, "queue_sha": JSON.stringify(backend.read_session(backend.session_id)).sha256_text()}))


func run_export_recover() -> void:
	var pair := await new_shell()
	var backend = pair[0]
	var shell = pair[1]
	backend.session_id = OS.get_environment("GEP_SHELL_SESSION")
	var target := OS.get_environment("GEP_SHELL_EXPORT_TARGET")
	var result: Dictionary = await shell.export_failure_data(target)
	check(str(result.get("state", "")) == "exported", "the export works again after the restricted failure: " + JSON.stringify(result))
	var text := FileAccess.get_file_as_string(target)
	print("P03R09C_EXPORT_RECOVERED ", JSON.stringify({"session_id": backend.session_id, "bytes": text.length(),
			"queue_sha": JSON.stringify(backend.read_session(backend.session_id)).sha256_text()}))


func find_dialog(node) -> FileDialog:
	for child in node.get_children():
		if child is FileDialog:
			return child
	return null


func run_export_gui() -> void:
	var pair := await new_shell()
	var backend = pair[0]
	var shell = pair[1]
	var session_id := random_id()
	check(backend.save(make_export_row(session_id, 2048)), "the GUI export session is committed to the real store")
	backend.session_id = session_id
	var queue_before := JSON.stringify(backend.read_session(session_id))
	var target := storage_root.path_join("gui-export.json")
	var direct_path := storage_root.path_join("automation-export.json")

	shell.export_recovery()
	await process_frame
	var dialog := find_dialog(shell)
	check(dialog != null, "the GUI export created its real save dialog")
	if dialog == null:
		return
	dialog.file_selected.emit(target)
	await create_timer(0.3).timeout
	check(str(shell.last_announcement) == shell.t("export_saved", {"path": target}),
			"the GUI save announces the written path: " + str(shell.last_announcement))
	check(FileAccess.file_exists(target), "the GUI save really wrote the target")
	var gui_text := FileAccess.get_file_as_string(target)
	var direct: Dictionary = await shell.export_recovery_to(direct_path)
	check(str(direct.get("state", "")) == "exported", "the automation entry still exports: " + JSON.stringify(direct))
	check(FileAccess.get_file_as_string(direct_path) == gui_text, "the GUI and the automation entry wrote the same atomic document")
	check(not gui_text.contains("SYNTHETIC-TOKEN-MUST-NOT-BE-EXPORTED")
			and not gui_text.contains("SYNTHETIC-PROOF-MUST-NOT-BE-EXPORTED"), "the GUI export carries no credential")

	# A rename target that cannot be replaced: the temporary is removed, the
	# existing target (here a directory) stays untouched and the error is shown.
	var blocked := storage_root.path_join("blocked-target")
	DirAccess.make_dir_recursive_absolute(blocked)
	dialog.file_selected.emit(blocked)
	await create_timer(0.3).timeout
	check(str(shell.last_announcement) == shell.t("export_failed", {"code": "recovery_export_unavailable"}),
			"a failed GUI export shows the export error: " + str(shell.last_announcement))
	check(DirAccess.dir_exists_absolute(blocked), "the failed GUI export kept the existing target untouched")
	var leftovers: Array = []
	var directory := DirAccess.open(storage_root)
	if directory != null:
		for name in directory.get_files():
			if name.contains(".part-"):
				leftovers.append(name)
	check(leftovers.is_empty(), "the failed GUI export left no temporary file: " + JSON.stringify(leftovers))

	# Cancelling the dialog writes nothing and keeps the target and the queue.
	var target_before := FileAccess.get_file_as_string(target)
	dialog.canceled.emit()
	await create_timer(0.2).timeout
	check(str(shell.last_announcement) == shell.t("export_cancelled"),
			"cancelling the GUI export says so: " + str(shell.last_announcement))
	check(FileAccess.get_file_as_string(target) == target_before, "a cancelled export keeps the previous target bytes")
	check(JSON.stringify(backend.read_session(session_id)) == queue_before, "no export path touched the local queue or credentials")
	print("P03R09C_EXPORT_GUI ", session_id)


func run_counting() -> void:
	## The display always matches the persisted counters: the initial counted
	## failure has no retry count, a real ACK progress resets the counter so the
	## next counted failure is exactly 1, and 2/3 follow exactly.
	var pair := await new_shell()
	var backend = pair[0]
	var shell = pair[1]
	await admit(shell, "synthetic-counting")
	var session_id: String = backend.session_id
	await record_and_finish(backend)

	# One real batch failure, then a real batch ACK (progress) followed by a
	# refused completion in the same round: the failure after that progress is
	# retry number 1, never 2 and never the historical initial failure.
	await script({session_id: {"events": [{"kind": "fail", "status": 503, "code": "unavailable", "retryable": true}, {"kind": "ack"}], "completion": failure_entries(4)}})
	await force_flush(backend, session_id)
	await settle()
	var s = backend.read_session(session_id)
	check(int(delivery_of(s).get("retry_failures", 0)) == 0 and bool(delivery_of(s).get("initial_failed", false)),
			"the initial counted failure only set initial_failed: " + JSON.stringify(delivery_of(s)))
	check(str(shell.last_announcement) == shell.t("retry"),
			"the initial counted failure shows no invented retry count: " + str(shell.last_announcement))
	check(not bool(snapshot(shell).get("failure_surface", false)), "the initial failure shows no failure surface")

	await force_flush(backend, session_id)
	await settle()
	s = backend.read_session(session_id)
	check(int(delivery_of(s).get("ack_progress", 0)) == 1, "the batch ACK really progressed: " + JSON.stringify(delivery_of(s)))
	check(s.pending.is_empty(), "the ACK really reduced the pending queue")
	check(int(delivery_of(s).get("retry_failures", 0)) == 0,
			"a round that really progressed is never counted as a retry: " + JSON.stringify(delivery_of(s)))
	for expected in [1, 2, 3]:
		await force_flush(backend, session_id)
		await settle()
		s = backend.read_session(session_id)
		check(int(delivery_of(s).get("retry_failures", 0)) == expected,
				"the persisted count after the progress is exactly " + str(expected) + ": " + JSON.stringify(delivery_of(s)))
		if expected < 3:
			check(str(shell.last_announcement) == shell.t("retrying", {"n": expected}),
					"the display shows exactly the persisted " + str(expected) + ": " + str(shell.last_announcement))
		else:
			check(str(shell.last_announcement) == shell.t("retry_ready", {"n": 3}),
					"the third counted failure shows exactly 3: " + str(shell.last_announcement))
			check(bool(snapshot(shell).get("failure_surface", false)), "the third counted failure shows the failure surface")
	print("P03R09C_COUNTING ", session_id)


func run_receipt_surface() -> void:
	# Case 1: a stored completion receipt with stale retry failures is received:
	# no failure surface, no retry and no failure export.
	var pair := await new_shell()
	var backend = pair[0]
	var shell = pair[1]
	var receipt_id := random_id()
	check(backend.save(make_surface_row(receipt_id, false, true, 3)), "the stored-receipt row is committed to the real store")
	backend.session_id = receipt_id
	await settle()
	var snap := snapshot(shell)
	check(bool(snap.get("summary", {}).get("complete_ack", false)), "the summary reports the stored receipt: " + JSON.stringify(snap.get("summary", {})))
	check(str(snap.get("state", "")) == "uploaded", "the stored receipt is announced as uploaded: " + str(snap.get("state", "")))
	check(not bool(snap.get("failure_surface", false)), "a stored receipt hides the failure surface even with stale failures")
	check(shell.retry_button == null or not shell.retry_button.visible, "no retry entry with a stored receipt")
	check(shell.failure_export_button == null or not shell.failure_export_button.visible, "no failure export entry with a stored receipt")
	backend.queue_free()
	shell.queue_free()
	await process_frame

	# Case 2: a front-locked session with the same stale failures offers no export.
	pair = await new_shell()
	backend = pair[0]
	shell = pair[1]
	var locked_id := random_id()
	check(backend.save(make_surface_row(locked_id, true, false, 3)), "the front-locked row is committed to the real store")
	backend.session_id = locked_id
	await settle()
	snap = snapshot(shell)
	check(bool(snap.get("summary", {}).get("front_locked", false)), "the summary reports the front lock: " + JSON.stringify(snap.get("summary", {})))
	check(not bool(snap.get("failure_surface", false)), "a front-locked session offers no failure export even with stale failures")
	check(shell.retry_button == null or not shell.retry_button.visible, "no retry entry for a front-locked session")
	check(shell.failure_export_button == null or not shell.failure_export_button.visible, "no failure export entry for a front-locked session")
	print("P03R09C_RECEIPT_SURFACE ", locked_id)


func live_config() -> Dictionary:
	return {"config_version": "1", "protocol_version": "gep/1", "purpose": "synthetic", "mode": "password",
			"api_url": OS.get_environment("GEP_SHELL_LIVE_URL"), "instance_id": OS.get_environment("GEP_SHELL_INSTANCE"),
			"study_id": OS.get_environment("GEP_SHELL_STUDY"), "release_id": OS.get_environment("GEP_SHELL_RELEASE"),
			"build_id": OS.get_environment("GEP_SHELL_BUILD")}


func seed_live_session(storage: String, session_id: String, token: String, proof: String, cfg: Dictionary) -> bool:
	## The durable client queue for a session that already exists on the real
	## server: the real token/proof and one declared record, exactly what a
	## participant device holds after a real admission and finish.
	var raw = ClassDB.instantiate("SQLite")
	raw.path = storage.path_join("queue.sqlite")
	if not raw.open_db():
		return false
	if not raw.query("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,value TEXT NOT NULL)"):
		raw.close_db()
		return false
	var event = {"protocol_version": "gep/1", "event_id": OS.get_environment("GEP_SHELL_EVENT_ID"), "session_id": session_id,
			"segment_id": OS.get_environment("GEP_SHELL_SEGMENT_ID"), "sequence": 1, "event_type": "exp.rt", "schema_id": "rt", "schema_version": "1",
			"payload": {"rt_ms": 321.5, "choice": "left"}}
	var row = {"id": session_id, "kind": "session", "config": cfg, "context": {"token": token}, "proof": proof, "records": [event],
			"pending": [event.event_id], "segments": [event.segment_id], "checkpoint": null,
			"completion": {"event_ids": [event.event_id], "segment_ids": [event.segment_id]}, "complete_ack": null,
			"front_locked": false, "attempts": 0, "retry_at": 0, "paused": false, "delivery_version": 2,
			"delivery": {"initial_failed": false, "retry_failures": 0, "round_id": 0, "last_error": "", "last_error_kind": "",
				"ack_progress": 0, "terminal_reason": "", "inflight": false}}
	var ok = raw.query_with_bindings("INSERT INTO sessions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",
			[session_id, JSON.stringify(row)])
	raw.close_db()
	return ok


func run_live(phase: String) -> void:
	## The real isolated Django server (already marked deleted for the
	## ``live_deleted`` phase): the seeded row is the client's own durable queue,
	## so the terminal behavior is decided by the real server's refusal.
	var backend = NativeBackend.new()
	root.add_child(backend)
	backend.config = live_config()
	var shell = Shell.new()
	root.add_child(shell)
	var session_id := OS.get_environment("GEP_SHELL_SESSION")
	check(seed_live_session(storage_root, session_id, OS.get_environment("GEP_SHELL_TOKEN"), OS.get_environment("GEP_SHELL_PROOF"), backend.config),
			"the live session row was seeded")
	var prepared: Dictionary = shell.setup(backend)
	check(not prepared.has("error"), "the shell prepared against the live server: " + JSON.stringify(prepared))
	if backend.timer != null:
		backend.timer.stop()
	backend.session_id = session_id
	await backend.flush()
	await settle()
	var s = backend.read_session(session_id)
	if phase == "live_before":
		check(str(s.get("kind", "")) == "cleaned" and s.get("complete_ack") == null,
				"the live batch and completion were received and cleaned: " + JSON.stringify([s.get("kind"), s.get("state")]))
		var snap := snapshot(shell)
		check(str(snap.get("state", "")) == "uploaded", "the received live session is announced as uploaded: " + JSON.stringify(snap.get("state", "")))
	else:
		check(str(delivery_of(s).get("terminal_reason", "")) == "study_deleted", "the live deletion is a persisted terminal: " + JSON.stringify(delivery_of(s)))
		check(bool(s.get("paused", false)), "the live deletion terminal is safely paused")
		check(s.get("kind") == "session" and s.get("complete_ack") == null and s.pending.size() == 1 and s.records.size() == 1,
				"no fake receipt: the live records and pending stay local: " + JSON.stringify([s.get("kind"), s.get("complete_ack"), s.pending.size(), s.records.size()]))
		var snap := snapshot(shell)
		check(bool(snap.get("failure_surface", false)), "the live deletion keeps the legal export surface")
		check(bool(shell.retry_button.disabled), "the live deletion disables the retry action")
		var export_path := storage_root.path_join("live-deleted-export.json")
		var exported: Dictionary = await shell.export_failure_data(export_path)
		check(str(exported.get("state", "")) == "exported", "the live deletion still allows the legal export: " + JSON.stringify(exported))
		var text := FileAccess.get_file_as_string(export_path)
		check(text.contains(OS.get_environment("GEP_SHELL_EVENT_ID")) and not text.contains(OS.get_environment("GEP_SHELL_TOKEN")) and not text.contains("proof"),
				"the live export keeps the record and no credentials")
		await shell._request_retry()
		await settle()
	print("P03R09C_LIVE_", phase.to_upper(), " ", session_id)


func run() -> void:
	await process_frame
	fault_url = OS.get_environment("GEP_DELIVERY_FAULT_URL")
	storage_root = OS.get_environment("GEP_SYNTHETIC_STORAGE")
	check(not storage_root.is_empty(), "storage environment is set")
	check(not OS.get_environment("GEP_SHELL_INSTANCE").is_empty(), "instance environment is set")
	check(not OS.get_environment("GEP_SHELL_STUDY").is_empty(), "study environment is set")
	check(not OS.get_environment("GEP_SHELL_BUILD").is_empty(), "build environment is set")
	DirAccess.make_dir_recursive_absolute(storage_root)
	var phase := OS.get_environment("GEP_SHELL_PHASE")
	if phase == "reopen_cleaned":
		await run_reopen_cleaned()
	elif phase == "reopen_pending":
		await run_reopen_pending()
	elif phase == "live_before" or phase == "live_deleted":
		await run_live(phase)
	elif phase == "export_prepare":
		await run_export_prepare()
	elif phase == "export_limited":
		await run_export_limited()
	elif phase == "export_recover":
		await run_export_recover()
	elif phase == "export_gui":
		await run_export_gui()
	elif phase == "counting":
		await run_counting()
	elif phase == "receipt_surface":
		await run_receipt_surface()
	else:
		check(not fault_url.is_empty(), "fault url environment is set")
		await run_main()
	if failures.is_empty():
		print("P03R09C_SHELL_VERIFIED")
		quit(0)
	else:
		print("P03R09C_SHELL_FAILED ", JSON.stringify(failures))
		quit(1)
