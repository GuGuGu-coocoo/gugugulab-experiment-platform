extends Node
## Optional task-neutral participation helper. The host owns its scientific
## task, emitted values, checkpoints, random state and timing. Calls to record()
## only queue an in-process snapshot; local_saved reports the later commit.
signal local_saved(result: Dictionary)
signal failed(result: Dictionary)
signal completed(result: Dictionary)

const DataModule = preload("res://addons/gec/experiment_data.gd")
const Shell = preload("res://addons/gec/shell.gd")

var display_locale := ""
var backend: Node
var data = DataModule.new(null)
var shell
var retry_save: Button
var queue: Array[Dictionary] = []
var prepared := false
var active := false
var data_only := false
var draining := false
var stopped := false
var finish_requested := false
var finished := false
var _task: Node
var _on_start: Callable
var _on_retire: Callable
var _initial_mode: int
var _save_mode: int
var _paused_for_save := false
var _retired := false


func configure_defaults(config: Dictionary) -> Dictionary:
	return data.configure_defaults(config)


func prepare(program_version: String, task: Node, on_start: Callable, on_retire: Callable) -> Dictionary:
	if prepared:
		return {"error": "already_prepared"}
	if not is_inside_tree() or program_version.is_empty() or task == null or not on_start.is_valid() or not on_retire.is_valid():
		return {"error": "invalid_preparation"}
	prepared = true
	_task = task
	_on_start = on_start
	_on_retire = on_retire
	_initial_mode = task.process_mode
	task.process_mode = Node.PROCESS_MODE_DISABLED
	if backend == null:
		backend = _select_backend()
	if not backend.is_inside_tree():
		add_child(backend)
	data.backend = backend
	var surface := CanvasLayer.new()
	surface.layer = 50
	add_child(surface)
	shell = Shell.new()
	shell.program_version = program_version
	surface.add_child(shell)
	shell.session_started.connect(_on_admitted)
	shell.session_data_only.connect(_on_data_only)
	shell.local_save_retry_requested.connect(_retry_save)
	var result: Dictionary = shell.setup(backend)
	if not display_locale.is_empty():
		# Language is a display choice, never a frozen connection-policy edit.
		shell.set_locale(display_locale)
		if result.has("error"):
			shell.announce(shell.t("unsupported") if str(result.error) == "unsupported_configuration"
				else shell.t("error_prefix", {"code": str(result.error)}))
	retry_save = Button.new()
	retry_save.text = "重试本地保存" if shell.locale == "zh" else "Retry local save"
	retry_save.anchor_top = 1.0
	retry_save.anchor_bottom = 1.0
	retry_save.offset_left = 24
	retry_save.offset_right = 260
	retry_save.offset_top = -72
	retry_save.offset_bottom = -24
	retry_save.hide()
	retry_save.pressed.connect(_retry_save)
	surface.add_child(retry_save)
	return result


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
	if active or finished or data_only:
		return
	# No resume policy is declared by this convenience layer. A task developer
	# who implements checkpoints uses the existing low-level data contract.
	if checkpoint != null:
		_on_data_only()
		return
	active = true
	_task.process_mode = _initial_mode
	shell.set_overlay_visible(false)
	_on_start.call()


func _retire_task() -> void:
	_task.process_mode = Node.PROCESS_MODE_DISABLED
	if not _retired:
		_retired = true
		_on_retire.call()


func _on_data_only() -> void:
	active = false
	data_only = true
	shell.set_local_save_retry(false)
	_retire_task()
	shell.set_overlay_visible(true)
	shell.announce(shell.t("data_only"))


func record(payload: Dictionary, kind: String = "", schema: Dictionary = {}) -> Dictionary:
	if not active:
		return {"error": "not_active"}
	if finish_requested or finished:
		return {"error": "already_finished"}
	# Reuse the pinned data module's synchronous value validation and snapshot
	# preparation. This checks cycles before copying and resolves defaults now,
	# before an asynchronous drain or later producer mutation can change values.
	var snapshot: Dictionary = data._prepared(kind, payload, schema, {})
	if snapshot.has("error"):
		return snapshot
	queue.append({"record": snapshot, "buffered": false})
	_schedule_drain()
	return {"state": "queued", "queued": queue.size()}


func finish() -> Dictionary:
	if finished:
		return {"state": "finished"}
	if not active:
		return {"error": "not_active"}
	finish_requested = true
	_schedule_drain()
	return {"state": "finishing", "queued": queue.size()}


func status() -> Dictionary:
	var result: Dictionary = {"state": "unprepared"} if backend == null else data.status().duplicate(true)
	result["queued"] = queue.size()
	result["local_save_failed"] = stopped
	result["finish_requested"] = finish_requested
	result["data_only"] = data_only
	return result


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
			var value: Dictionary = next.record
			var recorded: Dictionary = await data.record(value.kind, value.payload, value.schema, value.observed)
			if recorded.has("error"):
				_save_failed(recorded)
				return
			next.buffered = true
			queue[0] = next
		var committed: Dictionary = await data.commit()
		if committed.has("error"):
			_save_failed(committed)
			return
		queue.pop_front()
		local_saved.emit(committed)
	if finish_requested:
		var result: Dictionary = await data.finish()
		if result.has("error"):
			_save_failed(result)
			return
		finished = true
		active = false
		shell.set_local_save_retry(false)
		_retire_task()
		shell.announce_finished(result)
		completed.emit(result)
	draining = false


func _save_failed(result: Dictionary) -> void:
	draining = false
	stopped = true
	if not _paused_for_save:
		_save_mode = _task.process_mode
		_paused_for_save = true
	_task.process_mode = Node.PROCESS_MODE_DISABLED
	shell.set_overlay_visible(true)
	var template := "本地保存失败：%s。请点击重试；尚未持久保存的进程内记录不能保证在关闭后保留。" if shell.locale == "zh" else "Local save failed: %s. Click Retry local save. In-process records not yet committed cannot be promised after closing."
	shell.announce(template % str(result.get("error", "storage_error")))
	shell.set_local_save_retry(true)
	# A visible Web DOM shell overlays the canvas. Its own retry control is the
	# reachable one; native retains the engine Button on the same surface.
	retry_save.visible = not OS.has_feature("web")
	failed.emit(result)


func _retry_save() -> void:
	if not stopped or draining:
		return
	stopped = false
	retry_save.hide()
	shell.set_local_save_retry(false)
	await _drain()
	if not stopped and not finish_requested:
		_task.process_mode = _save_mode
		_paused_for_save = false
		shell.set_overlay_visible(false)
