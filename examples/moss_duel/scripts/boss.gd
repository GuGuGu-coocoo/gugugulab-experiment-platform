extends CharacterBody2D

signal health_changed(current: int, maximum: int)
signal defeated
signal action_event(kind: String, payload: Dictionary)

const MAX_HEALTH := 28
const GRAVITY := 1680.0
const QUAKE_RANGE := 390.0

var health := MAX_HEALTH
var max_health := MAX_HEALTH
var enabled := false
var state := "idle"
var phase := 1
var facing := -1
var player: Node2D
var current_attack := "sweep"
var state_time := 0.0
var state_remaining := 1.2
var attack_serial := 0
var attack_resolved := false
var attack_cycle := 0
var flash_time := 0.0
var visual_time := 0.0
var impact_time := 0.0
var rush_speed := 0.0
var last_attack_result := ""


func _ready() -> void:
	collision_layer = 4
	collision_mask = 1
	if get_node_or_null("BodyCollision") == null:
		var shape := CollisionShape2D.new()
		shape.name = "BodyCollision"
		var capsule := CapsuleShape2D.new()
		capsule.radius = 29.0
		capsule.height = 112.0
		shape.shape = capsule
		shape.position = Vector2(0, -56)
		add_child(shape)
	queue_redraw()


func set_player(value: Node2D) -> void:
	player = value


func set_enabled(value: bool) -> void:
	enabled = value
	if not value:
		velocity = Vector2.ZERO
		if state != "dead":
			state = "idle"


func reset_at(at: Vector2) -> void:
	position = at
	velocity = Vector2.ZERO
	health = max_health
	phase = 1
	facing = -1
	state = "idle"
	state_time = 0.0
	state_remaining = 1.2
	attack_serial = 0
	attack_resolved = false
	attack_cycle = 0
	flash_time = 0.0
	impact_time = 0.0
	last_attack_result = ""
	health_changed.emit(health, max_health)
	queue_redraw()


func _physics_process(delta: float) -> void:
	visual_time += delta
	flash_time = maxf(0.0, flash_time - delta)
	impact_time = maxf(0.0, impact_time - delta)
	if not enabled or state == "dead":
		queue_redraw()
		return
	state_time += delta
	state_remaining = maxf(0.0, state_remaining - delta)
	if not is_on_floor():
		velocity.y += GRAVITY * delta
	else:
		velocity.y = 0.0
	if not is_instance_valid(player) or player.health <= 0:
		velocity.x = 0.0
		queue_redraw()
		return
	match state:
		"idle":
			var gap := player.position.x - position.x
			if absf(gap) > 190.0:
				velocity.x = signf(gap) * (115.0 if phase == 1 else 150.0)
			else:
				velocity.x = move_toward(velocity.x, 0.0, 1600.0 * delta)
			if absf(gap) > 3.0:
				facing = 1 if gap > 0 else -1
			if state_remaining <= 0.0:
				choose_attack()
		"telegraph":
			velocity.x = 0.0
			if state_remaining <= 0.0:
				activate_attack()
		"sweep":
			velocity.x = 0.0
			if not attack_resolved and state_time >= 0.08:
				resolve_attack()
			if state_remaining <= 0.0:
				_recover(0.82 if phase == 1 else 0.67)
		"lunge":
			velocity.x = facing * rush_speed
			if not attack_resolved and _can_hit_player(85.0, 98.0, true):
				resolve_attack()
			if state_remaining <= 0.0:
				if not attack_resolved:
					resolve_attack()
				_recover(0.92 if phase == 1 else 0.72)
		"quake":
			velocity.x = 0.0
			# Airborne boss slams at a declared deadline; vertical player separation dodges.
			if not attack_resolved and state_time >= 0.52:
				position.y = 570.0
				velocity.y = 0.0
				impact_time = 0.6
				resolve_attack()
			if state_remaining <= 0.0:
				_recover(1.10 if phase == 1 else 0.90)
		"recovery", "stagger":
			velocity.x = move_toward(velocity.x, 0.0, 1800.0 * delta)
			if state_remaining <= 0.0:
				state = "idle"
				state_time = 0.0
				state_remaining = 0.5 if phase == 1 else 0.30
	move_and_slide()
	position.x = clampf(position.x, 130.0, 1150.0)
	queue_redraw()


func choose_attack() -> void:
	var sequence := ["sweep", "lunge", "quake", "sweep"]
	var next: String = sequence[attack_cycle % sequence.size()]
	attack_cycle += 1
	begin_attack(next)


func begin_attack(kind: String) -> bool:
	if not enabled or state == "dead" or kind not in ["sweep", "lunge", "quake"]:
		return false
	current_attack = kind
	state = "telegraph"
	state_time = 0.0
	state_remaining = 0.90 if kind == "quake" else (0.78 if kind == "sweep" else 0.70)
	if phase == 2:
		state_remaining *= 0.88
	attack_serial += 1
	attack_resolved = false
	if is_instance_valid(player):
		facing = 1 if player.position.x > position.x else -1
	action_event.emit("boss_telegraph", {"attack_id": attack_serial, "attack": kind, "parryable": kind != "quake", "phase": phase, "warning_seconds": state_remaining})
	return true


func activate_attack() -> void:
	if state != "telegraph":
		return
	state = current_attack
	state_time = 0.0
	match current_attack:
		"sweep":
			state_remaining = 0.28
		"lunge":
			state_remaining = 0.48 if phase == 1 else 0.52
			rush_speed = 510.0 if phase == 1 else 610.0
		"quake":
			state_remaining = 0.76
			velocity.y = -560.0
	action_event.emit("boss_attack", {"attack_id": attack_serial, "attack": current_attack, "phase": phase})


func resolve_attack() -> String:
	if attack_resolved or state not in ["sweep", "lunge", "quake"]:
		return "already_resolved"
	attack_resolved = true
	var can_hit := false
	if current_attack == "sweep":
		can_hit = _can_hit_player(180.0, 120.0, true)
	elif current_attack == "lunge":
		can_hit = _can_hit_player(85.0, 98.0, true)
	elif current_attack == "quake":
		can_hit = is_instance_valid(player) and absf(player.position.x - position.x) <= QUAKE_RANGE and player.position.y > 525.0
	last_attack_result = "missed"
	if can_hit:
		last_attack_result = player.receive_attack(1, current_attack != "quake", self, current_attack)
	action_event.emit("boss_attack_result", {"attack_id": attack_serial, "attack": current_attack, "result": last_attack_result})
	return last_attack_result


func _can_hit_player(reach: float, height: float, directional: bool) -> bool:
	if not is_instance_valid(player):
		return false
	var offset: Vector2 = player.position - position
	if absf(offset.y) > height:
		return false
	if directional:
		var forward := offset.x * facing
		return forward >= -35.0 and forward <= reach
	return absf(offset.x) <= reach


func take_damage(amount: int, _from_position: Vector2, kind: String = "light") -> bool:
	if not enabled or state == "dead" or health <= 0 or amount <= 0:
		return false
	health = maxi(0, health - amount)
	flash_time = 0.18
	health_changed.emit(health, max_health)
	action_event.emit("boss_damage", {"damage": amount, "health": health, "source": kind})
	if health == 0:
		state = "dead"
		velocity = Vector2.ZERO
		defeated.emit()
	elif phase == 1 and health <= max_health / 2:
		phase = 2
		action_event.emit("boss_phase", {"phase": phase, "health": health})
		# Phase change grants an opening; never surprise-hit during a transition.
		stagger(1.25)
	elif kind == "heavy_charged" and state in ["idle", "recovery"]:
		stagger(0.6)
	return true


func stagger(duration: float) -> void:
	if state == "dead":
		return
	state = "stagger"
	state_time = 0.0
	state_remaining = duration
	velocity.x = 0.0
	attack_resolved = true
	action_event.emit("boss_stagger", {"seconds": duration})


func _recover(duration: float) -> void:
	# A parry may have replaced attack state with stagger while resolving the hit.
	if state == "stagger" or state == "dead":
		return
	state = "recovery"
	state_time = 0.0
	state_remaining = duration
	velocity.x = 0.0


func get_status_text() -> String:
	if state == "telegraph":
		match current_attack:
			"sweep": return "SWEEP · parry when the blade flashes"
			"lunge": return "LUNGE · parry or dash through"
			"quake": return "QUAKE · jump, cannot parry"
	if state == "stagger":
		return "Staggered · strike now!"
	if state == "recovery":
		return "Recovery · strike now!"
	return ""


func _draw() -> void:
	var bark := Color("696953") if phase == 1 else Color("877349")
	var moss := Color("385a40") if phase == 1 else Color("567143")
	var amber := Color("f0c47a")
	if state == "dead":
		bark = Color("454d42")
		moss = Color("30453a")
	if flash_time > 0.0:
		bark = Color("e7dda9")
	var lean := facing * 5.0 if state == "lunge" else 0.0
	var sway := sin(visual_time * 2.2) * 2.0
	_draw_oval(Vector2(0, -3), Vector2(40, 7), Color(0, 0, 0, 0.24))
	draw_line(Vector2(-17, -27), Vector2(-23, 0), Color("354337"), 17.0)
	draw_line(Vector2(17, -27), Vector2(23, 0), Color("354337"), 17.0)
	draw_colored_polygon(PackedVector2Array([Vector2(-35 + lean, -100), Vector2(30 + lean, -100), Vector2(42 + sway, -30), Vector2(22, -13), Vector2(-31, -19), Vector2(-43, -46)]), bark)
	draw_colored_polygon(PackedVector2Array([Vector2(-41 + lean, -89), Vector2(-22 + lean, -115), Vector2(21 + lean, -108), Vector2(40 + lean, -88), Vector2(16, -74), Vector2(-20, -79)]), moss)
	draw_circle(Vector2(lean, -111), 22.0, bark)
	# Antler-like wooden branches distinguish the original guardian silhouette.
	for direction in [-1, 1]:
		draw_line(Vector2(direction * 14 + lean, -126), Vector2(direction * 35 + lean, -151), moss, 8.0)
		draw_line(Vector2(direction * 27 + lean, -141), Vector2(direction * 23 + lean, -160), moss, 5.0)
	draw_line(Vector2(facing * 6 + lean, -115), Vector2(facing * 16 + lean, -114), amber, 4.0)
	var hand := Vector2(facing * 33 + lean, -68)
	var blade_tip := Vector2(facing * 97 + lean, -97)
	if state == "telegraph":
		var blink := 0.65 + sin(visual_time * 15.0) * 0.25
		if current_attack == "quake":
			blade_tip = Vector2(facing * 37, -169)
			draw_arc(Vector2(0, -5), 75.0 + state_time * 40.0, PI, TAU, 24, Color(0.92, 0.46, 0.31, blink), 4.0)
		else:
			blade_tip = Vector2(-facing * 22, -151)
			draw_circle(Vector2(facing * 53, -87), 8.0, Color(0.95, 0.79, 0.45, blink))
			if state_remaining < 0.18:
				draw_arc(Vector2(facing * 53, -87), 17.0, 0.0, TAU, 20, Color("fff4c9"), 3.0)
	elif state == "sweep":
		blade_tip = Vector2(facing * 160, -44)
		draw_arc(Vector2(0, -60), 158.0, -0.7 if facing > 0 else PI - 0.7, 0.7 if facing > 0 else PI + 0.7, 28, Color(0.93, 0.72, 0.39, 0.65), 9.0)
	elif state == "lunge":
		blade_tip = Vector2(facing * 110, -61)
		draw_line(Vector2(-facing * 55, -59), Vector2(-facing * 125, -59), Color(0.9, 0.78, 0.53, 0.4), 7.0)
	draw_line(hand, blade_tip, Color("a99974"), 12.0)
	draw_line(hand, hand + (blade_tip - hand) * 0.27, Color("454b36"), 14.0)
	if state == "stagger":
		for index in range(3):
			var angle := visual_time * 3.0 + index * TAU / 3.0
			draw_circle(Vector2(cos(angle) * 32.0, -173 + sin(angle) * 9.0), 3.5, amber)
	if impact_time > 0.0:
		var radius := (0.6 - impact_time) * 650.0
		draw_line(Vector2(-radius, -4), Vector2(radius, -4), Color(0.98, 0.58, 0.32, impact_time), 5.0)
		for index in range(7):
			var x := (index - 3) * radius / 3.0
			draw_line(Vector2(x, -3), Vector2(x + 8, -22 * impact_time), Color(0.97, 0.62, 0.36, impact_time), 3.0)


func _draw_oval(center: Vector2, radii: Vector2, color: Color) -> void:
	var points := PackedVector2Array()
	for index in range(24):
		var angle := index * TAU / 24.0
		points.append(center + Vector2(cos(angle), sin(angle)) * radii)
	draw_colored_polygon(points, color)
