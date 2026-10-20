@tool
extends EditorScript
## Editor-only helper: no game execution, server request or connection.json.
const SQLITE_DLL := "libgdsqlite.windows.template_release.x86_64.dll"
const SQLITE_EXTENSION := "addons/godot-sqlite/gdsqlite.gdextension"
const ZIP_TIMESTAMP := 946684800 # Fixed 2000 date, safely inside ZIP's range.
const MAX_ARCHIVE_BYTES := 256 * 1024 * 1024
const MAX_EXPANDED_BYTES := 512 * 1024 * 1024
const MAX_DESCRIPTOR_BYTES := 1024 * 1024
const MAX_FILES := 4096
const CHUNK_BYTES := 256 * 1024
const DESCRIPTOR_FIELDS := ["version", "platform", "host_version", "sdk_version", "protocol_version", "schemas", "codebook", "program_sha256"]
var picker: FileDialog

func _run() -> void:
	var version := Engine.get_version_info()
	if version.major != 4 or version.minor != 7 or version.patch != 2:
		_message("请使用 Godot 4.7.2；GEP 当前不接受其他引擎版本。")
		return
	picker = FileDialog.new()
	picker.title = "选择 Godot 导出的 macOS ZIP 或 Windows EXE"
	picker.file_mode = FileDialog.FILE_MODE_OPEN_FILE
	picker.access = FileDialog.ACCESS_FILESYSTEM
	picker.filters = PackedStringArray(["*.zip,*.exe ; macOS ZIP / Windows EXE"])
	picker.current_dir = ProjectSettings.globalize_path("res://exports")
	picker.file_selected.connect(_selected)
	picker.canceled.connect(func(): picker.queue_free())
	picker.set_meta("packaging_script", self)
	EditorInterface.get_base_control().add_child(picker)
	picker.popup_centered_ratio(0.7)

func _selected(path: String) -> void:
	picker.queue_free()
	var checked := _file_check("res://gec_descriptor.json", MAX_DESCRIPTOR_BYTES)
	if checked.has("error"):
		_message("打包失败：" + str(checked.error))
		return
	var descriptor: Variant = JSON.parse_string(FileAccess.get_file_as_string("res://gec_descriptor.json"))
	if not descriptor is Dictionary:
		_message("gec_descriptor.json 不是 JSON 对象，请依据 gec_descriptor.example.json 创建自己的完整构建描述。")
		return
	var result := _windows(path, descriptor) if path.to_lower().ends_with(".exe") else _macos(path, descriptor)
	if result.has("error"):
		_message("打包失败：" + str(result.error))
	else:
		_message("打包完成。\n\n程序 ZIP：\n%s\n\n构建描述：\n%s\n\n接下来在 GEP“构建与发行”登记构建描述并上传这个 ZIP。" % [result.archive, result.descriptor])

static func _descriptor_problem(descriptor: Dictionary) -> String:
	var fields: Array = descriptor.keys()
	fields.sort()
	var expected: Array = DESCRIPTOR_FIELDS.duplicate()
	expected.sort()
	if fields != expected:
		return "构建模板字段不匹配，请依据 gec_descriptor.example.json 检查自己的描述；不要加入连接配置或凭据。"
	if not descriptor.version is String or descriptor.version.is_empty() or descriptor.version.length() > 64 or descriptor.host_version != "4.7.2" or descriptor.sdk_version != "0.1.0" or descriptor.protocol_version != "gep/1":
		return "构建模板版本不兼容，需要 Godot 4.7.2、SDK 0.1.0、gep/1。"
	if not descriptor.schemas is Dictionary or descriptor.schemas.is_empty() or descriptor.schemas.size() > 16 or not descriptor.codebook is Dictionary or descriptor.codebook.is_empty():
		return "构建描述缺少 schema 或 codebook，请填写自己的事件 schema、字段、单位和来源。"
	return ""

static func _absolute(path: String) -> String:
	return ProjectSettings.globalize_path(path).replace("\\", "/").simplify_path()

static func _contains_link(path: String) -> bool:
	var absolute := _absolute(path)
	while not absolute.is_empty():
		var parent := absolute.get_base_dir()
		if parent == absolute:
			break
		var directory := DirAccess.open(parent)
		if directory != null and directory.is_link(absolute.get_file()):
			return true
		absolute = parent
	return false

static func _occupied(path: String) -> bool:
	return FileAccess.file_exists(path) or DirAccess.dir_exists_absolute(path) or _contains_link(path)

static func _file_check(path: String, limit: int) -> Dictionary:
	if _contains_link(path):
		return {"error": "不接受符号链接文件或目录：" + path}
	var file := FileAccess.open(_absolute(path), FileAccess.READ)
	if file == null:
		return {"error": "缺少或无法读取 " + path.get_file() + "；请检查完整导出与文件权限。"}
	var size := file.get_length()
	file.close()
	if size <= 0 or size > limit:
		return {"error": "文件为空或超过打包体积上限：" + path.get_file()}
	return {"size": size}

static func _reserve(directory: String, stem: String) -> Dictionary:
	if _contains_link(directory) or not DirAccess.dir_exists_absolute(directory):
		return {"error": "输出目录包含符号链接或不存在，请选择普通导出目录。"}
	for index in range(1, 101):
		var name := stem if index == 1 else stem + "_" + str(index)
		var archive := directory.path_join(name + ".zip")
		var descriptor := directory.path_join(name + "_descriptor.json")
		var lock := archive + ".lock"
		if _occupied(archive) or _occupied(descriptor) or _occupied(lock):
			continue
		if DirAccess.make_dir_absolute(lock) == OK:
			return {"archive": archive, "descriptor": descriptor, "lock": lock,
				"archive_tmp": lock.path_join("archive.tmp"), "descriptor_tmp": lock.path_join("descriptor.tmp")}
	return {"error": "输出目录已有 100 个同名前缀结果，或目录不可写；请选择新的导出目录。"}

static func _cleanup(reservation: Dictionary) -> void:
	# Only files in the directory this invocation exclusively reserved are ours.
	for key in ["archive_tmp", "descriptor_tmp"]:
		var path: String = reservation[key]
		if not _contains_link(path) and FileAccess.file_exists(path):
			DirAccess.remove_absolute(path)
	if not _contains_link(reservation.lock):
		DirAccess.remove_absolute(reservation.lock)

static func _write_descriptor(path: String, descriptor: Dictionary) -> bool:
	var bytes := (JSON.stringify(descriptor, "  ") + "\n").to_utf8_buffer()
	if bytes.size() > MAX_DESCRIPTOR_BYTES or _occupied(path):
		return false
	var target := FileAccess.open(path, FileAccess.WRITE)
	if target == null:
		return false
	target.store_buffer(bytes)
	target.flush()
	var success := target.get_error() == OK
	target.close()
	return success and FileAccess.get_file_as_bytes(path) == bytes and JSON.parse_string(bytes.get_string_from_utf8()) == descriptor

static func _publish(reservation: Dictionary, descriptor: Dictionary) -> Dictionary:
	if not _write_descriptor(reservation.descriptor_tmp, descriptor):
		_cleanup(reservation)
		return {"error": "构建描述写入或回读校验失败；本次临时输出已撤销，请检查磁盘与权限。"}
	if _occupied(reservation.archive) or _occupied(reservation.descriptor) or _contains_link(reservation.lock):
		_cleanup(reservation)
		return {"error": "目标路径刚刚被创建或发生变化，已停止以保护原文件；请重新运行。"}
	# Both objects are verified before either receives its final name. Two
	# filesystem renames cannot be one atomic operation: a failed second rename
	# withdraws only our ZIP. Interruption leaves at most a complete ZIP plus its
	# reservation, never a partially written final ZIP or JSON.
	if DirAccess.rename_absolute(reservation.archive_tmp, reservation.archive) != OK:
		_cleanup(reservation)
		return {"error": "无法保存已校验的程序 ZIP；原导出和旧结果保持不变。"}
	if _occupied(reservation.descriptor) or DirAccess.rename_absolute(reservation.descriptor_tmp, reservation.descriptor) != OK:
		if not _contains_link(reservation.archive) and FileAccess.get_sha256(reservation.archive) == descriptor.program_sha256:
			DirAccess.remove_absolute(reservation.archive)
		_cleanup(reservation)
		return {"error": "无法保存构建描述；本次程序 ZIP 已撤回，原导出和旧结果保持不变。"}
	_cleanup(reservation)
	return {"archive": reservation.archive, "descriptor": reservation.descriptor}

static func _windows(entry: String, template: Dictionary, extension_path: String = "res://" + SQLITE_EXTENSION) -> Dictionary:
	var problem := _descriptor_problem(template)
	if not problem.is_empty():
		return {"error": problem}
	entry = _absolute(entry)
	var directory := entry.get_base_dir()
	var root := directory.get_file()
	if root.is_empty() or root.length() > 255 or entry.get_extension().to_lower() != "exe" or entry.get_basename().get_file().is_empty():
		return {"error": "请选择 Godot 导出的 Windows EXE；同目录须有同名 PCK 和 SQLite release DLL。"}
	var pck := entry.get_basename() + ".pck"
	var dll := directory.path_join(SQLITE_DLL)
	var members := {root + "/" + entry.get_file(): entry, root + "/" + pck.get_file(): pck,
		root + "/" + SQLITE_DLL: dll, root + "/" + SQLITE_EXTENSION: _absolute(extension_path)}
	var expanded := 0
	for file: String in members.values():
		var checked := _file_check(file, MAX_EXPANDED_BYTES)
		if checked.has("error"):
			return checked
		expanded += checked.size
	if expanded > MAX_EXPANDED_BYTES:
		return {"error": "程序展开体积超过 GEP 的 512 MiB 上限；没有创建上传包。"}
	var reservation := _reserve(directory.get_base_dir(), "gep_windows")
	if reservation.has("error"):
		return reservation
	var expected := _write_windows(reservation.archive_tmp, members)
	if expected.has("error"):
		_cleanup(reservation)
		return expected
	var checked := _verify_archive(reservation.archive_tmp, "windows_x64", expected.hashes)
	if checked.has("error"):
		_cleanup(reservation)
		return checked
	var descriptor := template.duplicate(true)
	descriptor.platform = "windows_x64"
	descriptor.package = {"root": root, "entry": entry.get_file(), "dependencies": [SQLITE_DLL], "extension": SQLITE_EXTENSION}
	descriptor.program_sha256 = FileAccess.get_sha256(reservation.archive_tmp)
	if str(descriptor.program_sha256).length() != 64:
		_cleanup(reservation)
		return {"error": "无法核对本次 ZIP 摘要；未生成构建描述。"}
	return _publish(reservation, descriptor)

static func _write_windows(path: String, members: Dictionary) -> Dictionary:
	if _occupied(path):
		return {"error": "临时路径已存在，停止打包以保护原文件。"}
	var writer := ZIPPacker.new()
	if writer.open(path) != OK:
		return {"error": "无法创建临时程序 ZIP。"}
	var hashes := {}
	var names: Array = members.keys()
	names.sort()
	var error := OK
	for name: String in names:
		var source := FileAccess.open(members[name], FileAccess.READ)
		if source == null or _contains_link(members[name]):
			error = ERR_FILE_CANT_READ
			break
		var length := source.get_length()
		if length <= 0 or length > MAX_EXPANDED_BYTES:
			source.close()
			error = ERR_FILE_CANT_READ
			break
		var hash := HashingContext.new()
		hash.start(HashingContext.HASH_SHA256)
		error = writer.start_file(name, 420, ZIP_TIMESTAMP)
		while error == OK and source.get_position() < length:
			var chunk := source.get_buffer(mini(CHUNK_BYTES, length - source.get_position()))
			if chunk.is_empty() or hash.update(chunk) != OK:
				error = ERR_FILE_CANT_READ
				break
			error = writer.write_file(chunk)
		if length <= 0 or source.get_length() != length:
			error = ERR_FILE_CANT_READ
		source.close()
		if error == OK:
			error = writer.close_file()
		hashes[name] = hash.finish().hex_encode()
		if error != OK:
			break
	var closed := writer.close()
	if error != OK or closed != OK:
		return {"error": "程序 ZIP 写入失败；没有发布最终包。"}
	return {"hashes": hashes}

static func _macos(source: String, template: Dictionary) -> Dictionary:
	var problem := _descriptor_problem(template)
	if not problem.is_empty():
		return {"error": problem}
	source = _absolute(source)
	var checked := _verify_archive(source, "macos_arm64")
	if checked.has("error"):
		return checked
	var original_hash := FileAccess.get_sha256(source)
	var reservation := _reserve(source.get_base_dir(), "gep_macos")
	if reservation.has("error"):
		return reservation
	if DirAccess.copy_absolute(source, reservation.archive_tmp) != OK:
		_cleanup(reservation)
		return {"error": "无法复制程序 ZIP；请检查磁盘与目录权限。"}
	checked = _verify_archive(reservation.archive_tmp, "macos_arm64")
	var copied_hash := FileAccess.get_sha256(reservation.archive_tmp)
	if checked.has("error") or copied_hash.length() != 64 or copied_hash != original_hash:
		_cleanup(reservation)
		return {"error": str(checked.get("error", "复制后的 ZIP 摘要变化；没有发布最终包。"))}
	var descriptor := template.duplicate(true)
	descriptor.platform = "macos_arm64"
	descriptor.program_sha256 = copied_hash
	return _publish(reservation, descriptor)

static func _unsafe_member(name: String) -> bool:
	var raw := name.trim_suffix("/")
	if raw.is_empty() or raw.begins_with("/") or raw.contains("\\") or raw.contains(":") or raw.length() > 1024:
		return true
	for part: String in raw.split("/"):
		var lower := part.to_lower()
		if part in ["", ".", ".."] or lower == "connection.json" or lower.begins_with(".env") or lower.contains("credentials") or lower.ends_with(".sqlite") or lower.ends_with(".sqlite3") or lower.ends_with(".db"):
			return true
	return false

static func _zip_entries(path: String) -> Dictionary:
	var checked := _file_check(path, MAX_ARCHIVE_BYTES)
	if checked.has("error"):
		return checked
	var file := FileAccess.open(path, FileAccess.READ)
	var length := file.get_length()
	var tail_start := maxi(0, length - 65557)
	file.seek(tail_start)
	var tail := file.get_buffer(length - tail_start)
	var end := -1
	for cursor in range(tail.size() - 22, -1, -1):
		if tail.decode_u32(cursor) == 0x06054b50 and cursor + 22 + tail.decode_u16(cursor + 20) == tail.size():
			end = cursor
			break
	if end < 0:
		file.close()
		return {"error": "ZIP 结束目录缺失或截断；没有生成最终包。"}
	var count := tail.decode_u16(end + 10)
	var size := tail.decode_u32(end + 12)
	var offset := tail.decode_u32(end + 16)
	if count == 0 or count > MAX_FILES or tail.decode_u16(end + 4) != 0 or tail.decode_u16(end + 6) != 0 or tail.decode_u16(end + 8) != count or size > 16 * 1024 * 1024 or offset + size != tail_start + end:
		file.close()
		return {"error": "ZIP 目录数量、体积或边界无效；仅支持有界的标准 ZIP。"}
	file.seek(offset)
	var entries: Array[Dictionary] = []
	var names := {}
	var expanded := 0
	for unused in range(count):
		var header := file.get_buffer(46)
		if header.size() != 46 or header.decode_u32(0) != 0x02014b50:
			file.close()
			return {"error": "ZIP 目录条目损坏。"}
		var name_size := header.decode_u16(28)
		var following := name_size + header.decode_u16(30) + header.decode_u16(32)
		if name_size == 0 or name_size > 4096 or file.get_position() + following > offset + size:
			file.close()
			return {"error": "ZIP 成员路径或目录边界无效。"}
		var name := file.get_buffer(name_size).get_string_from_utf8()
		var kind := (header.decode_u32(38) >> 16) & 61440
		var directory := name.ends_with("/")
		var bytes := header.decode_u32(24)
		if _unsafe_member(name) or names.has(name.to_lower()) or header.decode_u16(8) & 1 or (kind not in [0, 16384] if directory else kind not in [0, 32768]):
			file.close()
			return {"error": "ZIP 含有不安全、重复、链接、加密或连接配置成员：" + name}
		names[name.to_lower()] = true
		expanded += bytes
		if expanded > MAX_EXPANDED_BYTES or bytes > MAX_EXPANDED_BYTES or (directory and bytes != 0) or (not directory and bytes == 0):
			file.close()
			return {"error": "ZIP 解压体积超过 512 MiB 或目录成员无效。"}
		entries.append({"name": name, "size": bytes, "directory": directory})
		file.seek(file.get_position() + following - name_size)
	var valid := file.get_position() == offset + size
	file.close()
	return {"entries": entries} if valid else {"error": "ZIP 目录大小不一致。"}

static func _verify_archive(path: String, platform: String, expected_hashes: Dictionary = {}) -> Dictionary:
	var structure := _zip_entries(path)
	if structure.has("error"):
		return structure
	var reader := ZIPReader.new()
	if reader.open(path) != OK:
		return {"error": "临时 ZIP 无法重新打开，未生成最终文件。"}
	var has_app := false
	var has_pck := false
	var has_sqlite := false
	var roots := {}
	var actual := {}
	for entry: Dictionary in structure.entries:
		var name: String = entry.name
		roots[name.split("/")[0]] = true
		if entry.directory:
			continue
		var contents := reader.read_file(name)
		if contents.size() != entry.size:
			reader.close()
			return {"error": "ZIP 文件回读失败或内容截断：" + name}
		actual[name] = true
		if not expected_hashes.is_empty():
			var hash := HashingContext.new()
			hash.start(HashingContext.HASH_SHA256)
			hash.update(contents)
			if not expected_hashes.has(name) or hash.finish().hex_encode() != expected_hashes[name]:
				reader.close()
				return {"error": "ZIP 内容与本次源文件摘要不一致：" + name}
		has_app = has_app or name.ends_with(".app/Contents/Info.plist")
		has_pck = has_pck or (name.ends_with(".pck") and name.contains(".app/Contents/Resources/"))
		has_sqlite = has_sqlite or name.contains(".app/Contents/Frameworks/libgdsqlite.macos.template_release.framework/")
	reader.close()
	if not expected_hashes.is_empty() and actual.size() != expected_hashes.size():
		return {"error": "ZIP 成员集合与本次源文件不一致。"}
	if platform == "macos_arm64" and (roots.size() != 1 or not str(roots.keys()[0]).ends_with(".app") or not has_app or not has_pck or not has_sqlite):
		return {"error": "程序 ZIP 缺少唯一 .app、PCK 或 SQLite Framework；请重新完整导出 macOS。"}
	return {"ok": true}

func _message(text: String) -> void:
	var popup := AcceptDialog.new()
	popup.title = "GEC 原生 GEP 包装"
	popup.dialog_text = text
	popup.min_size = Vector2i(660, 260)
	popup.confirmed.connect(func(): popup.queue_free())
	popup.canceled.connect(func(): popup.queue_free())
	EditorInterface.get_base_control().add_child(popup)
	popup.popup_centered()
