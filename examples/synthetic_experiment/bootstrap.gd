extends Control
## Assembly only: choose the platform backend, build the science task and hand
## the generic entry/recovery/finish surface to the reusable GEC shell. This
## scene owns the synthetic task lifecycle (trial prompts, response capture and
## the finish call); it does not own credential or status forms.
const Shell = preload("res://addons/gec/shell.gd")
const DataModule = preload("res://data/experiment_data.gd")
const Task = preload("res://task.gd")
var shell
var data
var task
var started_at := 0
var accepting := false
var data_only := false

# Read-only accessors kept for the existing native layout/input probes.
var message: Label:
	get: return shell.message if shell != null else null
var code: LineEdit:
	get: return shell.code if shell != null else null
var password: LineEdit:
	get: return shell.password if shell != null else null
var recovery: LineEdit:
	get: return shell.recovery if shell != null else null
var permit: LineEdit:
	get: return shell.permit if shell != null else null
var start: Button:
	get: return shell.start if shell != null else null


func _ready() -> void:
	var backend := _select_backend()
	add_child(backend)
	data = DataModule.new(backend)
	task = Task.new(data)
	shell = Shell.new()
	shell.name = "GECShell"
	add_child(shell)
	shell.session_started.connect(_on_session_started)
	shell.session_data_only.connect(_on_session_data_only)
	var prepared: Dictionary = shell.setup(backend)
	if prepared.has("error"):
		if OS.get_cmdline_user_args().has("--synthetic-auto"):
			get_tree().quit(2)
		return
	if OS.get_cmdline_user_args().has("--synthetic-auto"):
		call_deferred("auto_run")


func _select_backend() -> Node:
	if OS.has_feature("web"):
		return load("res://addons/gec/web_backend.gd").new()
	if OS.get_cmdline_user_args().has("--local"):
		return load("res://data/local_backend.gd").new()
	var backend = load("res://addons/gec/native_backend.gd").new()
	var directory = OS.get_executable_path().get_base_dir()
	if directory.ends_with(".app/Contents/MacOS"):
		directory = directory.get_base_dir().get_base_dir().get_base_dir()
	var path = directory.path_join("connection.json")
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--config="):
			path = arg.trim_prefix("--config=")
	if FileAccess.file_exists(path):
		var parsed = JSON.parse_string(FileAccess.get_file_as_string(path))
		if parsed is Dictionary:
			backend.config = parsed
	return backend


func _on_session_started(checkpoint) -> void:
	if checkpoint != null:
		task.restore(checkpoint)
	show_trial()


func _on_session_data_only() -> void:
	data_only = true


func show_trial() -> void:
	shell.announce(shell.t("trial", {"n": task.next_trial + 1}))
	started_at = Time.get_ticks_usec()
	accepting = true


func _input(event: InputEvent) -> void:
	if accepting and event is InputEventKey and event.pressed and not event.echo and event.keycode in [KEY_LEFT, KEY_RIGHT]:
		accepting = false
		var elapsed = float(Time.get_ticks_usec() - started_at) / 1000.0
		respond("left" if event.keycode == KEY_LEFT else "right", elapsed)


func respond(choice: String, elapsed: float) -> void:
	var result = await task.response(choice, elapsed)
	if result.has("error"):
		shell.announce(shell.t("save_failed", {"code": str(result.error)}))
		return
	if task.next_trial < 2:
		show_trial()
	else:
		accepting = false
		result = await data.finish()
		shell.announce_finished(result)


func auto_run() -> void:
	var credentials := {}
	var auto_new := false
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--recover="):
			credentials.recovery_session = arg.trim_prefix("--recover=")
		if arg.begins_with("--permit="):
			credentials.permit = arg.trim_prefix("--permit=")
		if arg.begins_with("--participant-code="):
			credentials.code = arg.trim_prefix("--participant-code=")
		if arg.begins_with("--password="):
			credentials.password = arg.trim_prefix("--password=")
		if arg.begins_with("--short-code="):
			credentials.short_code = arg.trim_prefix("--short-code=")
		if arg == "--auto-new-session":
			auto_new = true
	await shell.auto_submit(credentials, auto_new)
	if not accepting:
		if data_only and OS.get_cmdline_user_args().has("--await-upload"):
			await await_data_only_upload()
			return
		get_tree().quit(2)
		return
	accepting = false
	if task.next_trial == 0:
		await respond("left", 321.5)
	if OS.get_cmdline_user_args().has("--stop-after-trial"):
		print("SYNTHETIC_BOUNDARY_SAVED")
		return
	accepting = false
	if task.next_trial == 1:
		await respond("right", 217.25)
	for i in range(30):
		if data.status().state == "remote_acknowledged" or OS.get_cmdline_user_args().has("--local"):
			print("SYNTHETIC_DONE ", JSON.stringify(data.status()))
			get_tree().quit()
			return
		await get_tree().create_timer(1.0).timeout
	print("SYNTHETIC_TIMEOUT ", JSON.stringify(data.status()))
	get_tree().quit(3)


func await_data_only_upload() -> void:
	## Data-only continuation: no trial runs. Wait until the retained pending
	## records and the declared completion are acknowledged, then stop.
	for _i in range(60):
		if data.status().state == "remote_acknowledged":
			print("SYNTHETIC_DATA_ONLY_DONE ", JSON.stringify(data.status()))
			get_tree().quit()
			return
		await get_tree().create_timer(1.0).timeout
	print("SYNTHETIC_TIMEOUT ", JSON.stringify(data.status()))
	get_tree().quit(3)
