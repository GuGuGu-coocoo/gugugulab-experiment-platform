extends Control
## Reusable GEC participation shell: entry, same-device recovery and the generic
## local/remote/finish status surface for native and Web builds.
##
## Responsibilities are deliberately narrow. The shell owns credential/status
## forms, recovery candidate discovery and explicit continuation, localized
## public strings and the finish announcement. It never records trial responses,
## never changes the science task's timing, order or random sequence, and never
## scores or rewrites checkpoints: the assembled experiment keeps those.
##
## The frozen public configuration decides the entry contract. A release with a
## frozen ``mode`` and ``shell_capability`` renders exactly the fields that mode
## requires; a legacy release without them keeps the pre-shell field set; an
## unknown capability or mode fails closed instead of guessing a protocol.

signal session_started(checkpoint)
signal session_data_only()

const SHELL_CAPABILITY := "gec-shell/v1"
const LOCALIZED := {
	"zh": {
		"title": "合成实验参与",
		"status": "本地保存与服务器回执是两种状态。",
		"anonymous": "本研究无需 ID。",
		"code": "名单 ID（可留空表示匿名）",
		"password": "名单密码",
		"recovery_legend": "同设备恢复",
		"short_code": "六位恢复码（研究者签发）",
		"recover": "恢复",
		"advanced": "高级：长许可（兼容路径）",
		"session": "会话 UUID",
		"permit": "一次性恢复许可",
		"recover_permit": "使用许可恢复",
		"start": "开始参与",
		"export": "导出当前恢复数据",
		"continue_": "继续上次参与",
		"start_new": "开始新的参与",
		"cancel": "取消",
		"confirm_continue": "已找到本机未完成的会话。是否继续上次参与？",
		"confirm_data_only": "已找到本机会话；该会话已声明结束，只能恢复数据。是否继续？",
		"cleaned": "本机此前的数据已上传，无需恢复。",
		"ambiguous": "本机存在多个匹配会话，请联系研究人员处理；未继续任何会话。",
		"front_locked": "本机会话已被更新的参与锁定；请使用研究者签发的六位码或长许可。",
		"code_denied": "恢复码无效、已过期或本机证明不匹配；未恢复任何会话。",
		"invalid_code": "请输入六位数字恢复码。",
		"legacy_fields": "长许可恢复需要同时填写会话 UUID 与许可。",
		"recovery_denied": "凭据或本机证明不匹配；未恢复任何会话。",
		"trial": "第 {n} 次试次：按左或右方向键",
		"save_failed": "保存失败：{code}",
		"finished_local": "任务已完成；本地记录已提交，等待上传确认。",
		"uploaded": "任务已完成；数据已上传并确认，本地只保留已清理标记。",
		"data_only": "仅恢复数据：不会再进行试次。",
		"error_prefix": "无法开始：{code}",
		"retry": "服务器暂时不可用，将按退避自动重试；本地记录不会丢失。",
		"unsupported": "发行配置的参与能力无法识别，已停止以避免误接。",
		"no_server": "本地运行模式：不连接服务器。",
		"bridge_unavailable": "参与界面未能连接到浏览器桥接。",
		"err_admission_denied": "名单 ID 或密码不正确，或该名单不可用。",
		"err_participation_limit": "该名单 ID 已达到参与次数上限。",
		"err_admission_closed": "该研究当前未开放新参与。",
		"err_wrong_program_version": "参与包版本与发行不一致，请联系研究人员。",
		"err_stale_entry": "研究入口已更新，请返回入口刷新后重试。",
		"err_entry_closed": "该研究当前没有可用的参与版本。",
		"err_unsupported_capability": "服务器不支持该恢复能力，请联系研究人员。",
		"err_not_recoverable": "本机没有可恢复的会话。",
	},
	"en": {
		"title": "Synthetic experiment",
		"status": "Local save and server receipt are separate states.",
		"anonymous": "This study does not require an ID.",
		"code": "Roster ID (leave empty for anonymous)",
		"password": "Roster password",
		"recovery_legend": "Same-device recovery",
		"short_code": "Six-digit recovery code (issued by the researcher)",
		"recover": "Recover",
		"advanced": "Advanced: long permit (compatibility path)",
		"session": "Session UUID",
		"permit": "One-time recovery permit",
		"recover_permit": "Recover with permit",
		"start": "Start participation",
		"export": "Export current recovery data",
		"continue_": "Continue previous session",
		"start_new": "Start a new session",
		"cancel": "Cancel",
		"confirm_continue": "A local unfinished session was found. Continue it?",
		"confirm_data_only": "A local session was found; it already declared completion, so only its data can be recovered. Continue?",
		"cleaned": "This device already uploaded its previous data; no recovery is needed.",
		"ambiguous": "Several local sessions match. Ask the researcher; no session was continued.",
		"front_locked": "A newer participation locked this local session; use the issued six-digit code or long permit.",
		"code_denied": "The code is invalid, expired or does not match this device proof; no session was recovered.",
		"invalid_code": "Enter the six-digit recovery code.",
		"legacy_fields": "Long-permit recovery needs both the session UUID and the permit.",
		"recovery_denied": "Credentials or device proof do not match; no session was recovered.",
		"trial": "Trial {n}: press LEFT or RIGHT",
		"save_failed": "Save failed: {code}",
		"finished_local": "Task finished; the local records are committed and waiting for upload confirmation.",
		"uploaded": "Task finished; data is uploaded and confirmed, and only the cleaned marker remains.",
		"data_only": "Data recovery only: no trials will resume.",
		"error_prefix": "Cannot start: {code}",
		"retry": "The server is temporarily unavailable; retries back off automatically and local records are kept.",
		"unsupported": "The release capability is not recognized, so the shell stopped instead of guessing.",
		"no_server": "Local-only mode: no server connection.",
		"bridge_unavailable": "The participation panel could not reach the browser bridge.",
		"err_admission_denied": "The roster ID or password is not valid, or the ID is unavailable.",
		"err_participation_limit": "This roster ID reached its participation limit.",
		"err_admission_closed": "This study is not open for new participation.",
		"err_wrong_program_version": "The participation build does not match the release version.",
		"err_stale_entry": "The study entry changed; go back to the entry page and retry.",
		"err_entry_closed": "This study has no available participation release.",
		"err_unsupported_capability": "The server does not support this recovery capability.",
		"err_not_recoverable": "No recoverable session exists on this device.",
	},
}

var backend: Node
var web := false
var config: Dictionary = {}
var mode := "legacy"
var state := "unprepared"
var locale := "zh"
var data_only := false
var finished_announced := false
var pending: Dictionary = {}
var pending_credentials: Dictionary = {}

var message: Label
var hint: Label
var code: LineEdit
var password: LineEdit
var short_code: LineEdit
var recovery: LineEdit
var permit: LineEdit
var start: Button
var export_button: Button
var advanced_toggle: Button
var advanced_box: VBoxContainer
var confirm_box: HBoxContainer
var confirm_label: Label
var confirm_continue: Button
var confirm_new: Button
var confirm_cancel: Button

var _callbacks: Array = []
var _reported_state := ""
var _refresh: Timer


func setup(selected: Node) -> Dictionary:
	backend = selected
	web = OS.has_feature("web")
	if web and backend != null and backend.has_method("context"):
		config = backend.context()
	elif backend != null and backend.get("config") is Dictionary:
		config = backend.config
	_resolve_locale()
	_resolve_mode()
	if state == "unsupported":
		_build_native_ui()
		announce(t("unsupported"))
		print("SYNTHETIC_ERROR ", t("unsupported"))
		start.disabled = true
		return {"error": "unsupported_configuration"}
	if not web and backend != null and backend.has_method("initialize_queue"):
		var prepared: Dictionary = backend.initialize_queue()
		if prepared.has("error"):
			_build_native_ui()
			_set_error(t("error_prefix", {"code": prepared.error}))
			return prepared
	_build_native_ui()
	state = "ready"
	announce(t("status"))
	_refresh = Timer.new()
	_refresh.wait_time = 0.5
	_refresh.timeout.connect(_refresh_state)
	add_child(_refresh)
	_refresh.start()
	if web:
		call_deferred("_mount_web_shell")
	return {"state": state, "mode": mode, "locale": locale}


func _resolve_locale() -> void:
	var requested := str(config.get("locale", ""))
	if requested.is_empty():
		requested = OS.get_locale_language()
	if LOCALIZED.has(requested):
		locale = requested
	else:
		locale = "zh" if requested.begins_with("zh") else "en"


func _resolve_mode() -> void:
	mode = "legacy"
	if config.is_empty():
		return
	if not config.has("mode"):
		return
	var declared := str(config.get("mode", ""))
	var declared_capability := str(config.get("shell_capability", ""))
	if declared_capability != "" and declared_capability != SHELL_CAPABILITY:
		mode = "unsupported"
		state = "unsupported"
		return
	if not declared in ["anonymous", "id", "password"]:
		mode = "unsupported"
		state = "unsupported"
		return
	mode = declared


func t(key: String, values: Dictionary = {}) -> String:
	var table: Dictionary = LOCALIZED.get(locale, LOCALIZED["zh"])
	var text := str(table.get(key, LOCALIZED["zh"].get(key, key)))
	for name in values:
		text = text.replace("{" + str(name) + "}", str(values[name]))
	return text


func set_locale(code: String) -> void:
	if not LOCALIZED.has(code):
		return
	locale = code
	if message == null:
		return
	announce(t("status"))
	if hint != null and mode == "anonymous":
		hint.text = t("anonymous")
	if code_field() != null:
		code_field().placeholder_text = t("code")
	if password != null:
		password.placeholder_text = t("password")
	if short_code != null:
		short_code.placeholder_text = t("short_code")
	if recovery != null:
		recovery.placeholder_text = t("session")
	if permit != null:
		permit.placeholder_text = t("permit")
	if advanced_toggle != null:
		advanced_toggle.text = t("advanced")
	if start != null:
		start.text = t("start")
	if export_button != null:
		export_button.text = t("export")
	if confirm_continue != null:
		confirm_continue.text = t("continue_")
	if confirm_new != null:
		confirm_new.text = t("start_new")
	if confirm_cancel != null:
		confirm_cancel.text = t("cancel")


func code_field() -> LineEdit:
	return code


func _build_native_ui() -> void:
	var box = VBoxContainer.new()
	box.position = Vector2(70, 70)
	box.size = Vector2(850, 560)
	add_child(box)
	message = Label.new()
	message.text = t("status")
	box.add_child(message)
	# Keep the two-line status region stable when trial/error text uses one line.
	message.custom_minimum_size.y = message.get_minimum_size().y
	if web:
		# Web entry, recovery and finish controls are owned by the browser panel;
		# the canvas only carries the localized status/trial line.
		return
	hint = Label.new()
	hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	hint.custom_minimum_size = Vector2(850, 0)
	hint.text = t("anonymous") if mode == "anonymous" else ""
	box.add_child(hint)
	if mode != "anonymous":
		code = LineEdit.new()
		code.placeholder_text = t("code")
		box.add_child(code)
	if mode in ["password", "legacy"]:
		password = LineEdit.new()
		password.secret = true
		password.placeholder_text = t("password")
		box.add_child(password)
	if backend != null and backend.has_method("recover_code"):
		short_code = LineEdit.new()
		short_code.max_length = 6
		short_code.placeholder_text = t("short_code")
		box.add_child(short_code)
		var recover_button = Button.new()
		recover_button.text = t("recover")
		recover_button.pressed.connect(func(): await submit_entry({"short_code": short_code.text}))
		box.add_child(recover_button)
		advanced_toggle = Button.new()
		advanced_toggle.text = t("advanced")
		advanced_toggle.pressed.connect(_toggle_advanced)
		box.add_child(advanced_toggle)
		advanced_box = VBoxContainer.new()
		advanced_box.visible = false
		box.add_child(advanced_box)
		recovery = LineEdit.new()
		recovery.placeholder_text = t("session")
		advanced_box.add_child(recovery)
		permit = LineEdit.new()
		permit.secret = true
		permit.placeholder_text = t("permit")
		advanced_box.add_child(permit)
		var permit_button = Button.new()
		permit_button.text = t("recover_permit")
		permit_button.pressed.connect(func(): await submit_entry({"recovery": recovery.text, "permit": permit.text}))
		advanced_box.add_child(permit_button)
	elif backend == null or not backend.has_method("recover_code"):
		hint.text = t("no_server")
	start = Button.new()
	start.text = t("start")
	start.pressed.connect(func(): await submit_entry())
	box.add_child(start)
	export_button = Button.new()
	export_button.text = t("export")
	export_button.pressed.connect(export_recovery)
	box.add_child(export_button)
	confirm_box = HBoxContainer.new()
	confirm_box.visible = false
	box.add_child(confirm_box)
	confirm_label = Label.new()
	confirm_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	confirm_label.custom_minimum_size = Vector2(430, 0)
	confirm_box.add_child(confirm_label)
	confirm_continue = Button.new()
	confirm_continue.text = t("continue_")
	confirm_continue.pressed.connect(confirm_session)
	confirm_box.add_child(confirm_continue)
	confirm_new = Button.new()
	confirm_new.text = t("start_new")
	confirm_new.pressed.connect(confirm_new_session)
	confirm_box.add_child(confirm_new)
	confirm_cancel = Button.new()
	confirm_cancel.text = t("cancel")
	confirm_cancel.pressed.connect(cancel_confirmation)
	confirm_box.add_child(confirm_cancel)


func _toggle_advanced() -> void:
	if advanced_box != null:
		advanced_box.visible = not advanced_box.visible


func _mount_web_shell() -> void:
	for _i in range(180):
		var bridge = JavaScriptBridge.get_interface("GECBridge")
		if bridge != null:
			var callback := JavaScriptBridge.create_callback(_on_web_action)
			_callbacks.append(callback)
			bridge.on_shell_action(callback)
			bridge.mount_shell(JSON.stringify({
				"title": t("title"),
				"status": t("status"),
				"mode": mode,
				"show_code": mode == "legacy",
				"messages": {
					"anonymous": t("anonymous"),
					"code": t("code"),
					"password": t("password"),
					"recovery_legend": t("recovery_legend"),
					"short_code": t("short_code"),
					"recover": t("recover"),
					"advanced": t("advanced"),
					"session": t("session"),
					"permit": t("permit"),
					"recover_permit": t("recover_permit"),
					"start": t("start"),
					"export": t("export"),
					"continue_": t("continue_"),
					"start_new": t("start_new"),
					"cancel": t("cancel"),
				},
			}))
			return
		await get_tree().process_frame
	_set_error(t("bridge_unavailable"))


func _on_web_action(args: Array) -> void:
	var action := str(args[0]) if args.size() > 0 else ""
	_focus_canvas()
	match action:
		"start":
			await submit_entry()
		"export":
			await export_recovery()
		"recover-code":
			await submit_entry({"short_code": _web_values().get("short_code", "")})
		"recover-permit":
			await submit_entry({"recovery": _web_values().get("recovery", ""), "permit": _web_values().get("permit", "")})
		"confirm-continue":
			await confirm_session()
		"confirm-new":
			await confirm_new_session()
		"confirm-cancel":
			cancel_confirmation()
	_focus_canvas()


func _focus_canvas() -> void:
	if not web:
		return
	var bridge = JavaScriptBridge.get_interface("GECBridge")
	if bridge != null:
		bridge.shell_blur()


func _web_values() -> Dictionary:
	if not web:
		return {}
	var bridge = JavaScriptBridge.get_interface("GECBridge")
	if bridge == null:
		return {}
	var parsed = JSON.parse_string(bridge.shell_values())
	return parsed if parsed is Dictionary else {}


func _read_values() -> Dictionary:
	if web:
		return _web_values()
	var values := {}
	if code != null:
		values["code"] = code.text
	if password != null:
		values["password"] = password.text
	if short_code != null:
		values["short_code"] = short_code.text
	if recovery != null:
		values["recovery"] = recovery.text
	if permit != null:
		values["permit"] = permit.text
	return values


func _clear_secrets() -> void:
	if web:
		var bridge = JavaScriptBridge.get_interface("GECBridge")
		if bridge != null:
			bridge.shell_clear_secrets()
		return
	if password != null:
		password.text = ""
	if permit != null:
		permit.text = ""
	if short_code != null:
		short_code.text = ""


func _set_busy(busy: bool) -> void:
	if web:
		_web_state({"busy": busy})
		return
	if start != null:
		start.disabled = busy


func _web_state(spec: Dictionary) -> void:
	if not web:
		return
	var bridge = JavaScriptBridge.get_interface("GECBridge")
	if bridge != null:
		bridge.shell_state(JSON.stringify(spec))


func announce(text: String) -> void:
	if message != null:
		message.text = text
	if web:
		# The browser panel is the visible surface, so the announced state (local,
		# upload, error, retry, data-only, finish) is pushed there too.
		_web_state({"status": text})
		var document = JavaScriptBridge.get_interface("document")
		if document != null:
			var canvas = document.getElementById("canvas")
			if canvas != null:
				canvas.setAttribute("aria-label", text)


func _error_message(code: String) -> String:
	var table: Dictionary = LOCALIZED.get(locale, LOCALIZED["zh"])
	var key := "err_" + code
	if table.has(key):
		return str(table[key])
	return t("error_prefix", {"code": code})


func _fail_with(result: Dictionary) -> void:
	var code := str(result.get("code", ""))
	var raw := str(result.get("error", ""))
	if code.is_empty():
		code = raw
	_set_error(_error_message(code), code, raw)


func _set_error(text: String, reason := "", raw := "") -> void:
	state = "error"
	announce(text)
	var codes: Array = []
	if not raw.is_empty():
		codes.append(raw)
	if not reason.is_empty() and reason != raw:
		codes.append(reason)
	print("SYNTHETIC_ERROR ", " ".join(codes) if not codes.is_empty() else text)
	_web_state({"busy": false, "reason": reason if not reason.is_empty() else raw})


func submit_entry(overrides: Dictionary = {}) -> Dictionary:
	if state == "unsupported":
		return {"error": "unsupported_configuration"}
	if state == "busy":
		return {"error": "busy"}
	state = "busy"
	var values := _read_values()
	for key in overrides:
		values[key] = overrides[key]
	_clear_secrets()
	_set_busy(true)
	var legacy_session := str(values.get("recovery", "")).strip_edges()
	var legacy_permit := str(values.get("permit", ""))
	var result: Dictionary
	if not legacy_session.is_empty() or not legacy_permit.is_empty():
		result = await _submit_legacy(legacy_session, legacy_permit)
	else:
		var code_value := str(values.get("short_code", "")).strip_edges()
		if not code_value.is_empty():
			result = await _submit_code(code_value)
		else:
			result = await _submit_named(values)
	_set_busy(false)
	return result


func _submit_legacy(session_id: String, permit_value: String) -> Dictionary:
	if session_id.is_empty() or permit_value.is_empty():
		_set_error(t("legacy_fields"))
		return {"error": "legacy_fields"}
	var result: Dictionary = await backend.prepare({"recovery_session": session_id, "permit": permit_value})
	return _apply_prepare(result)


func _submit_code(code_value: String) -> Dictionary:
	if backend == null or not backend.has_method("recover_code"):
		_set_error(t("no_server"))
		return {"error": "unsupported"}
	var candidate: Dictionary = await backend.recover_code(code_value)
	if candidate.has("error"):
		var code_error := str(candidate.get("error", ""))
		if code_error == "recovery_denied":
			_set_error(t("code_denied"), "recovery_denied", code_error)
		elif code_error == "invalid_code":
			_set_error(t("invalid_code"), "invalid_code", code_error)
		else:
			_set_error(_error_message(code_error), code_error)
		return candidate
	pending = candidate
	pending_credentials = {}
	_show_confirmation(candidate, false)
	return {"state": "confirm"}


func _submit_named(values: Dictionary) -> Dictionary:
	var participant_code := str(values.get("code", "")).strip_edges()
	var password_value := str(values.get("password", ""))
	if mode in ["id", "password"] and backend != null and backend.has_method("recover_named"):
		var candidate: Dictionary = await backend.recover_named(participant_code, password_value)
		if candidate.has("error"):
			_fail_with(candidate)
			return candidate
		match str(candidate.get("state", "none")):
			"confirm":
				pending = candidate
				pending_credentials = {"participant_code": participant_code, "password": password_value}
				_show_confirmation(candidate, true)
				return {"state": "confirm"}
			"ambiguous":
				_set_error(t("ambiguous"), "ambiguous")
				print("SYNTHETIC_NAMED ambiguous")
				return {"state": "ambiguous"}
			"front_locked":
				_set_error(t("front_locked"), "front_locked")
				print("SYNTHETIC_NAMED front_locked")
				return {"state": "front_locked"}
			"cleaned":
				announce(t("cleaned"))
				print("SYNTHETIC_NAMED cleaned")
			_:
				print("SYNTHETIC_NAMED none")
	var credentials := {"expected_version": "synthetic-1"}
	if mode != "anonymous":
		if not participant_code.is_empty():
			credentials["participant_code"] = participant_code
		if mode == "password" and not password_value.is_empty():
			credentials["password"] = password_value
	var result: Dictionary = await backend.prepare(credentials)
	return _apply_prepare(result)


func _show_confirmation(candidate: Dictionary, allow_new: bool) -> void:
	var text := t("confirm_continue") if bool(candidate.get("can_resume", false)) else t("confirm_data_only")
	state = "confirm"
	announce(text)
	if web:
		_web_state({"busy": false, "confirm": {"text": text, "new_session": allow_new}, "focus": "confirm-continue"})
		return
	if confirm_box != null:
		confirm_label.text = text
		confirm_new.visible = allow_new
		confirm_box.visible = true
		confirm_continue.grab_focus()


func _hide_confirmation() -> void:
	if web:
		_web_state({"confirm": null, "busy": false})
		return
	if confirm_box != null:
		confirm_box.visible = false


func confirm_session() -> Dictionary:
	if pending.is_empty():
		return {"error": "no_pending_recovery"}
	var candidate := pending
	pending = {}
	pending_credentials = {}
	_hide_confirmation()
	state = "busy"
	_set_busy(true)
	var result: Dictionary = await backend.confirm_recovery(str(candidate.get("session_id", "")), str(candidate.get("token", "")), bool(candidate.get("can_resume", false)))
	_set_busy(false)
	return _apply_prepare(result)


func confirm_new_session() -> Dictionary:
	if pending_credentials.is_empty():
		return {"error": "no_pending_recovery"}
	var credentials := {"expected_version": "synthetic-1"}
	if not str(pending_credentials.get("participant_code", "")).is_empty():
		credentials["participant_code"] = pending_credentials["participant_code"]
	if not str(pending_credentials.get("password", "")).is_empty():
		credentials["password"] = pending_credentials["password"]
	pending = {}
	pending_credentials = {}
	_hide_confirmation()
	state = "busy"
	_set_busy(true)
	var result: Dictionary = await backend.prepare(credentials)
	_set_busy(false)
	return _apply_prepare(result)


func cancel_confirmation() -> void:
	pending = {}
	pending_credentials = {}
	_hide_confirmation()
	state = "ready"
	announce(t("status"))


func _apply_prepare(result: Dictionary) -> Dictionary:
	if result.has("error"):
		_fail_with(result)
		return result
	if str(result.get("state", "")) == "data_only":
		data_only = true
		state = "data_only"
		_reported_state = "data_only"
		announce(t("data_only"))
		print("SYNTHETIC_DATA_ONLY")
		emit_signal("session_data_only")
		return result
	state = "active"
	data_only = false
	finished_announced = false
	_reported_state = "active"
	_web_state({"reason": ""})
	emit_signal("session_started", result.get("checkpoint"))
	return result


func announce_finished(_result: Dictionary = {}) -> void:
	finished_announced = true
	announce(t("finished_local"))


func _refresh_state() -> void:
	if backend == null or state in ["unsupported", "busy", "confirm"]:
		return
	var summary: Dictionary = {}
	if backend.has_method("summary"):
		summary = await backend.summary()
	elif backend.has_method("status"):
		summary = backend.status()
	var current := str(summary.get("state", "unprepared"))
	if current == _reported_state:
		return
	_reported_state = current
	if current == "remote_acknowledged":
		state = "uploaded"
		announce(t("uploaded"))
	elif current == "local_committed" and finished_announced:
		announce(t("finished_local"))
	elif current in ["active", "local_committed"] and not str(summary.get("error", "")).is_empty():
		announce(t("retry") + " (" + str(summary.get("error", "")) + ")")


func export_recovery() -> void:
	if backend == null:
		return
	if web:
		var result: Dictionary = await backend.download_recovery()
		if result.has("error"):
			announce(t("error_prefix", {"code": str(result.get("error", ""))}))
		return
	var recovery_data: Dictionary = await backend.recovery_export()
	if recovery_data.has("error"):
		announce(t("error_prefix", {"code": str(recovery_data.get("error", ""))}))
		return
	var dialog = FileDialog.new()
	dialog.file_mode = FileDialog.FILE_MODE_SAVE_FILE
	dialog.access = FileDialog.ACCESS_FILESYSTEM
	dialog.use_native_dialog = true
	dialog.current_file = "recovery.json"
	dialog.filters = PackedStringArray(["*.json ; Recovery data"])
	add_child(dialog)
	dialog.file_selected.connect(func(path):
		var file = FileAccess.open(path, FileAccess.WRITE)
		if file == null:
			announce(t("error_prefix", {"code": "recovery_export_unavailable"}))
			return
		file.store_string(JSON.stringify(recovery_data))
		file.close()
	)
	dialog.popup_centered(Vector2i(800, 500))


func export_recovery_to(path: String) -> Dictionary:
	## Automation entry for the failure-data export: it writes exactly the same
	## recovery document the GUI save dialog writes, without a dialog and without
	## secrets. It never deletes the local queue.
	if backend == null or not backend.has_method("recovery_export"):
		return {"error": "recovery_export_unavailable"}
	var recovery_data: Dictionary = await backend.recovery_export()
	if recovery_data.has("error"):
		return recovery_data
	var file = FileAccess.open(path, FileAccess.WRITE)
	if file == null:
		return {"error": "recovery_export_unavailable"}
	file.store_string(JSON.stringify(recovery_data))
	file.close()
	return {"state": "exported", "path": path}


## Harness entry used by the synthetic automation: it fills the same shell fields
## and calls the same submission path as a real button press.
func auto_submit(credentials: Dictionary, auto_new := false) -> Dictionary:
	var overrides := {}
	for key in ["code", "password", "short_code", "recovery", "permit"]:
		if credentials.has(key):
			overrides[key] = str(credentials[key])
	if credentials.has("recovery_session"):
		overrides["recovery"] = str(credentials["recovery_session"])
	if overrides.has("recovery") and not overrides.has("permit"):
		overrides["permit"] = str(credentials.get("permit", ""))
	var result := await submit_entry(overrides)
	if str(result.get("state", "")) == "confirm":
		if auto_new and not pending_credentials.is_empty():
			return await confirm_new_session()
		return await confirm_session()
	return result


func status_snapshot() -> Dictionary:
	return {"state": state, "mode": mode, "locale": locale, "data_only": data_only, "finished_announced": finished_announced, "pending": not pending.is_empty()}
