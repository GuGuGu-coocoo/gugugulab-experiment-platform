extends SceneTree
## P03R09AR harness: the real GEC shell with the real independent local backend,
## real SQLite and real file IO.
##
## It covers the save/export boundaries of this Task: saving results never
## claims a finished local test by itself, the feedback and the open-folder
## target point at the actual final file parent (chosen or default), a real
## permission failure and a real unopenable target keep the old target and the
## local records, an injected post-open write failure also keeps the previous
## target (clearly separated from the real disk faults), and a data-only
## recovery retires the entry form exactly like a normal admission while a
## failed or cancelled confirmation does not lock it early.
##
## Environment: GEP_SYNTHETIC_STORAGE (success store), GEP_SYNTHETIC_RESULTS
## (result directory), GEP_SYNTHETIC_STORAGE_FAULT (fault store).
const Shell = preload("res://addons/gec/shell.gd")
const LocalBackend = preload("res://data/local_backend.gd")


class RecoveryBackend:
	extends Node
	## Scripted recovery contract for the shell: it never touches a server or a
	## store, so the harness can drive the explicit confirmation paths.
	var config: Dictionary = {"config_version": "1", "protocol_version": "gep/1", "purpose": "synthetic", "mode": "id", "shell_capability": "gec-shell/v1"}
	var can_resume := false
	var confirm_error := ""
	var confirmations: Array = []
	func initialize_queue() -> Dictionary:
		return {"state": "ready"}
	func summary() -> Dictionary:
		return {"state": "active" if can_resume else "data_only"}
	func recover_code(_code: String) -> Dictionary:
		return {"state": "confirm", "session_id": "recovery-1", "token": "synthetic-token", "can_resume": can_resume}
	func confirm_recovery(session_id: String, token: String, resume: bool) -> Dictionary:
		confirmations.append([session_id, token, resume])
		if not confirm_error.is_empty(): return {"error": confirm_error}
		if resume: return {"state": "active", "checkpoint": null}
		return {"state": "data_only"}
	func recovery_export() -> Dictionary:
		return {"format_version": 1, "records": []}


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


func jsonl_lines(path: String) -> Array:
	if not FileAccess.file_exists(path): return []
	var lines: Array = []
	for line in FileAccess.get_file_as_string(path).strip_edges().split("\n"):
		if not line.is_empty(): lines.append(JSON.parse_string(line))
	return lines


func records_match(path: String, expected: Array) -> bool:
	var lines := jsonl_lines(path)
	if lines.size() != expected.size(): return false
	for index in range(lines.size()):
		if JSON.stringify(normalize(lines[index])) != JSON.stringify(normalize(expected[index])): return false
	return true


func part_files(directory: String) -> Array:
	var out: Array = []
	for name in DirAccess.get_files_at(directory):
		if name.contains(".part-"): out.append(name)
	return out


func run() -> void:
	await process_frame
	var results_dir := OS.get_environment("GEP_SYNTHETIC_RESULTS")
	var storage_fault := OS.get_environment("GEP_SYNTHETIC_STORAGE_FAULT")
	check(not results_dir.is_empty(), "results directory environment is set")
	check(not storage_fault.is_empty(), "fault storage environment is set")

	# ---- saving results while the local test is still active ----------------
	var backend = LocalBackend.new()
	root.add_child(backend)
	var shell = build_shell(backend)
	var admitted: Dictionary = await shell.auto_submit({})
	check(str(admitted.get("state", "")) == "active", "admission became active: " + JSON.stringify(admitted))
	var first: Dictionary = await backend.record("exp.rt", {"trial_id": "t1", "choice": "left", "rt_ms": 321.5}, {"id": "rt", "version": "1"}, {"value": 321.5, "unit": "ms", "source": "harness"})
	var second: Dictionary = await backend.record("exp.interaction", {"action": "revise", "confidence": null}, {"id": "interaction", "version": "1"})
	check(not first.has("error") and not second.has("error"), "local records buffered")
	var committed: Dictionary = await backend.commit({"version": 1, "strategy": "trial_boundary_v1", "next_trial": 1, "dependencies": [first.event_id, second.event_id]})
	check(str(committed.get("state", "")) == "local_committed", "checkpoint commit saved: " + JSON.stringify(committed))

	var chosen := results_dir.path_join("chosen")
	check(DirAccess.make_dir_recursive_absolute(chosen) == OK, "chosen directory created")
	var chosen_target := chosen.path_join("partial.jsonl")
	var chosen_save: Dictionary = await shell.save_results_to(chosen_target)
	check(str(chosen_save.get("state", "")) == "results_saved", "chosen save succeeded: " + JSON.stringify(chosen_save))
	check(str(chosen_save.get("directory", "")) == chosen, "the reported directory is the actual file parent: " + str(chosen_save.get("directory")))
	check(shell.results_directory() == chosen, "results_directory points at the chosen directory after the save")
	check(shell.open_results_button != null and shell.open_results_button.visible, "open results folder offered after the save")
	check(shell.message.text.contains(chosen_target), "the saved path is announced: " + shell.message.text)
	check(not shell.message.text.contains(shell.t("local_test_complete")), "an active save never claims the local test complete: " + shell.message.text)
	check(shell.message.text == shell.t("results_saved_partial", {"path": chosen_target}), "the active save announces results without completion: " + shell.message.text)
	check(records_match(chosen_target, backend.records), "the saved JSONL keeps the record values")
	check(backend.current.get("state", "") == "active", "saving does not change the backend state")
	check(backend.records.size() == 2, "saving kept the local records")

	# ---- the default directory still works and reports itself ---------------
	var default_save: Dictionary = await shell.save_results_to("")
	check(str(default_save.get("state", "")) == "results_saved", "default save succeeded: " + JSON.stringify(default_save))
	check(str(default_save.get("directory", "")) == backend.results_directory(), "the default save reports the default directory")
	check(FileAccess.file_exists(str(default_save.get("path", ""))), "the default result file exists")
	check(records_match(str(default_save.get("path", "")), backend.records), "the default JSONL keeps the record values")
	check(not shell.message.text.contains(shell.t("local_test_complete")), "the default save does not claim completion either")
	check(shell.results_directory() == backend.results_directory(), "results_directory follows the default save")

	# ---- cancelling the dialog keeps records and writes nothing -------------
	var before_cancel := DirAccess.get_files_at(results_dir)
	shell.save_results_button.pressed.emit()
	await process_frame
	await process_frame
	if shell.save_results_dialog != null:
		shell.save_results_dialog.hide()
		shell.save_results_dialog.canceled.emit()
		await process_frame
	check(DirAccess.get_files_at(results_dir) == before_cancel, "a cancelled save wrote no file")
	check(shell.message.text.contains(shell.t("results_cancelled")), "the cancelled save is announced: " + shell.message.text)
	check(backend.records.size() == 2, "a cancelled save kept the records")

	# ---- real failures keep the old target and the records ------------------
	var missing_parent := results_dir.path_join("no-such-directory").path_join("results.jsonl")
	var open_failed: Dictionary = await shell.save_results_to(missing_parent)
	check(str(open_failed.get("error", "")) == "results_export_unavailable", "an unopenable target reports results_export_unavailable: " + JSON.stringify(open_failed))
	check(not FileAccess.file_exists(missing_parent), "the unopenable target was not created")
	check(backend.records.size() == 2, "an open failure kept the records")

	var readonly := results_dir.path_join("readonly")
	check(DirAccess.make_dir_recursive_absolute(readonly) == OK, "read-only test directory created")
	check(OS.execute("chmod", ["0555", readonly]) == 0, "the results directory was made read-only (real disk failure)")
	var blocked_target := readonly.path_join("blocked.jsonl")
	var permission_failed: Dictionary = await shell.save_results_to(blocked_target)
	check(str(permission_failed.get("error", "")) == "results_export_unavailable", "a real permission failure reports results_export_unavailable: " + JSON.stringify(permission_failed))
	check(OS.execute("chmod", ["0755", readonly]) == 0, "the results directory was restored")
	check(not FileAccess.file_exists(blocked_target), "the blocked target was not created")
	check(backend.records.size() == 2, "a permission failure kept the records")

	# ---- an injected post-open write failure preserves the old target -------
	# This one is an injected store failure, not a physical disk fault: the
	# point is that an open that succeeded can still fail afterwards and must
	# never be reported as a saved file or damage the previous target.
	var preserved := results_dir.path_join("preserved.jsonl")
	var old_file = FileAccess.open(preserved, FileAccess.WRITE)
	check(old_file != null, "the preserved target was pre-created")
	if old_file != null:
		old_file.store_string("OLD-CONTENT");old_file.close()
	backend.fault_injection = "store_failure"
	var injected: Dictionary = await shell.save_results_to(preserved)
	check(str(injected.get("error", "")) == "results_export_unavailable", "the injected write failure reports results_export_unavailable: " + JSON.stringify(injected))
	check(FileAccess.get_file_as_string(preserved) == "OLD-CONTENT", "the injected failure kept the old target content")
	check(part_files(results_dir).is_empty(), "the injected failure left no temporary file: " + JSON.stringify(part_files(results_dir)))
	check(backend.records.size() == 2, "the injected failure kept the records")
	backend.fault_injection = ""
	var recovered_save: Dictionary = await shell.save_results_to(preserved)
	check(str(recovered_save.get("state", "")) == "results_saved", "the same target saves once the injection is cleared: " + JSON.stringify(recovered_save))
	check(records_match(preserved, backend.records), "the replaced target holds the full JSONL")

	# ---- a storage_error state still only reports a saved result ------------
	backend.current = {"state": "storage_error", "error": "storage_error", "remote_status": "unsupported"}
	var error_target := results_dir.path_join("storage-error-results.jsonl")
	var error_save: Dictionary = await shell.save_results_to(error_target)
	check(str(error_save.get("state", "")) == "results_saved", "results still save during storage_error: " + JSON.stringify(error_save))
	check(shell.message.text.contains(error_target), "the storage_error save announces the path: " + shell.message.text)
	check(not shell.message.text.contains(shell.t("local_test_complete")), "a storage_error save never claims the local test complete: " + shell.message.text)
	check(backend.records.size() == 2, "the storage_error save kept the records")

	# ---- only a durable finish then allows the completion wording -----------
	backend.current = {"state": "active", "remote_status": "unsupported"}
	var finished: Dictionary = await backend.finish()
	check(str(finished.get("state", "")) == "finished_saved", "finish saved both writes: " + JSON.stringify(finished))
	shell.announce_finished(finished)
	check(shell.message.text.contains(shell.t("local_test_complete")), "the saved finish announces completion")
	var after_finish_target := results_dir.path_join("after-finish.jsonl")
	var after_finish: Dictionary = await shell.save_results_to(after_finish_target)
	check(str(after_finish.get("state", "")) == "results_saved", "results save after the finish: " + JSON.stringify(after_finish))
	check(shell.message.text == shell.t("results_saved", {"path": after_finish_target}), "after a durable finish the save may say complete: " + shell.message.text)

	# ---- a failed finish keeps the retired entry closed ---------------------
	var fault_backend = LocalBackend.new()
	root.add_child(fault_backend)
	OS.set_environment("GEP_SYNTHETIC_STORAGE", storage_fault)
	var fault_shell = build_shell(fault_backend)
	var fault_admitted: Dictionary = await fault_shell.auto_submit({})
	check(str(fault_admitted.get("state", "")) == "active", "fault admission became active: " + JSON.stringify(fault_admitted))
	var fault_first: Dictionary = await fault_backend.record("exp.rt", {"trial_id": "t1", "choice": "left", "rt_ms": 321.5}, {"id": "rt", "version": "1"})
	var fault_second: Dictionary = await fault_backend.record("exp.interaction", {"action": "revise"}, {"id": "interaction", "version": "1"})
	var fault_commit: Dictionary = await fault_backend.commit({"version": 1, "strategy": "trial_boundary_v1", "next_trial": 1, "dependencies": [fault_first.get("event_id", ""), fault_second.get("event_id", "")]})
	check(str(fault_commit.get("state", "")) == "local_committed", "fault commit saved")
	check(OS.execute("chmod", ["0555", storage_fault]) == 0, "fault store made read-only (real disk failure)")
	var fault_finish: Dictionary = await fault_backend.finish()
	check(str(fault_finish.get("error", "")) == "storage_error", "the failed finish reports storage_error: " + JSON.stringify(fault_finish))
	fault_shell.announce_finished(fault_finish)
	check(fault_shell.state == "error", "the shell is in error after the failed finish")
	check(not fault_shell.finished_announced, "a failed finish is never announced as finished")
	check(fault_backend.records.size() == 2, "the failed finish kept the records")
	var fault_session: String = fault_backend.session_id
	var retrigger: Dictionary = await fault_shell.submit_entry()
	check(str(retrigger.get("error", "")) == "already_started", "a retired entry refuses a stale submit after the failed finish: " + JSON.stringify(retrigger))
	check(fault_backend.session_id == fault_session, "the refused submit created no second session")
	check(OS.execute("chmod", ["0755", storage_fault]) == 0, "fault store restored")

	# ---- explicit recovery: data_only retires, failures/cancel do not -------
	var data_backend := RecoveryBackend.new()
	data_backend.can_resume = false
	root.add_child(data_backend)
	var data_shell = build_shell(data_backend)
	data_shell.set_locale("en")
	data_shell.short_code.text = "123456"
	var pending: Dictionary = await data_shell.submit_entry()
	check(str(pending.get("state", "")) == "confirm", "a code candidate waits for explicit confirmation")
	check(not data_shell.entry_locked and data_shell.start != null and data_shell.start.visible, "a pending confirmation does not retire the entry")
	await data_shell.confirm_session()
	check(data_backend.confirmations == [["recovery-1", "synthetic-token", false]], "the confirmation adopted the candidate as data only: " + JSON.stringify(data_backend.confirmations))
	check(data_shell.state == "data_only", "the shell is data only")
	check(data_shell.entry_locked and data_shell.start != null and not data_shell.start.visible and data_shell.start.disabled, "a data-only recovery retires the entry form")
	check(data_shell.message.text == data_shell.t("data_only"), "the data-only state is announced: " + data_shell.message.text)
	var data_retrigger: Dictionary = await data_shell.submit_entry()
	check(str(data_retrigger.get("error", "")) == "already_started", "the retired entry refuses another submit after data-only recovery")
	check(data_backend.confirmations.size() == 1, "the refused submit contacted no backend")

	var cancel_backend := RecoveryBackend.new()
	root.add_child(cancel_backend)
	var cancel_shell = build_shell(cancel_backend)
	cancel_shell.short_code.text = "123456"
	await cancel_shell.submit_entry()
	cancel_shell.cancel_confirmation()
	check(not cancel_shell.entry_locked and cancel_shell.start != null and cancel_shell.start.visible, "cancel does not lock the entry early")
	check(cancel_backend.confirmations.is_empty(), "cancel adopted nothing")

	var failed_backend := RecoveryBackend.new()
	failed_backend.confirm_error = "recovery_denied"
	root.add_child(failed_backend)
	var failed_shell = build_shell(failed_backend)
	failed_shell.short_code.text = "123456"
	await failed_shell.submit_entry()
	await failed_shell.confirm_session()
	check(failed_shell.state == "error", "a failed confirmation reports an error")
	check(not failed_shell.entry_locked and failed_shell.start != null and failed_shell.start.visible, "a failed confirmation does not lock the entry early")

	var continue_backend := RecoveryBackend.new()
	continue_backend.can_resume = true
	root.add_child(continue_backend)
	var continue_shell = build_shell(continue_backend)
	continue_shell.short_code.text = "123456"
	await continue_shell.submit_entry()
	await continue_shell.confirm_session()
	check(continue_shell.state == "active", "a normal continue stays active")
	check(continue_shell.entry_locked and not continue_shell.start.visible, "a normal continue also retires the entry")

	if failures.is_empty():
		print("P03R09AR_HARNESS_VERIFIED")
		quit(0)
	else:
		print("P03R09AR_HARNESS_FAILED ", JSON.stringify(failures))
		quit(1)
