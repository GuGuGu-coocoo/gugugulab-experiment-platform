extends SceneTree
## Fault harness for the optional assembly layer. It exercises the real adapter
## in Godot with controllable interface fakes; it is not storage/API evidence.
const Entry = preload("res://../moss_duel_legacy_adapter/gec_entry.gd")
var checks := 0
var failures := 0

class FakeGame extends Node2D:
	var starts := 0
	var controls_retired := false
	func set_host_controlled(value: bool) -> void:
		controls_retired = value
	func start_round() -> void:
		starts += 1

class FakeShell extends RefCounted:
	var overlay := true
	var announcements: Array[String] = []
	var finishes := 0
	func set_overlay_visible(value: bool) -> void:
		overlay = value
	func announce(text: String) -> void:
		announcements.append(text)
	func announce_finished(_result: Dictionary) -> void:
		finishes += 1

class FakeData extends RefCounted:
	var calls: Array[String] = []
	var record_calls := 0
	var commit_calls := 0
	var finish_calls := 0
	var committed: Array[Dictionary] = []
	var buffer: Array[Dictionary] = []
	var fail_commit := false
	var fail_finish := false
	func record(kind: String, payload: Dictionary, schema: Dictionary) -> Dictionary:
		record_calls += 1
		calls.append("record")
		buffer.append({"id": "event-" + str(record_calls), "kind": kind, "payload": payload.duplicate(true), "schema": schema.duplicate(true)})
		return {"event_id": buffer.back().id, "state": "buffered"}
	func commit() -> Dictionary:
		commit_calls += 1
		calls.append("commit")
		if fail_commit:
			fail_commit = false
			return {"error": "local_commit"}
		committed.append_array(buffer)
		buffer.clear()
		return {"state": "local_committed"}
	func finish() -> Dictionary:
		finish_calls += 1
		calls.append("finish")
		if fail_finish:
			fail_finish = false
			return {"error": "local_commit"}
		return {"state": "local_committed"}

func _initialize() -> void:
	call_deferred("run_checks")

func check(value: bool, name: String) -> void:
	checks += 1
	if not value:
		failures += 1
		push_error("MOSS_DUEL_ADAPTER_FAIL " + name)

func entry_fixture():
	var fixture = Entry.new()
	fixture.game = FakeGame.new()
	fixture.shell = FakeShell.new()
	fixture.data = FakeData.new()
	fixture.retry_save = Button.new()
	return fixture

func dispose(fixture) -> void:
	fixture.game.free()
	fixture.retry_save.free()
	fixture.free()

func run_checks() -> void:
	var sample = entry_fixture()
	var payload := {"choice": "heavy", "nested": {"value": 1}}
	sample._on_event("attack", payload)
	check(sample.queue.is_empty(), "unadmitted game emits no data")
	sample._on_admitted(null)
	check(sample.admitted and sample.game.starts == 1 and not sample.shell.overlay, "admission starts exactly one round and hides shell")
	sample.data.fail_commit = true
	sample._on_event("attack", payload)
	payload.nested.value = 999
	await sample._drain()
	check(sample.stopped and sample.game.process_mode == Node.PROCESS_MODE_DISABLED and sample.shell.overlay, "local failure pauses game and exposes retry")
	check(sample.data.record_calls == 1 and sample.data.buffer.size() == 1, "failed commit retains buffered identity")
	check(sample.queue[0].buffered and sample.data.buffer[0].payload.data.nested.value == 1, "signal snapshot copied before mutation")
	await sample._retry_save()
	check(not sample.stopped and sample.queue.is_empty() and not sample.shell.overlay, "successful retry resumes game")
	check(sample.data.record_calls == 1 and sample.data.commit_calls == 2, "commit retry never records second identity")
	check(sample.data.committed.size() == 1 and sample.data.committed[0].id == "event-1", "exactly one event committed")
	check(sample.data.committed[0].kind == "game.duel" and sample.data.committed[0].schema == {"id":"duel_event","version":"1"}, "registered event type and schema forwarded")
	sample._on_event("parry", {"success": true})
	sample._on_finished({"outcome": "win"})
	sample._on_finished({"outcome": "loss"})
	await sample._drain()
	check(sample.finished and sample.queue.is_empty() and not sample.admitted, "finish drains all queued events")
	check(sample.data.record_calls == 3 and sample.data.committed[-1].payload.kind == "round_summary", "summary recorded once after gameplay")
	check(sample.data.calls.back() == "finish" and sample.data.finish_calls == 1, "completion only after commits")
	check(not sample.game.visible and sample.game.controls_retired and sample.shell.overlay and sample.shell.finishes == 1, "completed world and CanvasLayer controls hidden; truthful finish shown")
	sample._on_event("late", {})
	await sample._drain()
	check(sample.data.record_calls == 3 and sample.data.finish_calls == 1, "closed participation cannot accept late event or second finish")
	dispose(sample)

	sample = entry_fixture()
	sample._on_admitted(null)
	sample.data.fail_finish = true
	sample._on_finished({"outcome": "loss"})
	await sample._drain()
	check(sample.stopped and not sample.finished and sample.shell.finishes == 0, "failed completion never announces saved finish")
	await sample._retry_save()
	check(sample.finished and sample.data.record_calls == 1 and sample.data.finish_calls == 2, "completion retry retains prior summary identity")
	dispose(sample)

	sample = entry_fixture()
	sample._on_admitted({"version": 1, "strategy": "trial_boundary_v1"})
	check(not sample.admitted and sample.game.starts == 0 and not sample.game.visible, "undeclared checkpoint never restarts battle")
	check(sample.shell.overlay and sample.data.record_calls == 0 and sample.data.finish_calls == 0, "data-only recovery never fabricates task completion")
	dispose(sample)
	print("MOSS_DUEL_ADAPTER_RESULT ", JSON.stringify({"checks": checks, "failures": failures}))
	quit(0 if failures == 0 else 1)
