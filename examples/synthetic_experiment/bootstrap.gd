extends Control
const DataModule = preload("res://data/experiment_data.gd")
const Task = preload("res://task.gd")
var data
var task
var message: Label
var start: Button
var code: LineEdit
var password: LineEdit
var recovery: LineEdit
var permit: LineEdit
var started_at := 0
var accepting := false
func _ready() -> void:
	var box = VBoxContainer.new();box.position = Vector2(70,70);box.size = Vector2(850,500);add_child(box)
	message = Label.new();message.text = "Synthetic experiment · two trials\nLocal save and server receipt are separate states.";box.add_child(message)
	code = LineEdit.new();code.placeholder_text = "Issued ID (leave empty for anonymous)";box.add_child(code)
	password = LineEdit.new();password.secret = true;password.placeholder_text = "Issued password (if required)";box.add_child(password)
	recovery = LineEdit.new();recovery.placeholder_text = "Researcher-authorized recovery: session UUID";box.add_child(recovery)
	permit = LineEdit.new();permit.secret = true;permit.placeholder_text = "One-time recovery permit";box.add_child(permit)
	start = Button.new();start.text = "Start synthetic participation";start.pressed.connect(begin);box.add_child(start)
	var backend: Node
	if OS.has_feature("web"):
		backend = load("res://addons/gec/web_backend.gd").new()
	elif OS.get_cmdline_user_args().has("--local"):
		backend = load("res://data/local_backend.gd").new()
	else:
		backend = load("res://addons/gec/native_backend.gd").new()
		var directory = OS.get_executable_path().get_base_dir()
		if directory.ends_with(".app/Contents/MacOS"): directory = directory.get_base_dir().get_base_dir().get_base_dir()
		var path = directory.path_join("connection.json")
		for arg in OS.get_cmdline_user_args():
			if arg.begins_with("--config="): path = arg.trim_prefix("--config=")
		if FileAccess.file_exists(path):
			var parsed = JSON.parse_string(FileAccess.get_file_as_string(path))
			if parsed is Dictionary: backend.config = parsed
	add_child(backend)
	if not OS.has_feature("web"):
		var prepared = backend.initialize_queue()
		if prepared.has("error"):
			message.text = prepared.error;start.disabled = true
			if OS.get_cmdline_user_args().has("--synthetic-auto"):
				print("SYNTHETIC_ERROR ",prepared.error);get_tree().quit(2)
			return
	data = DataModule.new(backend);task = Task.new(data)
	if OS.get_cmdline_user_args().has("--synthetic-auto"): call_deferred("auto_run")
func begin() -> void:
	start.disabled = true
	var credentials: Dictionary = {"expected_version":"synthetic-1"}
	if not code.text.is_empty(): credentials.participant_code = code.text
	if not password.text.is_empty(): credentials.password = password.text
	if not recovery.text.is_empty(): credentials = {"recovery_session":recovery.text,"permit":permit.text}
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--recover="): credentials.recovery_session = arg.trim_prefix("--recover=")
		if arg.begins_with("--permit="): credentials.permit = arg.trim_prefix("--permit=")
	var result = await data.prepare(credentials)
	password.text = ""
	if result.has("error"):
		message.text = "Cannot start: " + result.error
		print("SYNTHETIC_ERROR ",result.error)
		return
	if result.has("checkpoint"): task.restore(result.checkpoint)
	show_trial()
func show_trial() -> void:
	message.text = "Trial " + str(task.next_trial+1) + ": press LEFT or RIGHT"
	if OS.has_feature("web"):
		JavaScriptBridge.get_interface("document").getElementById("canvas").setAttribute("aria-label",message.text)
	started_at = Time.get_ticks_usec();accepting = true
func _input(event: InputEvent) -> void:
	if accepting and event is InputEventKey and event.pressed and not event.echo and event.keycode in [KEY_LEFT,KEY_RIGHT]:
		accepting = false
		var elapsed = float(Time.get_ticks_usec()-started_at)/1000.0
		respond("left" if event.keycode == KEY_LEFT else "right",elapsed)
func respond(choice: String,elapsed: float) -> void:
	var result = await task.response(choice,elapsed)
	if result.has("error"): message.text = "Save failed: "+result.error;return
	if task.next_trial < 2: show_trial()
	else:
		accepting = false
		result = await data.finish()
		message.text = "Task finished. " + JSON.stringify(result)
func _process(_delta: float) -> void:
	if data != null and task.next_trial == 2: message.text = "Task finished. Data state: " + JSON.stringify(data.status())
func auto_run() -> void:
	await begin()
	if not accepting: get_tree().quit(2);return
	accepting = false
	if task.next_trial == 0: await respond("left",321.5)
	if OS.get_cmdline_user_args().has("--stop-after-trial"):
		print("SYNTHETIC_BOUNDARY_SAVED")
		return
	accepting = false
	if task.next_trial == 1: await respond("right",217.25)
	for i in range(30):
		if data.status().state == "remote_acknowledged" or OS.get_cmdline_user_args().has("--local"):
			print("SYNTHETIC_DONE ",JSON.stringify(data.status()));get_tree().quit();return
		await get_tree().create_timer(1.0).timeout
	print("SYNTHETIC_TIMEOUT ",JSON.stringify(data.status()));get_tree().quit(3)
