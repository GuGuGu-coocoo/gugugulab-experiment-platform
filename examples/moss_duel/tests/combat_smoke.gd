extends SceneTree
## Real Godot nodes and physics, plus deterministic resolution at attack boundaries.

const PlayerScript := preload("res://scripts/player.gd")
const BossScript := preload("res://scripts/boss.gd")
var failures: Array[String] = []
var checks := 0
var player: CharacterBody2D
var boss: CharacterBody2D
var deaths := 0
var player_deaths := 0


func _initialize() -> void:
	call_deferred("_run")


func _check(condition: bool, label: String) -> void:
	checks += 1
	if not condition:
		failures.append(label)
		push_error(label)


func _reset() -> void:
	player.reset_at(Vector2(400, 570))
	boss.reset_at(Vector2(480, 570))
	player.set_enabled(true)
	boss.set_enabled(true)
	player.set_physics_process(false)
	boss.set_physics_process(false)
	player.facing = 1
	boss.facing = -1


func _run() -> void:
	# The test runs directly, without the main scene that normally maps physical keys.
	for action in ["move_left", "move_right", "jump", "dash", "light_attack", "heavy_attack", "parry"]:
		if not InputMap.has_action(action):
			InputMap.add_action(action)
	var arena := Node2D.new()
	root.add_child(arena)
	var floor := StaticBody2D.new()
	floor.collision_layer = 1
	floor.collision_mask = 0
	var floor_shape := CollisionShape2D.new()
	var rectangle := RectangleShape2D.new()
	rectangle.size = Vector2(1400, 50)
	floor_shape.shape = rectangle
	floor_shape.position = Vector2(640, 595)
	floor.add_child(floor_shape)
	arena.add_child(floor)
	player = PlayerScript.new()
	boss = BossScript.new()
	arena.add_child(player)
	arena.add_child(boss)
	player.set_boss(boss)
	boss.set_player(player)
	boss.defeated.connect(func(): deaths += 1)
	player.defeated.connect(func(): player_deaths += 1)
	_reset()
	_check(player.begin_light_attack(), "light attack starts")
	_check(player.resolve_attack(), "light attack hits a forward target")
	_check(boss.health == boss.max_health - 1, "light attack deals one damage")
	_check(not player.resolve_attack() and boss.health == boss.max_health - 1, "one light swing cannot double hit")
	_reset()
	_check(player.begin_heavy_attack(0.0), "uncharged heavy starts")
	_check(player.resolve_attack() and boss.health == boss.max_health - 2, "uncharged heavy deals two damage")
	_reset()
	_check(player.begin_heavy_attack(1.0), "charged heavy starts")
	_check(player.resolve_attack() and boss.health == boss.max_health - 3, "charged heavy rewards holding with three damage")
	_check(not player.resolve_attack(), "one charged swing cannot double hit")
	_reset()
	player.facing = -1
	player.begin_light_attack()
	_check(not player.resolve_attack() and boss.health == boss.max_health, "attack cannot hit behind the player")
	_reset()
	boss.position.x = 850
	player.begin_heavy_attack(1.0)
	_check(not player.resolve_attack(), "heavy attack respects range")
	_reset()
	_check(player.begin_parry(), "parry starts")
	player.state_time = 0.12
	var result: String = player.receive_attack(1, true, boss, "sweep")
	_check(result == "parried", "parry succeeds inside 160 ms window")
	_check(player.health == 5 and boss.health == boss.max_health - 2 and boss.state == "stagger", "parry blocks damage, counters and staggers")
	_check(not player.begin_parry(), "parry cooldown prevents instant repeat")
	_reset()
	player.begin_parry()
	player.state_time = 0.18
	_check(player.receive_attack(1, true, boss) == "hit" and player.health == 4, "expired parry window takes damage")
	_reset()
	player.begin_parry()
	_check(player.receive_attack(1, false, boss, "quake") == "hit", "quake cannot be parried")
	_check(player.receive_attack(1, false, boss, "quake") == "immune" and player.health == 4, "damage grace prevents repeated frame damage")
	_reset()
	_check(player.begin_dash(), "dash starts")
	_check(player.receive_attack(1, true, boss) == "dodged" and player.health == 5, "dash evades incoming hit")
	player.state = "idle"
	_check(not player.begin_dash(), "dash cooldown prevents infinite chaining")
	_reset()
	boss.begin_attack("sweep")
	boss.activate_attack()
	_check(boss.resolve_attack() == "hit", "boss sweep hits nearby player")
	player.invulnerability = 0.0
	_check(boss.resolve_attack() == "already_resolved" and player.health == 4, "boss sweep cannot hit twice")
	_reset()
	player.position.y = 490
	boss.begin_attack("quake")
	boss.activate_attack()
	_check(boss.resolve_attack() == "missed" and player.health == 5, "jumping over quake avoids damage")
	_reset()
	boss.take_damage(14, player.position)
	_check(boss.phase == 2 and boss.state == "stagger", "half health starts phase two with safe transition opening")
	boss.take_damage(100, player.position)
	_check(boss.health == 0 and boss.state == "dead" and deaths == 1, "boss dies once")
	_check(not boss.take_damage(1, player.position) and deaths == 1, "dead boss cannot take more hits or re-emit defeat")
	_reset()
	for index in range(5):
		player.invulnerability = 0.0
		player.receive_attack(1, false, boss)
	_check(player.health == 0 and player.state == "dead" and player_deaths == 1, "player defeat fires once after five hits")
	_check(player.receive_attack(1, false, boss) == "inactive" and player_deaths == 1, "dead player does not accumulate damage")
	# Exercise real floor contact, input and gravity instead of replacing physics.
	_reset()
	boss.set_enabled(false)
	player.set_physics_process(true)
	for frame in range(8):
		await physics_frame
	_check(player.is_on_floor(), "real CharacterBody2D settles on arena floor")
	var initial_x := player.position.x
	Input.action_press("move_right")
	for frame in range(18):
		await physics_frame
	Input.action_release("move_right")
	_check(player.position.x > initial_x + 35.0, "physics movement responds to right input")
	Input.action_press("jump")
	await physics_frame
	await physics_frame
	Input.action_release("jump")
	_check(player.position.y < 570.0 and player.velocity.y < 0.0, "jump input lifts player off floor")
	for frame in range(70):
		await physics_frame
	_check(player.is_on_floor() and absf(player.position.y - 570.0) < 1.0, "player lands at original floor boundary")
	player.set_enabled(false)
	print("MOSS_DUEL_COMBAT: %d checks, %d failures" % [checks, failures.size()])
	quit(0 if failures.is_empty() else 1)
