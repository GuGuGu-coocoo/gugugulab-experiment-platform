extends SceneTree
## P03R09A first-milestone harness: the real GEC shell driven through its own
## buttons with the real independent local backend and real SQLite.
##
## It covers the local-test contract of this Task: the entry form is retired
## after admission, a finish is only saved when the record commit and the
## completion set are both durable, a real disk failure reports storage_error
## without losing the records, and the results JSONL is only written by an
## explicit save. It never touches a server, a network or the science task.
##
## Environment: GEP_SYNTHETIC_STORAGE (success store), GEP_SYNTHETIC_RESULTS
## (result directory), GEP_SYNTHETIC_STORAGE_FAULT (fault store).
const Shell = preload("res://addons/gec/shell.gd")
const LocalBackend = preload("res://data/local_backend.gd")

var failures: Array = []


func check(condition: bool, label: String) -> void:
	if not condition:
		failures.append(label)


func _initialize() -> void:
	call_deferred("run")


func build_shell(backend) -> Node:
	var shell = Shell.new()
	root.add_child(shell)
	var setup_result: Dictionary = shell.setup(backend)
	check(not setup_result.has("error"), "shell setup: " + JSON.stringify(setup_result))
	if shell._refresh != null:
		shell._refresh.stop()
	return shell


func normalize(value):
	## JSON.parse_string returns every number as a float; the stored records keep
	## integral values as ints. Compare values, not number classes.
	if value is float and is_equal_approx(value, round(value)):
		return int(value)
	if value is Array:
		return value.map(normalize)
	if value is Dictionary:
		var out := {}
		for key in value:
			out[key] = normalize(value[key])
		return out
	return value


func run() -> void:
	await process_frame
	var results_dir := OS.get_environment("GEP_SYNTHETIC_RESULTS")
	var storage_fault := OS.get_environment("GEP_SYNTHETIC_STORAGE_FAULT")
	check(not results_dir.is_empty(), "results directory environment is set")
	check(not storage_fault.is_empty(), "fault storage environment is set")

	# ---- local test: admission retires the entry form ----------------------
	var backend = LocalBackend.new()
	root.add_child(backend)
	var shell = build_shell(backend)
	check(shell.local_test_mode, "the local backend is recognized as a local test")
	check(shell.start != null and shell.start.visible and not shell.start.disabled, "entry form visible before admission")
	check(shell.save_results_button != null and shell.save_results_button.visible, "explicit results save button is present")
	check(shell.open_results_button != null and not shell.open_results_button.visible, "results folder button hidden before a save")

	var admitted: Dictionary = await shell.auto_submit({})
	check(str(admitted.get("state", "")) == "active", "admission became active: " + JSON.stringify(admitted))
	check(shell.state == "active", "shell is active after admission")
	check(shell.start != null and not shell.start.visible and shell.start.disabled, "start button retired after admission")
	check(shell.code != null and not shell.code.visible and not shell.code.editable, "ID field hidden and read-only")
	check(shell.password != null and not shell.password.visible and not shell.password.editable, "password field hidden and read-only")
	check(shell.hint != null and not shell.hint.visible, "entry hint hidden")
	check(shell.message != null and shell.message.visible, "status line stays visible")
	check(shell.get_viewport().gui_get_focus_owner() == null, "no entry control keeps focus after admission")
	check(shell.start != null and shell.start.pressed.get_connections().is_empty(), "the start callback is removed after admission")
	check(shell.shell_box != null and shell.shell_box.mouse_filter == Control.MOUSE_FILTER_IGNORE, "shell box no longer intercepts pointer input")
	check(shell.shell_box != null and shell.shell_box.size.y < 200.0, "shell box shrank so it cannot cover the stimulus: " + str(shell.shell_box.size))
	var session_before: String = backend.session_id
	var retrigger: Dictionary = await shell.submit_entry()
	check(retrigger.get("error", "") == "already_started", "entry cannot be re-triggered: " + JSON.stringify(retrigger))
	check(backend.session_id == session_before, "re-trigger created no second local session")
	check(backend.current.get("state", "") == "active", "backend session stays active after the refused re-trigger")

	# ---- finish saves commit + completion, never a remote acknowledgement ---
	var first: Dictionary = await backend.record("exp.rt", {"trial_id": "t1", "choice": "left", "rt_ms": 321.5, "response_status": "responded"}, {"id": "rt", "version": "1"}, {"value": 321.5, "unit": "ms", "clock_id": "host_monotonic", "epoch": "harness", "source": "Godot Time.get_ticks_usec"})
	var second: Dictionary = await backend.record("exp.interaction", {"action": "revise", "selection": ["shape_a", "shape_c"], "confidence": null, "nested": {"changes": [{"from": null, "to": "001"}], "confirmed": false}}, {"id": "interaction", "version": "1"})
	check(not first.has("error") and not second.has("error"), "local records buffered")
	var committed: Dictionary = await backend.commit({"version": 1, "strategy": "trial_boundary_v1", "next_trial": 1, "dependencies": [first.event_id, second.event_id]})
	check(str(committed.get("state", "")) == "local_committed", "checkpoint commit saved: " + JSON.stringify(committed))
	check(backend.current.get("state", "") == "active", "a checkpoint commit is not a finish")
	var finished: Dictionary = await backend.finish()
	check(str(finished.get("state", "")) == "finished_saved", "finish saved both commit and completion: " + JSON.stringify(finished))
	check(backend.current.get("state", "") == "finished_saved" and backend.current.get("remote_status", "") == "unsupported", "finished local state carries no remote status")
	shell.announce_finished(finished)
	check(shell.finished_announced, "saved finish is announced")
	check(shell.message.text.contains(shell.t("local_test_complete")), "local test complete is shown: " + shell.message.text)
	check(not shell.message.text.contains(shell.t("finished_local")), "no upload-waiting message in the local test")
	check(not shell.message.text.contains(shell.t("storage_error")), "no storage error on a saved finish")
	check(root.find_children("*", "HTTPRequest", true, false).is_empty(), "no HTTPRequest node exists in the local run")

	# ---- the results JSONL is written only by an explicit save --------------
	var export_target: String = results_dir.path_join("harness-results.jsonl")
	check(not FileAccess.file_exists(export_target), "no result file exists before the explicit save")
	shell.save_results_button.pressed.emit()
	await process_frame
	await process_frame
	check(shell.save_results_dialog != null, "explicit save opened the results dialog")
	check(shell.results_directory() == backend.results_directory(), "the dialog uses the explicit results directory")
	if shell.save_results_dialog != null:
		check(shell.save_results_dialog.current_dir == backend.results_directory(), "dialog starts in the results directory")
		shell.save_results_dialog.file_selected.emit(export_target)
		await process_frame
		await process_frame
	check(FileAccess.file_exists(export_target), "results JSONL written to the chosen path")
	check(shell.message.text.contains(export_target), "the exact saved path is shown: " + shell.message.text)
	check(shell.open_results_button != null and shell.open_results_button.visible, "open results folder offered after a save")
	var lines := FileAccess.get_file_as_string(export_target).strip_edges().split("\n")
	check(lines.size() == 2, "one JSONL line per record: " + str(lines.size()))
	var exported: Array = []
	for line in lines:
		exported.append(JSON.parse_string(line))
	var values_match: bool = exported.size() == backend.records.size()
	if values_match:
		for index in range(exported.size()):
			if JSON.stringify(normalize(exported[index])) != JSON.stringify(normalize(backend.records[index])):
				values_match = false
				check(false, "record " + str(index) + " differs: " + JSON.stringify(exported[index]) + " vs " + JSON.stringify(backend.records[index]))
	check(values_match, "JSONL keeps the original record values")

	# ---- cancelling the save keeps the records and writes nothing -----------
	var before_files := DirAccess.get_files_at(results_dir)
	shell.save_results_button.pressed.emit()
	await process_frame
	await process_frame
	if shell.save_results_dialog != null:
		shell.save_results_dialog.hide()
		shell.save_results_dialog.canceled.emit()
		await process_frame
	check(DirAccess.get_files_at(results_dir) == before_files, "a cancelled save wrote no file")
	check(backend.records.size() == 2 and backend.buffer.is_empty(), "a cancelled save kept the records")
	check(shell.message.text.contains(shell.t("results_cancelled")), "the cancelled save is announced: " + shell.message.text)

	# ---- a failed export is an error and keeps the records ------------------
	var failed_export: Dictionary = await shell.save_results_to("/p03r09a-no-such-directory/results.jsonl")
	check(failed_export.get("error", "") == "results_export_unavailable", "unwritable results path reports an error: " + JSON.stringify(failed_export))
	check(shell.message.text.contains(shell.t("export_failed", {"code": ""})), "the export failure is shown: " + shell.message.text)
	check(backend.records.size() == 2, "a failed export kept the records")

	# ---- same-state error updates, without disturbing a successful ACK ------
	backend.current = {"state": "active", "error": "http_500", "remote_status": "unsupported"}
	await shell._refresh_state()
	check(shell.message.text.contains("http_500"), "same-state error update is shown: " + shell.message.text)
	backend.current = {"state": "active", "error": "http_403", "remote_status": "unsupported"}
	await shell._refresh_state()
	check(shell.message.text.contains("http_403") and not shell.message.text.contains("http_500"), "changed error text refreshes while the state string stays active")
	backend.current = {"state": "remote_acknowledged", "remote_status": "unsupported"}
	await shell._refresh_state()
	check(shell.message.text == shell.t("uploaded"), "the successful acknowledgement is announced: " + shell.message.text)
	backend.current = {"state": "remote_acknowledged", "error": "http_500", "remote_status": "unsupported"}
	await shell._refresh_state()
	check(shell.message.text == shell.t("uploaded"), "a late error does not overwrite the uploaded acknowledgement")

	# ---- a real disk failure on finish: storage_error, records kept ---------
	var fault_backend = LocalBackend.new()
	root.add_child(fault_backend)
	OS.set_environment("GEP_SYNTHETIC_STORAGE", storage_fault)
	var fault_shell = build_shell(fault_backend)
	var fault_admitted: Dictionary = await fault_shell.auto_submit({})
	check(str(fault_admitted.get("state", "")) == "active", "fault-phase admission became active: " + JSON.stringify(fault_admitted))
	var fault_first: Dictionary = await fault_backend.record("exp.rt", {"trial_id": "t1", "choice": "left", "rt_ms": 321.5}, {"id": "rt", "version": "1"})
	var fault_second: Dictionary = await fault_backend.record("exp.interaction", {"action": "revise"}, {"id": "interaction", "version": "1"})
	var fault_commit: Dictionary = await fault_backend.commit({"version": 1, "strategy": "trial_boundary_v1", "next_trial": 1, "dependencies": [fault_first.get("event_id", ""), fault_second.get("event_id", "")]})
	check(str(fault_commit.get("state", "")) == "local_committed", "fault-phase trial commit saved")
	var read_only := OS.execute("chmod", ["0555", storage_fault])
	check(read_only == 0, "storage directory made read-only (real disk failure): " + str(read_only))
	var fault_finish: Dictionary = await fault_backend.finish()
	check(fault_finish.get("error", "") == "storage_error", "finish on an unwritable store reports storage_error: " + JSON.stringify(fault_finish))
	check(fault_backend.current.get("state", "") == "storage_error", "backend state is storage_error: " + JSON.stringify(fault_backend.current))
	check(fault_backend.records.size() == 2, "the failed finish kept both records")
	check(fault_backend.buffer.is_empty(), "the failed finish kept the committed buffer empty")
	fault_shell.announce_finished(fault_finish)
	check(not fault_shell.finished_announced, "a failed finish is never announced as finished")
	check(fault_shell.message.text.contains(fault_shell.t("storage_error")), "the storage error is shown: " + fault_shell.message.text)
	check(not fault_shell.message.text.contains(fault_shell.t("local_test_complete")), "a failed finish never claims the local test complete")
	var restored := OS.execute("chmod", ["0755", storage_fault])
	check(restored == 0, "storage directory restored: " + str(restored))
	var retried: Dictionary = await fault_backend.finish()
	check(str(retried.get("state", "")) == "finished_saved", "the retry saved the preserved records: " + JSON.stringify(retried))
	var fault_target: String = results_dir.path_join("harness-fault-results.jsonl")
	var fault_export: Dictionary = await fault_shell.save_results_to(fault_target)
	check(str(fault_export.get("state", "")) == "results_saved", "the preserved records export after the retry: " + JSON.stringify(fault_export))
	var fault_lines := FileAccess.get_file_as_string(fault_target).strip_edges().split("\n")
	check(fault_lines.size() == 2, "the retried export contains both records: " + str(fault_lines.size()))

	if failures.is_empty():
		print("P03R09A_HARNESS_VERIFIED")
		quit(0)
	else:
		print("P03R09A_HARNESS_FAILED ", JSON.stringify(failures))
		quit(1)
