extends CharacterBody2D
## Scientific/gameplay code remains independent of any transport or platform SDK.

signal health_changed(current: int, maximum: int)
signal defeated
signal action_event(kind: String, payload: Dictionary)

const MAX_HEALTH := 5
const MOVE_SPEED := 265.0
const JUMP_SPEED := -620.0
const GRAVITY := 1680.0
const DASH_SPEED := 720.0
const DASH_DURATION := 0.17
const DASH_COOLDOWN := 0.72
const PARRY_WINDOW := 0.16
const PARRY_RECOVERY := 0.34
const PARRY_COOLDOWN := 0.62
const DAMAGE_GRACE := 0.85

var health := MAX_HEALTH
var max_health := MAX_HEALTH
var enabled := false
var state := "idle"
var facing := 1
var boss: Node2D
var last_feedback := ""
var state_time := 0.0
var state_remaining := 0.0
var dash_cooldown := 0.0
var parry_cooldown := 0.0
var invulnerability := 0.0
var charge_time := 0.0
var attack_damage := 1
var attack_range := 105.0
var attack_hit_time := 0.10
var attack_resolved := false
var attack_serial := 0
var attack_kind := "light"
var air_dash_used := false
var flash_time := 0.0
var feedback_time := 0.0
var visual_time := 0.0
var last_jump_time := 0.0


func _ready() -> void:
	collision_layer = 2
	collision_mask = 1
	if get_node_or_null("BodyCollision") == null:
		var shape := CollisionShape2D.new()
		shape.name = "BodyCollision"
		var capsule := CapsuleShape2D.new()
		capsule.radius = 15.0
		capsule.height = 62.0
		shape.shape = capsule
		shape.position = Vector2(0, -31)
		add_child(shape)
	queue_redraw()


func set_boss(value: Node2D) -> void:
	boss = value


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
	state = "idle"
	state_time = 0.0
	state_remaining = 0.0
	dash_cooldown = 0.0
	parry_cooldown = 0.0
	invulnerability = 0.0
	charge_time = 0.0
	attack_resolved = false
	air_dash_used = false
	flash_time = 0.0
	last_feedback = ""
	facing = 1
	health_changed.emit(health, max_health)
	queue_redraw()


func _physics_process(delta: float) -> void:
	visual_time += delta
	flash_time = maxf(0.0, flash_time - delta)
	feedback_time = maxf(0.0, feedback_time - delta)
	dash_cooldown = maxf(0.0, dash_cooldown - delta)
	parry_cooldown = maxf(0.0, parry_cooldown - delta)
	invulnerability = maxf(0.0, invulnerability - delta)
	if not enabled or state == "dead":
		queue_redraw()
		return
	state_time += delta
	state_remaining = maxf(0.0, state_remaining - delta)
	if is_on_floor():
		air_dash_used = false
	else:
		velocity.y += GRAVITY * delta
	_handle_input()
	if state == "charge":
		charge_time = minf(1.35, charge_time + delta)
	elif state == "attack":
		if not attack_resolved and state_time >= attack_hit_time:
			resolve_attack()
		if state_remaining <= 0.0:
			state = "idle"
	elif state == "dash":
		velocity.x = facing * DASH_SPEED
		velocity.y = 0.0
		if state_remaining <= 0.0:
			state = "idle"
	elif state == "parry" or state == "stagger":
		if state_remaining <= 0.0:
			state = "idle"
	if state != "dash":
		var input_axis := Input.get_axis("move_left", "move_right")
		var movement_scale := 1.0
		if state == "attack":
			movement_scale = 0.30
		elif state == "charge":
			movement_scale = 0.38
		elif state == "parry" or state == "stagger":
			movement_scale = 0.0
		velocity.x = move_toward(velocity.x, input_axis * MOVE_SPEED * movement_scale, 1900.0 * delta)
		if not is_zero_approx(input_axis) and state == "idle":
			facing = 1 if input_axis > 0 else -1
	move_and_slide()
	position.x = clampf(position.x, 95.0, 1185.0)
	queue_redraw()


func _handle_input() -> void:
	if state == "charge":
		# A key may be released while combat is paused. Check its current state,
		# so the next physics frame still releases the charge after resuming.
		if not Input.is_action_pressed("heavy_attack"):
			begin_heavy_attack(charge_time)
		return
	if state != "idle":
		return
	if Input.is_action_just_pressed("jump") and is_on_floor():
		velocity.y = JUMP_SPEED
		last_jump_time = visual_time
		action_event.emit("jump", {"position": _position_data()})
	if Input.is_action_just_pressed("dash") and dash_cooldown <= 0.0 and not air_dash_used:
		begin_dash()
	elif Input.is_action_just_pressed("parry") and parry_cooldown <= 0.0:
		begin_parry()
	elif Input.is_action_just_pressed("light_attack"):
		begin_light_attack()
	elif Input.is_action_just_pressed("heavy_attack"):
		state = "charge"
		state_time = 0.0
		charge_time = 0.0
		action_event.emit("heavy_charge", {})


func begin_dash() -> bool:
	if not enabled or state != "idle" or dash_cooldown > 0.0 or air_dash_used:
		return false
	state = "dash"
	state_time = 0.0
	state_remaining = DASH_DURATION
	dash_cooldown = DASH_COOLDOWN
	invulnerability = maxf(invulnerability, DASH_DURATION)
	if not is_on_floor():
		air_dash_used = true
	action_event.emit("dash", {"position": _position_data(), "direction": facing})
	return true


func begin_parry() -> bool:
	if not enabled or state != "idle" or parry_cooldown > 0.0:
		return false
	state = "parry"
	state_time = 0.0
	state_remaining = PARRY_RECOVERY
	parry_cooldown = PARRY_COOLDOWN
	action_event.emit("parry_attempt", {"window_ms": int(PARRY_WINDOW * 1000.0)})
	return true


func begin_light_attack() -> bool:
	if not enabled or state != "idle":
		return false
	_start_attack("light", 1, 110.0, 0.10, 0.29)
	return true


func begin_heavy_attack(charged_seconds: float = 0.0) -> bool:
	if not enabled or (state != "idle" and state != "charge"):
		return false
	charge_time = clampf(charged_seconds, 0.0, 1.35)
	var fully_charged := charge_time >= 0.8
	_start_attack("heavy_charged" if fully_charged else "heavy", 3 if fully_charged else 2, 140.0, 0.18, 0.52)
	return true


func _start_attack(kind: String, damage: int, reach: float, hit_at: float, duration: float) -> void:
	state = "attack"
	state_time = 0.0
	state_remaining = duration
	attack_kind = kind
	attack_damage = damage
	attack_range = reach
	attack_hit_time = hit_at
	attack_resolved = false
	attack_serial += 1
	action_event.emit(kind + "_attack", {"attack_id": attack_serial, "charge_seconds": charge_time, "damage": damage})


func resolve_attack() -> bool:
	if state != "attack" or attack_resolved:
		return false
	attack_resolved = true
	if not is_instance_valid(boss) or boss.health <= 0:
		return false
	var offset: Vector2 = boss.position - position
	var forward: float = offset.x * facing
	if forward < -20.0 or forward > attack_range + 33.0 or absf(offset.y) > 115.0:
		action_event.emit("attack_missed", {"attack_id": attack_serial, "kind": attack_kind})
		return false
	var hit: bool = boss.take_damage(attack_damage, position, attack_kind)
	if hit:
		_feedback("Charged strike!" if attack_kind == "heavy_charged" else "Hit!", 0.4)
		action_event.emit("attack_hit", {"attack_id": attack_serial, "kind": attack_kind, "damage": attack_damage})
	return hit


func receive_attack(damage: int, can_parry: bool, attacker: Node, kind: String = "sweep") -> String:
	if not enabled or state == "dead":
		return "inactive"
	# A successful parry is decided before damage grace, but cannot parry a quake.
	if can_parry and state == "parry" and state_time <= PARRY_WINDOW:
		state = "idle"
		state_remaining = 0.0
		invulnerability = maxf(invulnerability, 0.30)
		flash_time = 0.24
		_feedback("PARRY!", 0.8)
		if is_instance_valid(attacker):
			attacker.stagger(1.05)
			attacker.take_damage(2, position, "parry")
		action_event.emit("parry_success", {"attack": kind, "counter_damage": 2})
		return "parried"
	if state == "dash":
		action_event.emit("attack_evaded", {"attack": kind})
		return "dodged"
	if invulnerability > 0.0:
		return "immune"
	health = maxi(0, health - maxi(0, damage))
	invulnerability = DAMAGE_GRACE
	flash_time = 0.30
	state = "stagger"
	state_time = 0.0
	state_remaining = 0.20
	velocity.y = minf(velocity.y, -160.0)
	if is_instance_valid(attacker):
		velocity.x = 190.0 * signf(position.x - attacker.position.x)
	_feedback("Jump over the quake" if kind == "quake" else "Too late — watch the amber warning", 1.1)
	health_changed.emit(health, max_health)
	action_event.emit("damage_taken", {"damage": damage, "health": health, "attack": kind})
	if health == 0:
		state = "dead"
		velocity = Vector2.ZERO
		defeated.emit()
	return "hit"


func get_status_text() -> String:
	if feedback_time > 0.0:
		return last_feedback
	if state == "charge":
		return "Charged! Release heavy attack" if charge_time >= 0.8 else "Hold heavy attack to charge"
	if state == "parry":
		return "Parry window" if state_time <= PARRY_WINDOW else "Parry recovery"
	return ""


func _feedback(message: String, duration: float) -> void:
	last_feedback = message
	feedback_time = duration


func _position_data() -> Dictionary:
	return {"x": position.x, "y": position.y}


func _draw() -> void:
	var body := Color("d3dfbd")
	var cloak := Color("42715c")
	if invulnerability > 0.0 and int(visual_time * 20.0) % 2 == 0:
		body.a = 0.4
		cloak.a = 0.4
	if state == "dead":
		body = Color("738678")
		cloak = Color("344c42")
	var sway := sin(visual_time * 5.0) * 2.0
	if state == "dash":
		for index in range(3):
			draw_line(Vector2(-facing * (25 + index * 12), -25), Vector2(-facing * (60 + index * 13), -25), Color(0.7, 0.9, 0.76, 0.22), 8.0)
	# Original leaf-cloak duelist silhouette; feet are the collision origin.
	draw_colored_polygon(PackedVector2Array([Vector2(-18, -42), Vector2(16, -43), Vector2(23 + sway, -4), Vector2(0, -9), Vector2(-23, -2)]), cloak)
	draw_line(Vector2(-7, -12), Vector2(-8, 0), Color("263c35"), 7.0)
	draw_line(Vector2(7, -12), Vector2(9, 0), Color("263c35"), 7.0)
	draw_circle(Vector2(0, -54), 15.0, body)
	draw_colored_polygon(PackedVector2Array([Vector2(-11, -65), Vector2(-20, -79), Vector2(-4, -70)]), body)
	draw_colored_polygon(PackedVector2Array([Vector2(7, -66), Vector2(20, -80), Vector2(13, -61)]), body)
	draw_line(Vector2(facing * 4, -57), Vector2(facing * 11, -58), Color("1b3630"), 3.0)
	var weapon_start := Vector2(facing * 16, -32)
	var weapon_end := Vector2(facing * 47, -44)
	if state == "charge":
		weapon_end = Vector2(-facing * 4, -90)
		var glow := minf(1.0, charge_time / 0.8)
		draw_arc(Vector2.ZERO + Vector2(0, -34), 38.0 + glow * 7.0, 0.0, TAU * glow, 28, Color("f4d89a"), 3.0)
	elif state == "parry":
		weapon_end = Vector2(facing * 25, -76)
		var active := state_time <= PARRY_WINDOW
		draw_arc(Vector2(0, -36), 44.0, -1.4 if facing > 0 else 1.74, 1.4 if facing > 0 else 4.54, 24, Color("c8f3d0") if active else Color("657c66"), 5.0 if active else 2.0)
	elif state == "attack":
		var progress := clampf(state_time / 0.26, 0.0, 1.0)
		var angle := lerpf(-1.1, 1.0, progress)
		weapon_end = weapon_start + Vector2(facing * cos(angle), sin(angle)) * 62.0
		var arc_color := Color("f3d59b") if attack_kind.begins_with("heavy") else Color("cee9d1")
		arc_color.a = 0.65 * (1.0 - progress)
		var arc_start := -0.95 if facing > 0 else PI - 0.95
		draw_arc(Vector2(0, -34), attack_range - 8.0, arc_start, arc_start + 1.9, 24, arc_color, 6.0 if attack_kind.begins_with("heavy") else 3.0)
	draw_line(weapon_start, weapon_end, Color("b8c6b7"), 5.0)
	draw_line(weapon_start, weapon_start + (weapon_end - weapon_start) * 0.24, Color("947859"), 7.0)
	if flash_time > 0.0:
		draw_arc(Vector2(0, -36), 40.0 + (0.30 - flash_time) * 120.0, 0.0, TAU, 30, Color(0.9, 1.0, 0.8, flash_time * 2.0), 3.0)
