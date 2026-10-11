extends SceneTree
## Integration of the real main scene, menus, combat signals and end-of-run order.

const MainScene := preload("res://main.tscn")
var checks := 0
var failures: Array[String] = []
var finishes: Array[Dictionary] = []
var game: Node2D


func _initialize() -> void:
	call_deferred("_run")


func _check(condition: bool, label: String) -> void:
	checks += 1
	if not condition:
		failures.append(label)
		push_error(label)


func _frames(count: int) -> void:
	for frame in range(count):
		await physics_frame


func _event_index(kind: String) -> int:
	for index in range(game.event_history.size()):
		if game.event_history[index].kind == kind:
			return index
	return -1


func _stop_actor_physics() -> void:
	game.player.set_physics_process(false)
	game.boss.set_physics_process(false)


func _run() -> void:
	game = MainScene.instantiate()
	root.add_child(game)
	game.run_finished.connect(func(summary: Dictionary): finishes.append(summary))
	await process_frame
	_check(game.state == "title" and game.overlay.visible and not game.hud.visible, "standalone launches in title menu")
	_check(not game.player.enabled and not game.boss.enabled, "title menu cannot advance combat")
	game.primary_button.pressed.emit()
	_check(game.state == "playing" and not game.overlay.visible and game.hud.visible, "start button enters playable arena")
	_check(game.player.enabled and game.boss.enabled and game.round_index == 1, "start enables both fighters once")
	_check(_event_index("round_started") == 0, "round start is first telemetry event")
	await _frames(8)
	game.player.begin_parry()
	game.boss.begin_attack("sweep")
	var player_snapshot := {"state": game.player.state, "state_time": game.player.state_time, "position": game.player.position, "parry_cooldown": game.player.parry_cooldown}
	var boss_snapshot := {"state": game.boss.state, "remaining": game.boss.state_remaining, "position": game.boss.position}
	game.pause_button.pressed.emit()
	var simulation_snapshot: float = game.simulation_time
	_check(game.state == "paused" and game.overlay.visible, "pause button opens pause menu")
	await _frames(12)
	_check(game.player.state == player_snapshot.state and game.player.state_time == player_snapshot.state_time and game.player.position == player_snapshot.position and game.player.parry_cooldown == player_snapshot.parry_cooldown, "pause preserves active player parry, position and cooldown")
	_check(game.boss.state == boss_snapshot.state and game.boss.state_remaining == boss_snapshot.remaining and game.boss.position == boss_snapshot.position, "pause preserves boss telegraph deadline and position")
	_check(game.simulation_time == simulation_snapshot, "paused time does not enter simulation duration")
	game.primary_button.pressed.emit()
	_check(game.state == "playing" and game.player.state == "parry" and game.boss.state == "telegraph", "resume keeps exact combat states")
	await _frames(3)
	_check(game.boss.state_remaining < boss_snapshot.remaining, "boss telegraph continues after resume")
	# Pause during heavy charge and release the physical action while paused.
	game.player.state = "idle"
	Input.action_press("heavy_attack")
	await _frames(2)
	_check(game.player.state == "charge", "heavy input enters charge in live scene")
	game._toggle_pause()
	var charge_snapshot: float = game.player.charge_time
	Input.action_release("heavy_attack")
	await _frames(4)
	_check(game.player.charge_time == charge_snapshot, "heavy charge clock freezes during pause")
	game._toggle_pause()
	await _frames(2)
	_check(game.player.state == "attack", "heavy released while paused attacks on resume instead of getting stuck")
	# Pause in recovery as well as in a wind-up.
	game.boss.state = "recovery"
	game.boss.state_remaining = 0.7
	game._toggle_pause()
	await _frames(4)
	_check(game.boss.state == "recovery" and game.boss.state_remaining == 0.7, "pause preserves boss recovery opening")
	game._toggle_pause()
	await _frames(2)
	_check(game.boss.state == "recovery" and game.boss.state_remaining < 0.7, "recovery resumes without restarting attack")
	# Killing light hit: the final action is recorded before deferred round end.
	_stop_actor_physics()
	game.player.state = "idle"
	game.player.facing = 1
	game.player.position = Vector2(450, 570)
	game.boss.position = Vector2(520, 570)
	game.boss.take_damage(game.boss.health - 1, game.player.position, "test_setup")
	game.player.begin_light_attack()
	_check(game.player.resolve_attack(), "killing light attack resolves")
	_check(game.state == "playing", "finish waits until killing hit signal stack completes")
	await process_frame
	await process_frame
	_check(game.state == "victory" and finishes.size() == 1, "victory emits one run_finished")
	_check(_event_index("attack_hit") >= 0 and _event_index("round_ended") > _event_index("attack_hit"), "final attack_hit precedes round_ended")
	_check(finishes[0].outcome == "victory" and finishes[0].statistics.light_attacks >= 1, "victory summary includes final action")
	game._finish_round("victory")
	_check(finishes.size() == 1, "duplicate finish cannot emit second summary")
	# Killing parry preserves its success and statistics before finish too.
	game.start_round()
	_stop_actor_physics()
	game.boss.take_damage(game.boss.health - 2, game.player.position, "test_setup")
	game.player.begin_parry()
	_check(game.player.receive_attack(1, true, game.boss, "sweep") == "parried", "killing parry succeeds")
	await process_frame
	await process_frame
	_check(game.state == "victory" and finishes.size() == 2 and game.last_summary.statistics.parries == 1, "killing parry finishes once and enters summary")
	_check(_event_index("parry_success") >= 0 and _event_index("round_ended") > _event_index("parry_success"), "final parry_success precedes round_ended")
	# A fresh round can lose; public finish is still exactly once.
	game.start_round()
	_stop_actor_physics()
	for hit in range(5):
		game.player.invulnerability = 0.0
		game.player.receive_attack(1, false, game.boss, "quake")
	await process_frame
	await process_frame
	_check(game.state == "defeat" and finishes.size() == 3 and game.last_summary.player_health == 0, "defeat emits one complete summary")
	game._finish_round("defeat")
	_check(finishes.size() == 3, "duplicate defeat finish is ignored")
	# Host controls start/retry; the game still exposes start_round to that host.
	game.set_host_controlled(true)
	game.show_start_screen()
	var round_before: int = game.round_index
	game.primary_button.pressed.emit()
	var enter := InputEventKey.new()
	enter.physical_keycode = KEY_ENTER
	enter.pressed = true
	game._unhandled_input(enter)
	_check(game.state == "title" and game.round_index == round_before and not game.overlay.visible, "host-controlled menu and Enter cannot start a round")
	game.start_round()
	_stop_actor_physics()
	game._toggle_pause()
	game.primary_button.pressed.emit()
	_check(game.state == "playing", "host-controlled Resume button remains usable")
	if game.state == "paused":
		game._toggle_pause()
	game.boss.take_damage(1000, game.player.position, "test_setup")
	await process_frame
	await process_frame
	round_before = game.round_index
	game.primary_button.pressed.emit()
	game._unhandled_input(enter)
	_check(game.state == "victory" and game.round_index == round_before and not game.overlay.visible, "host-controlled finish cannot retry from menu or Enter")
	_check(finishes.size() == 4, "host-controlled completion emits exactly one summary")
	# Free the complete real scene before engine teardown, including audio Resources.
	game.queue_free()
	game = null
	await process_frame
	await process_frame
	print("MOSS_DUEL_MAIN: %d checks, %d failures" % [checks, failures.size()])
	quit(0 if failures.is_empty() else 1)
