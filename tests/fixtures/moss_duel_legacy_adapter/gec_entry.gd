extends Node
## Optional assembly layer. Copy this kit into a separate Moss Duel project;
## the unmodified game does not import or call GEC.
const PROGRAM_VERSION := "moss-duel-1"
const EVENT_SCHEMA := {"id": "duel_event", "version": "1"}
const DataModule = preload("res://data/experiment_data.gd")
const Shell = preload("res://addons/gec/shell.gd")

@onready var game: Node2D = $Game
var backend: Node
var data
var shell
var retry_save: Button
var queue: Array[Dictionary] = []
var draining := false
var admitted := false
var finish_requested := false
var stopped := false
var finished := false


func _enter_tree() -> void:
	# Set before the game's _ready() so standalone entry/retry never opens an
	# unadmitted round. The game also declares host_controlled in this scene.
	get_node("Game").set_host_controlled(true)
	get_node("Game").process_mode = Node.PROCESS_MODE_DISABLED


func _ready() -> void:
	backend = _select_backend()
	add_child(backend)
	data = DataModule.new(backend)
	var surface := CanvasLayer.new()
	surface.layer = 50
	add_child(surface)
	shell = Shell.new()
	shell.program_version = PROGRAM_VERSION
	surface.add_child(shell)
	retry_save = Button.new()
	# The independent game's built-in font is Latin. Keep the assembly's
	# native controls readable without silently adding an unlicensed font.
	retry_save.text = "Retry local save"
	retry_save.position = Vector2(70, 640)
	retry_save.hide()
	retry_save.pressed.connect(_retry_save)
	surface.add_child(retry_save)
	game.telemetry_event.connect(_on_event)
	game.run_finished.connect(_on_finished)
	shell.session_started.connect(_on_admitted)
	shell.session_data_only.connect(_on_data_only)
	var prepared: Dictionary = shell.setup(backend)
	if not OS.has_feature("web"):
		_apply_native_language(prepared)
	if prepared.has("error"):
		game.hide()


func _apply_native_language(prepared: Dictionary) -> void:
	# Failures need the same readable display as successful initialization. The
	# game's font is Latin; changing the display never changes frozen config,
	# setup state or disabled controls. set_locale first redraws normal status,
	# so restore the actual failure announcement through the public shell API.
	shell.set_locale("en")
	if prepared.has("error"):
		if str(prepared.error) == "unsupported_configuration":
			shell.announce(shell.t("unsupported"))
		else:
			shell.announce(shell.t("error_prefix", {"code": str(prepared.error)}))


func _select_backend() -> Node:
	if OS.has_feature("web"):
		return load("res://addons/gec/web_backend.gd").new()
	var selected: Node = load("res://addons/gec/native_backend.gd").new()
	var directory := OS.get_executable_path().get_base_dir()
	if OS.has_feature("editor"):
		directory = "res://"
	elif directory.ends_with(".app/Contents/MacOS"):
		directory = directory.get_base_dir().get_base_dir().get_base_dir()
	var path := directory.path_join("connection.json")
	if FileAccess.file_exists(path):
		var document = JSON.parse_string(FileAccess.get_file_as_string(path))
		if document is Dictionary:
			selected.config = document
	return selected


func _on_admitted(checkpoint) -> void:
	# This game intentionally does not declare a resumable battle checkpoint.
	# Never guess a restored HP, random state, unfinished hit or reaction time.
	if checkpoint != null:
		_on_data_only()
		return
	admitted = true
	game.process_mode = Node.PROCESS_MODE_INHERIT
	shell.set_overlay_visible(false)
	game.show()
	game.start_round()


func _on_data_only() -> void:
	admitted = false
	# CanvasLayer does not inherit a Node2D parent's visibility. Retire the
	# game's own HUD and overlay as well as hiding its world.
	game.set_host_controlled(true)
	game.process_mode = Node.PROCESS_MODE_DISABLED
	game.hide()
	shell.set_overlay_visible(true)
	shell.announce("Only previously saved data is being uploaded. This interrupted fight will not resume or restart. Reopen the participation entry for a new round.")


func _on_event(kind: String, payload: Dictionary) -> void:
	if not admitted or finish_requested or finished:
		return
	# The deep copy happens synchronously in the signal callback. Only these
	# explicit game signals are sampled; credentials and scene contents are not.
	queue.append({"payload": {"kind": kind, "data": payload.duplicate(true)}, "buffered": false})
	_schedule_drain()


func _on_finished(summary: Dictionary) -> void:
	if not admitted or finish_requested or finished:
		return
	finish_requested = true
	queue.append({"payload": {"kind": "round_summary", "data": summary.duplicate(true)}, "buffered": false})
	_schedule_drain()


func _schedule_drain() -> void:
	if not draining and not stopped:
		call_deferred("_drain")


func _drain() -> void:
	if draining or stopped or finished:
		return
	draining = true
	while not queue.is_empty():
		var next: Dictionary = queue[0]
		if not bool(next.buffered):
			var recorded: Dictionary = await data.record("game.duel", next.payload, EVENT_SCHEMA)
			if recorded.has("error"):
				_save_failed(recorded)
				return
			# Retry a failed commit using the existing event in the backend buffer;
			# do not generate a second identity by calling record() again.
			next.buffered = true
			queue[0] = next
		var committed: Dictionary = await data.commit()
		if committed.has("error"):
			_save_failed(committed)
			return
		queue.pop_front()
	if finish_requested:
		var result: Dictionary = await data.finish()
		if result.has("error"):
			_save_failed(result)
			return
		finished = true
		admitted = false
		game.set_host_controlled(true)
		game.process_mode = Node.PROCESS_MODE_DISABLED
		game.hide()
		shell.set_overlay_visible(true)
		shell.announce_finished(result)
	draining = false


func _save_failed(result: Dictionary) -> void:
	draining = false
	stopped = true
	game.process_mode = Node.PROCESS_MODE_DISABLED
	shell.set_overlay_visible(true)
	shell.announce("Local save failed: %s. This process keeps its queue; click Retry local save. Records not yet committed cannot be promised after closing." % str(result.get("error", "storage_error")))
	retry_save.show()


func _retry_save() -> void:
	if not stopped or draining:
		return
	stopped = false
	retry_save.hide()
	await _drain()
	if not stopped and not finish_requested:
		game.process_mode = Node.PROCESS_MODE_INHERIT
		shell.set_overlay_visible(false)
