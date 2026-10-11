extends SceneTree
## Leave the real Boss AI running until it naturally defeats an idle player.
## No SDK, backend, injected hit or replacement combat loop is used.

const MainScene := preload("res://main.tscn")
const JsonContract := preload("res://tests/json_contract.gd")
var game: Node2D
var checks := 0
var failures: Array[String] = []
var events: Array[Dictionary] = []
var summaries: Array[Dictionary] = []


func _initialize() -> void:
	call_deferred("_run")


func _check(condition: bool, label: String) -> void:
	checks += 1
	if not condition:
		failures.append(label)
		push_error(label)


func _receive_event(kind: String, payload: Dictionary) -> void:
	var error: String = JsonContract.problem(payload)
	_check(error.is_empty(), "JSON telemetry " + kind + ": " + error)
	events.append({"kind": kind, "payload": payload.duplicate(true)})


func _receive_summary(summary: Dictionary) -> void:
	var error: String = JsonContract.problem(summary)
	_check(error.is_empty(), "JSON completion summary: " + error)
	summaries.append(summary.duplicate(true))


func _run() -> void:
	_check(JsonContract.problem({"nested": [{"ok": true, "number": 1.25}, null]}).is_empty(), "JSON contract accepts nested JSON types")
	_check(not JsonContract.problem({&"actor": "player"}).is_empty(), "JSON contract rejects StringName object keys")
	_check(not JsonContract.problem({"position": Vector2.ONE}).is_empty(), "JSON contract rejects non-JSON vectors")
	_check(not JsonContract.problem({"time": INF}).is_empty(), "JSON contract rejects infinite numbers")
	game = MainScene.instantiate()
	root.add_child(game)
	game.muted = true
	game.telemetry_event.connect(_receive_event)
	game.run_finished.connect(_receive_summary)
	game.start_round()
	for frame in range(1800):
		await physics_frame
		if game.state == "defeat":
			break
	await process_frame
	_check(game.state == "defeat" and game.player.health == 0, "real Boss AI naturally defeats the idle player")
	_check(summaries.size() == 1 and summaries[0].outcome == "defeat", "natural defeat emits exactly one JSON completion")
	var kinds: Array[String] = []
	for event in events:
		kinds.append(event.kind)
	_check(kinds.has("boss_telegraph") and kinds.has("boss_attack") and kinds.has("boss_attack_result"), "natural Boss warnings, attacks and resolutions emit JSON")
	_check(kinds.count("damage_taken") == 5, "five real Boss hits emit damage records")
	_check(kinds.count("round_ended") == 1 and kinds[-1] == "round_ended", "natural end follows combat records exactly once")
	_check(summaries.size() == 1 and summaries[0].statistics.damage_taken == 5, "natural completion reports actual accumulated damage")
	for index in range(events.size()):
		_check(events[index].payload.sequence == index + 1, "natural telemetry sequence remains contiguous")
	game.queue_free()
	game = null
	await process_frame
	await process_frame
	print("MOSS_DUEL_TELEMETRY: %d checks, %d failures, %d natural events" % [checks, failures.size(), events.size()])
	quit(0 if failures.is_empty() else 1)
