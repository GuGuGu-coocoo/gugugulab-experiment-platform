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
	# Keep the two-line status region stable when trial/error text uses one line.
	message.custom_minimum_size.y = message.get_minimum_size().y
	code = LineEdit.new();code.placeholder_text = "Issued ID (leave empty for anonymous)";box.add_child(code)
	password = LineEdit.new();password.secret = true;password.placeholder_text = "Issued password (if required)";box.add_child(password)
	recovery = LineEdit.new();recovery.placeholder_text = "Researcher-authorized recovery: session UUID";box.add_child(recovery)
	permit = LineEdit.new();permit.secret = true;permit.placeholder_text = "One-time recovery permit";box.add_child(permit)
	start = Button.new();start.text = "Start synthetic participation";start.pressed.connect(begin);box.add_child(start)
	var export_button = Button.new();export_button.text = "Export current recovery data";export_button.pressed.connect(export_recovery);box.add_child(export_button)
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
	if OS.has_feature("web"): call_deferred("mount_web_inputs")
	if OS.get_cmdline_user_args().has("--synthetic-auto"): call_deferred("auto_run")
func mount_web_inputs() -> void:
	await get_tree().process_frame
	var fields: Array = []
	var controls = {"code":code,"password":password,"recovery":recovery,"permit":permit}
	for name in controls:
		var control = controls[name]
		var rect = control.get_global_rect()
		fields.append({"name":name,"label":control.placeholder_text,"secret":control.secret,"x":rect.position.x,"y":rect.position.y,"width":rect.size.x,"height":rect.size.y})
		control.modulate.a = 0
		control.mouse_filter = Control.MOUSE_FILTER_IGNORE
		control.focus_mode = Control.FOCUS_NONE
	var viewport = get_viewport_rect().size
	JavaScriptBridge.get_interface("GECBridge").mount_inputs(JSON.stringify({"fields":fields,"width":viewport.x,"height":viewport.y}))
func begin() -> void:
	start.disabled = true
	if OS.has_feature("web"):
		var values = JSON.parse_string(JavaScriptBridge.get_interface("GECBridge").inputs_json())
		code.text = values.code;password.text = values.password;recovery.text = values.recovery;permit.text = values.permit
		JavaScriptBridge.get_interface("GECBridge").clear_input_secrets()
	var credentials: Dictionary = {"expected_version":"synthetic-1"}
	if not code.text.is_empty(): credentials.participant_code = code.text
	if not password.text.is_empty(): credentials.password = password.text
	if not recovery.text.is_empty(): credentials = {"recovery_session":recovery.text,"permit":permit.text}
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--recover="): credentials.recovery_session = arg.trim_prefix("--recover=")
		if arg.begins_with("--permit="): credentials.permit = arg.trim_prefix("--permit=")
	var result = await data.prepare(credentials)
	password.text = "";permit.text = ""
	if result.has("error"):
		start.disabled = false
		message.text = "Cannot start: " + result.error
		print("SYNTHETIC_ERROR ",result.error)
		return
	if result.get("state") == "data_only":
		message.text = "Data recovered. No compatible task recovery policy; contact the researcher."
		return
	if result.get("checkpoint") != null: task.restore(result.checkpoint)
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

func export_recovery() -> void:
	if data == null: return
	if OS.has_feature("web"):
		var result = await data.backend.download_recovery()
		if result.has("error"): message.text = result.error
		return
	var recovery_data = await data.recovery_export()
	if recovery_data.has("error"): message.text = recovery_data.error;return
	var dialog = FileDialog.new();dialog.file_mode = FileDialog.FILE_MODE_SAVE_FILE;dialog.access = FileDialog.ACCESS_FILESYSTEM;dialog.use_native_dialog = true;dialog.current_file = "recovery.json";dialog.filters = PackedStringArray(["*.json ; Recovery data"])
	add_child(dialog)
	dialog.file_selected.connect(func(path):
		var file = FileAccess.open(path,FileAccess.WRITE)
		if file == null: message.text = "Recovery export could not be saved";return
		file.store_string(JSON.stringify(recovery_data));file.close()
	)
	dialog.popup_centered(Vector2i(800,500))
