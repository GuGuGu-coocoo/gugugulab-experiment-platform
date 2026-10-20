extends Node
const GEC = preload("res://addons/gec/session.gd")
var gec
var record_failed := false
@onready var game = $Game

func _enter_tree() -> void:
	$Game.set_host_controlled(true)
	$Game.process_mode = Node.PROCESS_MODE_DISABLED

func _ready() -> void:
	gec = GEC.new()
	add_child(gec)
	gec.configure_defaults({"event_type": "game.duel", "schema": {"id": "duel_event", "version": "1"}})
	game.telemetry_event.connect(_record)
	game.run_finished.connect(_finished)
	gec.prepare("moss-duel-3", game, _start, _retire)

func _start() -> void:
	game.process_mode = Node.PROCESS_MODE_INHERIT
	game.show()
	game.start_round()

func _record(kind: String, payload: Dictionary) -> void:
	if record_failed:
		return
	var result: Dictionary = gec.record({"kind": kind, "data": payload})
	if result.has("error"):
		record_failed = true
		game.process_mode = Node.PROCESS_MODE_DISABLED
		gec.shell.set_overlay_visible(true)
		gec.shell.announce("Game event rejected: " + str(result.error))

func _finished(summary: Dictionary) -> void:
	_record("round_summary", summary)
	if not record_failed:
		gec.finish()

func _retire() -> void:
	game.set_host_controlled(true)
	game.hide()
