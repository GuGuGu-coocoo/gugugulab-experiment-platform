extends SceneTree
## Real InputEvent dispatch through Godot GUI and the live fighter physics.

const MainScene := preload("res://main.tscn")
var game: Node2D
var failures: Array[String] = []
var checks := 0


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


func _move_mouse(point: Vector2) -> void:
	var motion := InputEventMouseMotion.new()
	motion.position = point
	motion.global_position = point
	Input.parse_input_event(motion)
	await process_frame


func _mouse_button(point: Vector2, button: int, pressed: bool) -> void:
	var event := InputEventMouseButton.new()
	event.position = point
	event.global_position = point
	event.button_index = button
	event.pressed = pressed
	Input.parse_input_event(event)


func _click(control: Control, button := MOUSE_BUTTON_LEFT) -> void:
	var point := control.get_global_rect().get_center()
	await _move_mouse(point)
	_mouse_button(point, button, true)
	_mouse_button(point, button, false)
	await _frames(2)


func _key(key: int, pressed: bool) -> void:
	var event := InputEventKey.new()
	event.physical_keycode = key
	event.pressed = pressed
	Input.parse_input_event(event)


func _run() -> void:
	root.size = Vector2i(1280, 720)
	game = MainScene.instantiate()
	root.add_child(game)
	await _frames(3)
	await _click(game.primary_button)
	_check(game.state == "playing", "real mouse click starts the game through the actual Button")
	_check(game.statistics.light_attacks == 0 and game.player.state == "idle", "Start mouse click does not leak a light attack")
	game.boss.set_physics_process(false)
	await _click(game.sound_button)
	_check(game.muted, "real Sound click toggles audio")
	_check(game.statistics.light_attacks == 0 and game.player.state == "idle", "Sound mouse click does not attack")
	await _click(game.pause_button)
	_check(game.state == "paused", "real Pause click pauses the game")
	await _click(game.primary_button)
	_check(game.state == "playing", "real Resume click resumes the game")
	_check(game.statistics.light_attacks == 0 and game.player.state == "idle", "Resume mouse click does not leak a light attack")
	# Hovering the HUD blocks mouse attacks, but keyboard J/K still work.
	await _move_mouse(game.sound_button.get_global_rect().get_center())
	_key(KEY_J, true)
	await _frames(2)
	_key(KEY_J, false)
	_check(game.statistics.light_attacks == 1 and game.player.state == "attack", "keyboard J attacks while pointer hovers over GUI")
	await _frames(24)
	_key(KEY_K, true)
	await _frames(2)
	_check(game.player.state == "charge", "keyboard K charges while pointer hovers over GUI")
	_key(KEY_K, false)
	await _frames(2)
	_check(game.statistics.heavy_attacks == 1 and game.player.state == "attack", "keyboard K release attacks while pointer hovers over GUI")
	await _frames(38)
	await _click(game.sound_button, MOUSE_BUTTON_RIGHT)
	_check(game.statistics.heavy_attacks == 1 and game.player.state == "idle", "right mouse click on GUI cannot begin heavy charge")
	# Mouse actions remain playable in the unobstructed arena.
	var arena_point := Vector2(500, 400)
	await _move_mouse(arena_point)
	_mouse_button(arena_point, MOUSE_BUTTON_LEFT, true)
	_mouse_button(arena_point, MOUSE_BUTTON_LEFT, false)
	await _frames(2)
	_check(game.statistics.light_attacks == 2, "left mouse click in arena attacks normally")
	await _frames(24)
	_mouse_button(arena_point, MOUSE_BUTTON_RIGHT, true)
	await _frames(2)
	_check(game.player.state == "charge", "right mouse hold in arena begins charge")
	await _move_mouse(game.sound_button.get_global_rect().get_center())
	_mouse_button(game.sound_button.get_global_rect().get_center(), MOUSE_BUTTON_RIGHT, false)
	await _frames(2)
	_check(game.statistics.heavy_attacks == 2 and game.player.state == "attack", "arena-started mouse charge releases over GUI without getting stuck")
	await _frames(38)
	await _move_mouse(arena_point)
	_mouse_button(arena_point, MOUSE_BUTTON_RIGHT, true)
	await _frames(2)
	_check(game.player.state == "charge", "second mouse charge starts")
	game._toggle_pause()
	_mouse_button(arena_point, MOUSE_BUTTON_RIGHT, false)
	await _frames(4)
	game._toggle_pause()
	await _frames(2)
	_check(game.statistics.heavy_attacks == 3 and game.player.state == "attack", "mouse release during pause attacks on resume")
	game.queue_free()
	game = null
	await process_frame
	await process_frame
	print("MOSS_DUEL_MOUSE_INPUT: %d checks, %d failures" % [checks, failures.size()])
	quit(0 if failures.is_empty() else 1)
