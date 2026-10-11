extends SceneTree
## Real convenience helper, data validation and shell; controlled backend faults
## exercise ordering. This is component evidence, not storage/API acceptance.
const Session = preload("res://addons/gec/session.gd")
var checks := 0
var failures := 0

class Task extends Node:
	var starts := 0
	var retires := 0
	func start() -> void:
		starts += 1
		process_mode = Node.PROCESS_MODE_ALWAYS
	func retire() -> void:
		retires += 1

class Backend extends Node:
	signal summary_continue()
	var config := {"locale": "zh", "mode": "anonymous"}
	var prepare_result := {"state": "active", "checkpoint": null}
	var current := {"state": "unprepared"}
	var buffer: Array[Dictionary] = []
	var records: Array[Dictionary] = []
	var calls: Array[String] = []
	var record_calls := 0
	var commit_calls := 0
	var finish_calls := 0
	var commit_failures := 0
	var finish_failures := 0
	var record_wait := false
	var setup_error := ""
	var summary_blocked := false
	func initialize_queue() -> Dictionary:
		return {"state": "ready"} if setup_error.is_empty() else {"error": setup_error}
	func prepare(options: Dictionary = {}) -> Dictionary:
		calls.append("prepare:" + str(options.expected_version))
		current = prepare_result.duplicate(true)
		return current
	func recover_code(_value: String) -> Dictionary:
		return {"error": "not_used"}
	func record(kind: String, payload: Dictionary, schema: Dictionary, observed: Dictionary = {}) -> Dictionary:
		record_calls += 1
		calls.append("record")
		buffer.append({"event_id": "id-" + str(record_calls), "kind": kind,
			"payload": payload.duplicate(true), "schema": schema.duplicate(true), "observed": observed.duplicate(true)})
		if record_wait:
			await get_tree().process_frame
		return {"state": "buffered", "event_id": buffer.back().event_id}
	func commit(_checkpoint: Dictionary = {}) -> Dictionary:
		commit_calls += 1
		calls.append("commit")
		if commit_failures > 0:
			commit_failures -= 1
			return {"error": "local_commit"}
		records.append_array(buffer)
		buffer.clear()
		return {"state": "local_committed"}
	func finish() -> Dictionary:
		finish_calls += 1
		calls.append("finish")
		if finish_failures > 0:
			finish_failures -= 1
			return {"error": "local_commit"}
		current = {"state": "local_committed"}
		return current
	func status() -> Dictionary:
		return current
	func summary() -> Dictionary:
		if summary_blocked:
			await summary_continue
		return current

func _initialize() -> void:
	call_deferred("run_checks")

func check(condition: bool, name: String) -> void:
	checks += 1
	if not condition:
		failures += 1
		push_error("GEC_SESSION_FAIL " + name)

func fixture():
	var task := Task.new()
	task.process_mode = Node.PROCESS_MODE_PAUSABLE
	root.add_child(task)
	var session = Session.new()
	session.backend = Backend.new()
	session.display_locale = "en"
	root.add_child(session)
	var configured: Dictionary = session.configure_defaults({"event_type": "custom.event", "schema": {"id": "arbitrary", "version": "3"}})
	check(not configured.has("error"), "ordinary data defaults accepted before preparation")
	var frozen := JSON.stringify(session.backend.config)
	var prepared: Dictionary = session.prepare("program-27", task, task.start, task.retire)
	check(not prepared.has("error") and task.process_mode == Node.PROCESS_MODE_DISABLED and task.starts == 0,
		"preparation blocks task until real shell admission")
	check(JSON.stringify(session.backend.config) == frozen and session.shell.locale == "en", "locale override does not edit frozen config")
	return {"session": session, "task": task}

func dispose(sample: Dictionary) -> void:
	sample.session.queue_free()
	sample.task.queue_free()

func run_checks() -> void:
	var sample: Dictionary = fixture()
	var session = sample.session
	var task = sample.task
	check(session.record({"value": 1}).error == "not_active", "unadmitted record refused")
	await session.shell._on_web_action(["local-retry"])
	check(task.starts == 0 and session.backend.commit_calls == 0, "disabled local retry action never admits or commits")
	var admitted: Dictionary = await session.shell.auto_submit({})
	check(not admitted.has("error") and task.starts == 1 and task.process_mode == Node.PROCESS_MODE_ALWAYS,
		"shell admission activates host callback once")
	check(session.backend.calls[0] == "prepare:program-27", "caller version sent to admission")
	check(session.status().keys().all(func(key): return key is String), "public status uses ordinary JSON string keys")
	var cyclic := {"nested": []}
	cyclic.nested.append(cyclic)
	check(session.record(cyclic).error == "invalid_value" and session.queue.is_empty(), "cyclic payload is rejected before snapshot copying")
	cyclic.nested.clear()
	check(session.configure_defaults({"event_type": "bad", "schema": {}}).has("error") and session.data.defaults.event_type == "custom.event",
		"rejected defaults preserve prior ordinary event declaration")
	var payload := {"nested": {"value": 1}}
	session.backend.commit_failures = 2
	session.backend.summary_blocked = true
	session.backend.current = {"state": "remote_acknowledged", "complete_ack": true}
	# Begin a real Shell refresh before the local failure, hold the controlled
	# backend summary at its async boundary, then release it after the fault.
	session.shell._refresh_state()
	var queued: Dictionary = session.record(payload)
	payload.nested.value = 999
	check(queued.state == "queued" and not queued.has("event_id") and session.backend.records.is_empty(), "memory queue never claims durable save")
	await session._drain()
	check(session.stopped and task.process_mode == Node.PROCESS_MODE_DISABLED and session.retry_save.visible,
		"failed local save pauses task and exposes retry")
	check(session.shell.local_save_retry and session.shell.entry_locked and session.shell.overlay_visible,
		"save failure exposes independent shell retry without restoring admission")
	var local_failure: String = session.shell.last_announcement
	await session.shell._refresh_state()
	check(session.shell.last_announcement == local_failure, "background summary cannot obscure an uncommitted save failure")
	session.backend.summary_blocked = false
	session.backend.summary_continue.emit()
	await process_frame
	check(session.shell.last_announcement == local_failure, "an already inflight summary also preserves the local failure")
	check(session.backend.record_calls == 1 and session.backend.buffer[0].event_id == "id-1" and session.queue[0].buffered,
		"failed commit retains original buffered event identity")
	await session.shell._on_web_action(["local-retry"])
	check(session.stopped and task.process_mode == Node.PROCESS_MODE_DISABLED and session.backend.record_calls == 1,
		"repeated failure stays paused without generating new identity")
	check(session.shell.local_save_retry, "a repeated commit fault makes local retry available again")
	await session.shell._on_web_action(["local-retry"])
	check(not session.stopped and task.process_mode == Node.PROCESS_MODE_ALWAYS and not session.retry_save.visible,
		"successful retry restores mode from before first failure")
	check(not session.shell.local_save_retry and not session.shell.overlay_visible and session.shell.entry_locked,
		"successful retry hides local retry and returns canvas ownership without reopening entry")
	check(session.backend.records.size() == 1 and session.backend.records[0].payload.nested.value == 1,
		"call-time snapshot survives producer mutation")
	check(session.backend.records[0].kind == "custom.event" and session.backend.records[0].schema == {"id": "arbitrary", "version": "3"},
		"no built-in event type schema or task version")
	session.record({"value": 2}, "explicit.type", {"id": "explicit", "version": "1"})
	session.finish()
	session.finish()
	await session._drain()
	check(session.finished and session.backend.records.size() == 2 and session.backend.calls.back() == "finish",
		"finish drains recorded values before completion")
	check(session.backend.finish_calls == 1 and task.retires == 1 and task.process_mode == Node.PROCESS_MODE_DISABLED,
		"completion retires task once and never restarts it")
	check(session.record({}).error == "not_active" and session.finish().state == "finished", "closed helper rejects later events")
	await session.shell._on_web_action(["local-retry"])
	check(not session.shell.local_save_retry and session.shell.overlay_visible and task.starts == 1 and session.backend.finish_calls == 1,
		"a stale local retry after completion cannot restart task or repeat finish")
	dispose(sample)
	await process_frame

	sample = fixture(); session = sample.session; task = sample.task
	await session.shell.auto_submit({})
	session.backend.record_wait = true
	session.record({"order": 1})
	await process_frame
	session.record({"order": 2})
	var finishing: Dictionary = session.finish()
	check(finishing.state == "finishing" and not session.finished, "inflight record delays actual completion")
	for _frame in range(10):
		if session.finished:
			break
		await process_frame
	check(session.finished and session.backend.records.size() == 2 and session.backend.records[0].payload.order == 1 and session.backend.records[1].payload.order == 2,
		"asynchronous drain preserves producer ordering")
	check(session.backend.finish_calls == 1 and task.starts == 1 and task.retires == 1, "inflight finish never opens another task")
	dispose(sample)
	await process_frame

	sample = fixture(); session = sample.session; task = sample.task
	await session.shell.auto_submit({})
	session.backend.finish_failures = 1
	session.record({"last": true})
	session.finish()
	await session._drain()
	check(session.stopped and not session.finished and not session.shell.finished_announced and task.retires == 0,
		"failed completion does not claim saved finish")
	await session.shell._on_web_action(["local-retry"])
	check(session.finished and session.backend.record_calls == 1 and session.backend.finish_calls == 2 and task.starts == 1 and task.retires == 1,
		"finish retry reuses prior record and never restarts task")
	check(not session.shell.local_save_retry and task.process_mode == Node.PROCESS_MODE_DISABLED,
		"finish drain clears retry control while retaining retired task")
	dispose(sample)
	await process_frame

	for result in [{"state": "data_only"}, {"state": "active", "checkpoint": {"format_version": 1}}]:
		sample = fixture(); session = sample.session; task = sample.task
		session.backend.prepare_result = result
		await session.shell.auto_submit({})
		check(session.data_only and not session.active and task.starts == 0 and task.retires == 1,
			"data-only or undeclared checkpoint never invokes start")
		check(session.record({}).error == "not_active" and session.finish().error == "not_active" and session.backend.finish_calls == 0,
			"recovery cannot fabricate new data or completion")
		dispose(sample)
		await process_frame

	for failure in ["storage_open", "unsupported_configuration"]:
		var backend := Backend.new()
		if failure == "unsupported_configuration":
			backend.config.mode = "unknown"
		else:
			backend.setup_error = failure
		var frozen := JSON.stringify(backend.config)
		task = Task.new()
		root.add_child(task)
		session = Session.new()
		session.backend = backend
		session.display_locale = "en"
		root.add_child(session)
		var result: Dictionary = session.prepare("any-version", task, task.start, task.retire)
		check(result.error == failure and session.shell.locale == "en" and not session.active and task.starts == 0,
			"failed setup stays inactive with display override")
		check(session.shell.last_announcement == (session.shell.t("unsupported") if failure == "unsupported_configuration" else session.shell.t("error_prefix", {"code": failure})),
			"display override restores actual failure reason")
		check(JSON.stringify(backend.config) == frozen and (failure != "unsupported_configuration" or session.shell.start.disabled),
			"failed setup preserves frozen configuration and unsupported guard")
		session.queue_free(); task.queue_free()
		await process_frame

	print("GEC_SESSION_RESULT ", JSON.stringify({"checks": checks, "failures": failures}))
	quit(0 if failures == 0 else 1)
