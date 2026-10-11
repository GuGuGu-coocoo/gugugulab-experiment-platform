extends Node2D
## An ordinary standalone game. Optional hosts can listen to these generic
## signals; this project contains no networking, platform SDK or credentials.
signal telemetry_event(kind: String, payload: Dictionary)
signal run_finished(summary: Dictionary)

@export var host_controlled := false
const Player = preload("res://scripts/player.gd")
const Boss = preload("res://scripts/boss.gd")
const Arena = preload("res://scripts/arena.gd")
const Effects = preload("res://scripts/effects.gd")
const GOLD = Color("edc57b")
const PALE = Color("d8efe3")
const RED = Color("d27a6a")

var player
var boss
var effects
var camera: Camera2D
var ui: CanvasLayer
var hud: Control
var overlay: Control
var menu_panel: PanelContainer
var overlay_title: Label
var overlay_body: Label
var primary_button: Button
var help_label: Label
var status_label: Label
var player_bar: ProgressBar
var boss_bar: ProgressBar
var boss_label: Label
var feedback_label: Label
var combat_hint: Label
var pause_button: Button
var sound_button: Button
var state := "title"
var previous_state := "playing"
var round_started_us := 0
var simulation_time := 0.0
var round_index := 0
var event_sequence := 0
var event_history: Array[Dictionary] = []
var statistics := {}
var last_summary := {}
var feedback_time := 0.0
var shake_time := 0.0
var shake_power := 0.0
var audio_players: Array[AudioStreamPlayer] = []
var audio_cursor := 0
var muted := false
var sounds := {}

func _ready() -> void:
	_ensure_inputs()
	var arena := Arena.new()
	add_child(arena)
	_add_floor()
	player = Player.new()
	boss = Boss.new()
	add_child(player)
	add_child(boss)
	player.set_boss(boss)
	boss.set_player(player)
	player.health_changed.connect(_player_health)
	boss.health_changed.connect(_boss_health)
	player.defeated.connect(func(): call_deferred("_finish_round", "defeat"))
	boss.defeated.connect(func(): call_deferred("_finish_round", "victory"))
	player.action_event.connect(func(kind, payload): _actor_event("player", kind, payload))
	boss.action_event.connect(func(kind, payload): _actor_event("boss", kind, payload))
	effects = Effects.new()
	effects.z_index = 10
	add_child(effects)
	camera = Camera2D.new()
	camera.position = Vector2(640, 360)
	add_child(camera)
	_create_audio()
	_create_ui()
	player.reset_at(Vector2(330, 570))
	boss.reset_at(Vector2(925, 570))
	player.set_enabled(false)
	boss.set_enabled(false)
	set_host_controlled(host_controlled)
	if not host_controlled:
		show_start_screen()

func _ensure_inputs() -> void:
	var keys := {"move_left": [KEY_A, KEY_LEFT], "move_right": [KEY_D, KEY_RIGHT], "jump": [KEY_SPACE, KEY_W, KEY_UP], "dash": [KEY_E], "light_attack": [KEY_J], "heavy_attack": [KEY_K], "parry": [KEY_L, KEY_SHIFT], "pause_duel": [KEY_ESCAPE]}
	for action in keys:
		if not InputMap.has_action(action):
			InputMap.add_action(action)
		for key in keys[action]:
			var event := InputEventKey.new()
			event.physical_keycode = key
			if not InputMap.action_has_event(action, event):
				InputMap.action_add_event(action, event)
	# GUI mouse clicks must never become gameplay attacks. Keep mouse actions
	# separate, including when a project reuses an existing InputMap.
	for action in ["light_attack", "heavy_attack"]:
		for existing in InputMap.action_get_events(action):
			if existing is InputEventMouseButton:
				InputMap.action_erase_event(action, existing)
	for pair in [["light_mouse", MOUSE_BUTTON_LEFT], ["heavy_mouse", MOUSE_BUTTON_RIGHT]]:
		if not InputMap.has_action(pair[0]):
			InputMap.add_action(pair[0])
		var event := InputEventMouseButton.new()
		event.button_index = pair[1]
		if not InputMap.action_has_event(pair[0], event):
			InputMap.action_add_event(pair[0], event)

func _add_floor() -> void:
	var floor_body := StaticBody2D.new()
	var collision := CollisionShape2D.new()
	var shape := RectangleShape2D.new()
	shape.size = Vector2(1440, 110)
	collision.shape = shape
	floor_body.position = Vector2(640, 625)
	floor_body.add_child(collision)
	add_child(floor_body)

func _create_audio() -> void:
	for kind in ["light_attack", "heavy_attack", "parry", "player_hit", "boss_hit", "jump", "land", "victory", "boss_attack"]:
		var path: String = "res://assets/audio/" + kind + ".ogg"
		if ResourceLoader.exists(path):
			sounds[kind] = load(path)
	for i in range(8):
		var audio := AudioStreamPlayer.new()
		audio.volume_db = -11
		add_child(audio)
		audio_players.append(audio)

func _sound(kind: String) -> void:
	if muted or not sounds.has(kind):
		return
	var audio := audio_players[audio_cursor % audio_players.size()]
	audio_cursor += 1
	audio.stream = sounds[kind]
	audio.play()

func _create_ui() -> void:
	ui = CanvasLayer.new()
	add_child(ui)
	hud = Control.new()
	hud.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	hud.mouse_filter = Control.MOUSE_FILTER_IGNORE
	ui.add_child(hud)
	var brand := _label("MOSS / DUEL", 19, Color("789389"))
	brand.position = Vector2(40, 27)
	hud.add_child(brand)
	player_bar = _bar(PALE)
	player_bar.position = Vector2(40, 62)
	player_bar.size = Vector2(208, 12)
	hud.add_child(player_bar)
	status_label = _label("5 / 5   •   Wanderer", 14, PALE)
	status_label.position = Vector2(40, 83)
	hud.add_child(status_label)
	boss_label = _label("THE MOSS WARDEN", 21, GOLD)
	boss_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	boss_label.position = Vector2(420, 24)
	boss_label.size = Vector2(440, 30)
	hud.add_child(boss_label)
	boss_bar = _bar(GOLD)
	boss_bar.position = Vector2(440, 63)
	boss_bar.size = Vector2(400, 9)
	hud.add_child(boss_bar)
	pause_button = _button("Pause  [Esc]", func(): _toggle_pause())
	pause_button.position = Vector2(1101, 24)
	pause_button.size = Vector2(142, 42)
	pause_button.focus_mode = Control.FOCUS_NONE
	hud.add_child(pause_button)
	sound_button = _button("Sound: on", func():
		muted = not muted
		sound_button.text = "Sound: off" if muted else "Sound: on"
	)
	sound_button.position = Vector2(1101, 73)
	sound_button.size = Vector2(142, 37)
	sound_button.focus_mode = Control.FOCUS_NONE
	hud.add_child(sound_button)
	feedback_label = _label("", 23, GOLD)
	feedback_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	feedback_label.position = Vector2(300, 121)
	feedback_label.size = Vector2(680, 34)
	hud.add_child(feedback_label)
	combat_hint = _label("", 20, GOLD)
	combat_hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	combat_hint.position = Vector2(280, 615)
	combat_hint.size = Vector2(720, 30)
	hud.add_child(combat_hint)
	help_label = _label("A / D  move     Space  jump     E  dash     J  light     hold K  heavy     L / Shift  parry", 17, Color("a4b7ab"))
	help_label.position = Vector2(80, 666)
	help_label.size = Vector2(1120, 24)
	help_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	hud.add_child(help_label)
	overlay = Control.new()
	overlay.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	ui.add_child(overlay)
	var shade := ColorRect.new()
	shade.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	shade.color = Color(0.015, 0.04, 0.05, 0.67)
	overlay.add_child(shade)
	menu_panel = PanelContainer.new()
	menu_panel.position = Vector2(360, 166)
	menu_panel.size = Vector2(560, 375)
	menu_panel.add_theme_stylebox_override("panel", _style(Color("10262b"), Color("4a6860"), 20))
	overlay.add_child(menu_panel)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 15)
	menu_panel.add_child(box)
	var eyebrow := _label("A ONE-BOSS PARRY DUEL", 13, Color("93aa98"))
	eyebrow.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	box.add_child(eyebrow)
	overlay_title = _label("MOSS DUEL", 44, PALE)
	overlay_title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	box.add_child(overlay_title)
	overlay_body = _label("", 19, Color("b7c8bd"))
	overlay_body.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	overlay_body.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	overlay_body.custom_minimum_size = Vector2(485, 120)
	box.add_child(overlay_body)
	primary_button = _button("Enter the arena  [Enter]", _primary_action)
	primary_button.custom_minimum_size.y = 49
	box.add_child(primary_button)
	var tip := _label("Gold = parry. Red ground pulse = jump.\nHold heavy to break the Warden's guard.", 16, GOLD)
	tip.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	box.add_child(tip)

func _label(text: String, font_size: int, color: Color) -> Label:
	var label := Label.new()
	label.text = text
	label.add_theme_font_size_override("font_size", font_size)
	label.add_theme_color_override("font_color", color)
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return label

func _style(background: Color, border: Color, margin := 12) -> StyleBoxFlat:
	var style := StyleBoxFlat.new()
	style.bg_color = background
	style.border_color = border
	style.set_border_width_all(1)
	style.set_corner_radius_all(9)
	style.content_margin_left = margin
	style.content_margin_right = margin
	style.content_margin_top = margin
	style.content_margin_bottom = margin
	return style

func _button(text: String, callback: Callable) -> Button:
	var button := Button.new()
	button.text = text
	button.add_theme_font_size_override("font_size", 17)
	button.add_theme_color_override("font_color", PALE)
	button.add_theme_stylebox_override("normal", _style(Color("22443e"), Color("638371")))
	button.add_theme_stylebox_override("hover", _style(Color("31584b"), GOLD))
	button.add_theme_stylebox_override("pressed", _style(Color("183930"), GOLD))
	button.add_theme_stylebox_override("focus", _style(Color(0, 0, 0, 0), GOLD))
	button.pressed.connect(callback)
	return button

func _bar(color: Color) -> ProgressBar:
	var bar := ProgressBar.new()
	bar.show_percentage = false
	bar.mouse_filter = Control.MOUSE_FILTER_IGNORE
	bar.add_theme_stylebox_override("background", _style(Color("1b3336"), Color("304746"), 0))
	bar.add_theme_stylebox_override("fill", _style(color, color, 0))
	return bar

func set_host_controlled(enabled: bool) -> void:
	host_controlled = enabled
	if overlay != null:
		overlay.visible = not enabled
	if hud != null:
		hud.visible = not enabled
	if player != null:
		player.set_enabled(false)
		boss.set_enabled(false)

func show_start_screen() -> void:
	state = "title"
	if host_controlled:
		return
	overlay.visible = true
	hud.visible = false
	overlay_title.text = "MOSS DUEL"
	overlay_body.text = "Beneath a ruined arch, a tired guardian\nwaits for one last honest duel.\n\nFive hearts. One blade. Learn the rhythm."
	primary_button.text = "Enter the arena  [Enter]"
	primary_button.grab_focus()

func start_round() -> void:
	round_index += 1
	event_sequence = 0
	event_history.clear()
	statistics = {"light_attacks": 0, "heavy_attacks": 0, "parries": 0, "damage_taken": 0, "dodges": 0, "pauses": 0}
	simulation_time = 0.0
	round_started_us = Time.get_ticks_usec()
	player.reset_at(Vector2(330, 570))
	boss.reset_at(Vector2(925, 570))
	player.process_mode = Node.PROCESS_MODE_INHERIT
	boss.process_mode = Node.PROCESS_MODE_INHERIT
	player.set_enabled(true)
	boss.set_enabled(true)
	state = "playing"
	hud.visible = true
	overlay.visible = false
	primary_button.release_focus()
	_feedback("Read the gold wind-up. Parry at the last moment.", 3.5)
	_emit_event("round_started", {"round": round_index, "rules": "moss_duel_v1", "boss_patterns": ["sweep", "lunge", "quake"]})

func _primary_action() -> void:
	if state == "paused":
		_toggle_pause()
	elif not host_controlled:
		start_round()

func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.physical_keycode == KEY_ENTER and state in ["title", "victory", "defeat"] and not host_controlled:
			start_round()
			get_viewport().set_input_as_handled()
		elif event.is_action_pressed("pause_duel") and state in ["playing", "paused"]:
			_toggle_pause()
			get_viewport().set_input_as_handled()

func _toggle_pause() -> void:
	if state == "playing":
		state = "paused"
		player.process_mode = Node.PROCESS_MODE_DISABLED
		boss.process_mode = Node.PROCESS_MODE_DISABLED
		statistics["pauses"] += 1
		_emit_event("pause", {"paused": true})
		overlay_title.text = "TAKE A BREATH"
		overlay_body.text = "The fight is paused.\n\nJ: quick slash    K: hold and release\nL / Shift: parry    E: dash\nSpace: jump over red ground pulses"
		primary_button.text = "Resume  [Esc]"
		overlay.visible = true
		primary_button.grab_focus()
	elif state == "paused":
		state = "playing"
		player.process_mode = Node.PROCESS_MODE_INHERIT
		boss.process_mode = Node.PROCESS_MODE_INHERIT
		overlay.visible = false
		primary_button.release_focus()
		_emit_event("pause", {"paused": false})

func _process(delta: float) -> void:
	if state == "playing":
		simulation_time += delta
		combat_hint.text = player.get_status_text() if not player.get_status_text().is_empty() else boss.get_status_text()
		combat_hint.modulate = RED if boss.state == "telegraph" and boss.current_attack == "quake" else GOLD
	if feedback_time > 0:
		feedback_time -= delta
		feedback_label.modulate.a = minf(1.0, feedback_time * 2)
	if shake_time > 0:
		shake_time -= delta
		camera.offset = Vector2(sin(shake_time * 113), cos(shake_time * 137)) * shake_power * minf(1, shake_time * 5)
	else:
		camera.offset = Vector2.ZERO

func _player_health(current, maximum) -> void:
	if player_bar == null:
		return
	player_bar.max_value = maximum
	player_bar.value = current
	status_label.text = "%s / %s   •   Wanderer" % [current, maximum]

func _boss_health(current, maximum) -> void:
	if boss_bar == null:
		return
	boss_bar.max_value = maximum
	boss_bar.value = current
	boss_label.text = "THE MOSS WARDEN" if current > maximum * 0.5 else "THE MOSS WARDEN  •  UNBOUND"

func _actor_event(actor: String, kind: String, payload: Dictionary) -> void:
	if state != "playing":
		return
	var point: Vector2 = player.position + Vector2(0, -44) if actor == "player" else boss.position + Vector2(0, -55)
	if kind.contains("parry_success"):
		statistics["parries"] += 1
		_sound("parry")
		effects.burst(point, GOLD, 22, 400)
		effects.floating_text(point + Vector2(-28, -26), "PARRY!", GOLD)
		_feedback("Perfect parry — the Warden is open.", 1.3)
		_shake(6)
	elif kind == "light_attack":
		statistics["light_attacks"] += 1
		_sound("light_attack")
	elif kind in ["heavy_attack", "heavy_charged_attack"]:
		statistics["heavy_attacks"] += 1
		_sound("heavy_attack")
	elif kind in ["damage_taken", "boss_damage"]:
		if actor == "player":
			statistics["damage_taken"] += int(payload.get("damage", 1))
			_sound("player_hit")
			effects.burst(point, RED, 12)
			_shake(5)
		else:
			_sound("boss_hit")
			effects.burst(point, PALE, 9)
	elif kind == "dash":
		statistics["dodges"] += 1
	elif kind == "jump":
		_sound("jump")
	elif kind == "land":
		_sound("land")
	elif kind == "boss_attack":
		_sound("boss_attack")
	elif kind == "boss_phase":
		_feedback("UNBOUND — the Warden's rhythm has quickened.", 3.0)
		_shake(4)
	var data := payload.duplicate(true)
	# Dictionary dot assignment creates StringName keys in Godot. The public
	# signal promises JSON data, so always add fields with explicit String keys.
	data["actor"] = actor
	data["positions"] = {"player": {"x": player.position.x, "y": player.position.y}, "boss": {"x": boss.position.x, "y": boss.position.y}}
	_emit_event(kind, data)

func _emit_event(kind: String, data: Dictionary) -> void:
	event_sequence += 1
	var payload := {"sequence": event_sequence, "elapsed_ms": float(Time.get_ticks_usec() - round_started_us) / 1000.0, "simulation_ms": simulation_time * 1000.0, "clock": {"unit": "ms", "source": "Godot monotonic ticks", "epoch": str(round_started_us)}, "data": data.duplicate(true)}
	event_history.append({"kind": kind, "payload": payload.duplicate(true)})
	telemetry_event.emit(kind, payload)

func _finish_round(outcome: String) -> void:
	if state != "playing":
		return
	player.set_enabled(false)
	boss.set_enabled(false)
	_emit_event("round_ended", {"outcome": outcome, "player_health": player.health, "boss_health": boss.health})
	state = outcome
	last_summary = {"outcome": outcome, "elapsed_ms": float(Time.get_ticks_usec() - round_started_us) / 1000.0, "simulation_ms": simulation_time * 1000.0, "statistics": statistics.duplicate(true), "player_health": player.health, "boss_health": boss.health, "game_version": "moss-duel-1"}
	if outcome == "victory":
		_sound("victory")
		effects.burst(boss.position + Vector2(0, -55), GOLD, 32, 320)
	overlay_title.text = "THE GATE OPENS" if outcome == "victory" else "A LESSON IN MOSS"
	overlay_body.text = ("You met the Warden's rhythm." if outcome == "victory" else "Watch the wind-up. You can try again.") + "\n\nTime %.1f s   •   Parries %s\nLight %s   •   Heavy %s   •   Hits taken %s" % [last_summary.elapsed_ms / 1000.0, statistics.parries, statistics.light_attacks, statistics.heavy_attacks, statistics.damage_taken]
	primary_button.text = "Another duel  [Enter]"
	primary_button.visible = not host_controlled
	overlay.visible = not host_controlled
	if not host_controlled:
		primary_button.grab_focus()
	run_finished.emit(last_summary.duplicate(true))

func _feedback(text: String, seconds: float) -> void:
	feedback_label.text = text
	feedback_label.modulate.a = 1
	feedback_time = seconds

func _shake(strength: float) -> void:
	shake_time = 0.22
	shake_power = strength
