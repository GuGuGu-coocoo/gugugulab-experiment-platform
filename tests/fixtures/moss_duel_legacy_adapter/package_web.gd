@tool
extends EditorScript
## Open this script in Godot's script editor, then File > Run (Ctrl+Shift+X).
## Packaging never modifies the chosen export. Only the fixed runtime allowlist
## and the four bundled GEC scripts become ZIP members.

const EXPORT_FILES := [
	"index.html", "index.js", "index.wasm", "index.pck", "index.png",
	"index.audio.worklet.js", "index.audio.position.worklet.js",
]
const GEC_FILES := ["sdk.js", "bridge.js", "inputs.js", "shell.js"]
const DESCRIPTOR_FIELDS := [
	"version", "platform", "host_version", "sdk_version", "protocol_version",
	"schemas", "codebook", "program_sha256",
]
const MAX_FILE_BYTES := 128 * 1024 * 1024
const MAX_EXPANDED_BYTES := 256 * 1024 * 1024
const MAX_ARCHIVE_BYTES := 128 * 1024 * 1024
const MAX_DESCRIPTOR_BYTES := 1024 * 1024
const CHUNK_BYTES := 256 * 1024
const MAX_OUTPUT_NAMES := 100


func _run() -> void:
	var picker := FileDialog.new()
	picker.title = "选择已导出的 Web index.html"
	picker.access = FileDialog.ACCESS_FILESYSTEM
	picker.file_mode = FileDialog.FILE_MODE_OPEN_FILE
	picker.filters = PackedStringArray(["index.html ; Godot Web 入口"])
	picker.size = Vector2i(880, 560)
	# EditorScript is RefCounted: retain it until the file dialog is closed.
	picker.set_meta("packaging_script", self)
	get_editor_interface().get_base_control().add_child(picker)
	picker.file_selected.connect(func(path: String) -> void:
		var result := package_export(path)
		picker.queue_free()
		if result.ok:
			_show_message("GEP Web 包已生成", "完成。上传以下 ZIP 到 GEP 的“构建与发行”页面：\n\n" + result.path + "\n\n原 Web 导出文件没有改变。")
		else:
			_show_message("打包失败", result.error + "\n\n原 Web 导出文件没有改变；修正后重新运行此脚本。")
	)
	picker.canceled.connect(picker.queue_free)
	picker.popup_centered()


func _show_message(title: String, message: String) -> void:
	var dialog := AcceptDialog.new()
	dialog.title = title
	dialog.dialog_text = message
	dialog.min_size = Vector2i(660, 200)
	get_editor_interface().get_base_control().add_child(dialog)
	dialog.confirmed.connect(dialog.queue_free)
	dialog.canceled.connect(dialog.queue_free)
	dialog.popup_centered()


## Also callable headlessly with load(".../package_web.gd").package_export(...).
## Return {ok, path, program_sha256, members} or {ok: false, code, error}.
static func package_export(index_path: String, descriptor_path: String = "res://gec_descriptor.json", sdk_directory: String = "res://gec_web") -> Dictionary:
	var absolute_index := ProjectSettings.globalize_path(index_path).replace("\\", "/").simplify_path()
	if absolute_index.get_file() != "index.html":
		return _failure("wrong_entry", "请选择文件名为 index.html 的 Godot Web 导出入口。")
	var source_dir := absolute_index.get_base_dir()
	var output_dir := source_dir.get_base_dir()
	if source_dir == output_dir:
		return _failure("unsafe_output", "请将 Web 导出放在独立子目录中，例如 exports/web/。")
	if _contains_link(source_dir) or _contains_link(output_dir):
		return _failure("linked_path", "导出目录或其父目录包含符号链接；请选择普通目录中的导出。")
	var sources: Dictionary = {}
	for name: String in EXPORT_FILES:
		sources["web/" + name] = source_dir.path_join(name)
	for name: String in GEC_FILES:
		sources["web/gec/" + name] = ProjectSettings.globalize_path(sdk_directory.path_join(name)).replace("\\", "/").simplify_path()
	var descriptor_read := _read_bounded(descriptor_path, MAX_DESCRIPTOR_BYTES)
	if not descriptor_read.ok:
		return descriptor_read
	var parser := JSON.new()
	if parser.parse(descriptor_read.bytes.get_string_from_utf8()) != OK or not parser.data is Dictionary:
		return _failure("descriptor_json", "gec_descriptor.json 不是有效的 JSON 对象。请重新复制接入包中的该文件。")
	var descriptor: Dictionary = parser.data
	var descriptor_problem := _descriptor_problem(descriptor)
	if not descriptor_problem.is_empty():
		return _failure("descriptor_invalid", descriptor_problem)
	var paths: Array = sources.keys()
	paths.sort()
	var total: int = descriptor_read.bytes.size()
	for path: String in paths:
		var check := _file_check(sources[path], MAX_FILE_BYTES)
		if not check.ok:
			return check
		total += check.size
		if total > MAX_EXPANDED_BYTES:
			return _failure("expanded_limit", "Web 导出总大小超过 256 MiB，未生成 ZIP。")
	# Fail early if the editor's Web export preset was not updated.
	var html_read := _read_bounded(absolute_index, MAX_FILE_BYTES)
	if not html_read.ok:
		return html_read
	var html: String = html_read.bytes.get_string_from_utf8()
	var module_pattern := RegEx.new()
	module_pattern.compile("(?is)<script\\b(?=[^>]*\\btype\\s*=\\s*[\"']module[\"'])(?=[^>]*\\bsrc\\s*=\\s*[\"'](?:\\./)?gec/bridge\\.js[\"'])[^>]*>")
	if module_pattern.search(html) == null:
		return _failure("bridge_missing", "index.html 缺少 GEC module 桥接。请使用接入包的 Web 导出预设重新导出，再运行本助手。")
	# Hash paths + source bytes in the same lexicographic order as GEP intake.
	var hash := HashingContext.new()
	if hash.start(HashingContext.HASH_SHA256) != OK:
		return _failure("hash_start", "无法启动 SHA-256 校验。")
	for path: String in paths:
		if hash.update(path.to_utf8_buffer()) != OK:
			return _failure("hash_update", "无法计算路径摘要：" + path)
		var hashed := _hash_file(hash, sources[path])
		if not hashed.ok:
			return hashed
	descriptor["platform"] = "godot_web"
	descriptor["program_sha256"] = hash.finish().hex_encode()
	var manifest_bytes := JSON.stringify(descriptor, "\t").to_utf8_buffer()
	total += manifest_bytes.size() - descriptor_read.bytes.size()
	if total > MAX_EXPANDED_BYTES:
		return _failure("expanded_limit", "Web 导出与描述总大小超过 256 MiB，未生成 ZIP。")
	var reservation := _reserve_output(output_dir)
	if not reservation.ok:
		return reservation
	var target: String = reservation.path
	var lock_path: String = reservation.lock_path
	var temporary := target + ".tmp-" + Crypto.new().generate_random_bytes(8).hex_encode()
	var write_result := _write_archive(temporary, paths, sources, manifest_bytes)
	if not write_result.ok:
		_cleanup(temporary, lock_path)
		return write_result
	var normalized := _normalize_archive(temporary)
	if not normalized.ok:
		_cleanup(temporary, lock_path)
		return normalized
	var validation := _verify_archive(temporary, paths, descriptor, total)
	if not validation.ok:
		_cleanup(temporary, lock_path)
		return validation
	# A lock prevents two copies of this helper selecting the same name.
	# Recheck the final path immediately before publishing the completed ZIP.
	if FileAccess.file_exists(target) or DirAccess.dir_exists_absolute(target) or _contains_link(target):
		_cleanup(temporary, lock_path)
		return _failure("output_exists", "目标文件刚刚被创建，已停止打包以保护原文件。重新运行可选择新文件名。")
	var rename_error := DirAccess.rename_absolute(temporary, target)
	DirAccess.remove_absolute(lock_path)
	if rename_error != OK:
		DirAccess.remove_absolute(temporary)
		return _failure("publish_failed", "ZIP 已校验但无法保存到目标目录（" + error_string(rename_error) + "）。请检查写入权限与磁盘空间。")
	return {"ok": true, "path": target, "program_sha256": descriptor.program_sha256, "members": paths.size() + 1}


static func _descriptor_problem(descriptor: Dictionary) -> String:
	var keys: Array = descriptor.keys()
	keys.sort()
	var expected: Array = DESCRIPTOR_FIELDS.duplicate()
	expected.sort()
	if keys != expected:
		return "gec_descriptor.json 字段不符合 Web 描述格式。请重新复制接入包中的该文件。"
	if descriptor.get("host_version") != "4.7.2" or descriptor.get("sdk_version") != "0.1.0" or descriptor.get("protocol_version") != "gep/1":
		return "接入包版本不兼容；需要 Godot 4.7.2、SDK 0.1.0、gep/1。"
	if not descriptor.version is String or descriptor.version.is_empty() or descriptor.version.length() > 64:
		return "构建 version 必须是 1–64 字符的非空文本。"
	if not descriptor.schemas is Dictionary or descriptor.schemas.is_empty() or descriptor.schemas.size() > 16:
		return "构建 schemas 必须包含 1–16 个事件 schema。"
	if not descriptor.codebook is Dictionary or descriptor.codebook.is_empty():
		return "构建 codebook 必须是非空对象。"
	for definition: Variant in descriptor.schemas.values():
		if not definition is Dictionary:
			return "每个事件 schema 必须是对象。"
		var definition_keys: Array = definition.keys()
		definition_keys.sort()
		if definition_keys != ["id", "schema", "version"] or not definition.schema is Dictionary:
			return "每个事件 schema 只能包含 id、version、schema。"
	return ""


static func _contains_link(path: String) -> bool:
	var absolute := ProjectSettings.globalize_path(path).replace("\\", "/").simplify_path()
	while not absolute.is_empty():
		var parent := absolute.get_base_dir()
		if parent == absolute:
			break
		var directory := DirAccess.open(parent)
		if directory != null and directory.is_link(absolute.get_file()):
			return true
		absolute = parent
	return false


static func _file_check(path: String, limit: int) -> Dictionary:
	var absolute := ProjectSettings.globalize_path(path)
	if _contains_link(absolute):
		return _failure("linked_path", "不接受符号链接文件或目录：" + absolute)
	if not FileAccess.file_exists(absolute):
		return _failure("missing_file", "缺少必要文件：" + absolute + "\n请完成 Web 导出或重新复制 GEC 接入文件。")
	var file := FileAccess.open(absolute, FileAccess.READ)
	if file == null:
		return _failure("read_failed", "无法读取：" + absolute + "（" + error_string(FileAccess.get_open_error()) + "）")
	var size := file.get_length()
	file.close()
	if size <= 0:
		return _failure("empty_file", "必要文件为空：" + absolute)
	if size > limit:
		return _failure("file_limit", "文件超过打包限额（" + str(limit / (1024 * 1024)) + " MiB）：" + absolute)
	return {"ok": true, "size": size}


static func _read_bounded(path: String, limit: int) -> Dictionary:
	var check := _file_check(path, limit)
	if not check.ok:
		return check
	var file := FileAccess.open(ProjectSettings.globalize_path(path), FileAccess.READ)
	if file == null:
		return _failure("read_failed", "无法读取：" + path)
	var bytes := file.get_buffer(check.size)
	file.close()
	if bytes.size() != check.size:
		return _failure("source_changed", "读取时文件内容发生变化：" + path + "。请先等 Web 导出完成。")
	return {"ok": true, "bytes": bytes}


static func _hash_file(hash: HashingContext, path: String) -> Dictionary:
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		return _failure("read_failed", "无法读取：" + path)
	var length := file.get_length()
	if length <= 0 or length > MAX_FILE_BYTES:
		file.close()
		return _failure("source_changed", "摘要计算前文件大小发生变化：" + path)
	while file.get_position() < length:
		var bytes := file.get_buffer(mini(CHUNK_BYTES, length - file.get_position()))
		if bytes.is_empty() or hash.update(bytes) != OK:
			file.close()
			return _failure("read_failed", "摘要计算时读取失败：" + path)
	if file.get_length() != length:
		file.close()
		return _failure("source_changed", "摘要计算时文件大小发生变化：" + path)
	file.close()
	return {"ok": true}


static func _reserve_output(directory: String) -> Dictionary:
	for index in range(1, MAX_OUTPUT_NAMES + 1):
		var suffix := "" if index == 1 else "_" + str(index)
		var target := directory.path_join("moss_duel_gep_web" + suffix + ".zip")
		var lock := target + ".lock"
		if FileAccess.file_exists(target) or DirAccess.dir_exists_absolute(target) or _contains_link(target):
			continue
		if FileAccess.file_exists(lock) or DirAccess.dir_exists_absolute(lock) or _contains_link(lock):
			continue
		if DirAccess.make_dir_absolute(lock) == OK:
			return {"ok": true, "path": target, "lock_path": lock}
	return _failure("output_names_exhausted", "无法创建新 ZIP 文件：前 100 个文件名已存在或目录不可写。请换一个导出目录。")


static func _write_archive(path: String, paths: Array, sources: Dictionary, manifest: PackedByteArray) -> Dictionary:
	var writer := ZIPPacker.new()
	var error := writer.open(path)
	if error != OK:
		return _failure("zip_open", "无法写入临时 ZIP（" + error_string(error) + "）。")
	error = writer.start_file("manifest.json")
	if error == OK:
		error = writer.write_file(manifest)
	if error == OK:
		error = writer.close_file()
	for member: String in paths:
		if error != OK:
			break
		var source := FileAccess.open(sources[member], FileAccess.READ)
		if source == null:
			error = ERR_FILE_CANT_READ
			break
		var length := source.get_length()
		if length <= 0 or length > MAX_FILE_BYTES:
			source.close()
			error = ERR_FILE_CANT_READ
			break
		error = writer.start_file(member)
		while error == OK and source.get_position() < length:
			var chunk := source.get_buffer(mini(CHUNK_BYTES, length - source.get_position()))
			if chunk.is_empty():
				error = ERR_FILE_CANT_READ
				break
			error = writer.write_file(chunk)
		if source.get_length() != length:
			error = ERR_FILE_CANT_READ
		source.close()
		if error == OK:
			error = writer.close_file()
	var close_error := writer.close()
	if error != OK or close_error != OK:
		return _failure("zip_write", "写入 ZIP 失败，请检查磁盘空间与导出是否仍在进行。")
	return {"ok": true}


# ZIPPacker adds directory members automatically. GEP accepts regular members
# only, so copy file records unchanged and rebuild their central-directory
# offsets. This bounded ZIP32 rewrite keeps compressed bytes and CRCs intact.
static func _normalize_archive(path: String) -> Dictionary:
	var source_read := _read_bounded(path, MAX_ARCHIVE_BYTES)
	if not source_read.ok:
		return source_read
	var data: PackedByteArray = source_read.bytes
	var end := data.size() - 22
	if end < 0 or data.decode_u32(end) != 0x06054b50 or data.decode_u16(end + 20) != 0:
		return _failure("zip_layout", "临时 ZIP 结构不符合受支持的格式。")
	var count := data.decode_u16(end + 10)
	var central_size := data.decode_u32(end + 12)
	var central_offset := data.decode_u32(end + 16)
	if count > 256 or data.decode_u16(end + 4) != 0 or data.decode_u16(end + 6) != 0 or data.decode_u16(end + 8) != count or central_offset + central_size != end:
		return _failure("zip_layout", "临时 ZIP 的目录边界无效。")
	var records: Array[Dictionary] = []
	var cursor := int(central_offset)
	for unused in range(count):
		if cursor + 46 > end or data.decode_u32(cursor) != 0x02014b50:
			return _failure("zip_layout", "临时 ZIP 目录条目无效。")
		var name_length := data.decode_u16(cursor + 28)
		var extra_length := data.decode_u16(cursor + 30)
		var comment_length := data.decode_u16(cursor + 32)
		var length := 46 + name_length + extra_length + comment_length
		var local := int(data.decode_u32(cursor + 42))
		if cursor + length > end or local + 30 > central_offset or data.decode_u32(local) != 0x04034b50:
			return _failure("zip_layout", "临时 ZIP 文件偏移无效。")
		var name := data.slice(cursor + 46, cursor + 46 + name_length).get_string_from_utf8()
		records.append({"name": name, "local": local, "central": data.slice(cursor, cursor + length)})
		cursor += length
	if cursor != end:
		return _failure("zip_layout", "临时 ZIP 目录大小不一致。")
	records.sort_custom(func(left: Dictionary, right: Dictionary) -> bool: return left.local < right.local)
	var normalized_path := path + ".regular"
	if FileAccess.file_exists(normalized_path) or _contains_link(normalized_path):
		return _failure("zip_layout", "临时文件名冲突，已停止以保护原文件。")
	var output := FileAccess.open(normalized_path, FileAccess.WRITE)
	if output == null:
		return _failure("zip_write", "无法写入规范化 ZIP，请检查写入权限和磁盘空间。")
	var kept: Array[PackedByteArray] = []
	for index in range(records.size()):
		var record := records[index]
		var boundary: int = records[index + 1].local if index + 1 < records.size() else central_offset
		if boundary <= record.local:
			output.close()
			DirAccess.remove_absolute(normalized_path)
			return _failure("zip_layout", "临时 ZIP 文件记录重叠。")
		if record.name.ends_with("/"):
			continue
		var central: PackedByteArray = record.central
		central.encode_u32(42, output.get_position())
		kept.append(central)
		output.store_buffer(data.slice(record.local, boundary))
	var rebuilt_offset := output.get_position()
	for central: PackedByteArray in kept:
		output.store_buffer(central)
	var rebuilt_size := output.get_position() - rebuilt_offset
	var end_record := data.slice(end)
	end_record.encode_u16(8, kept.size())
	end_record.encode_u16(10, kept.size())
	end_record.encode_u32(12, rebuilt_size)
	end_record.encode_u32(16, rebuilt_offset)
	output.store_buffer(end_record)
	var write_error := output.get_error()
	output.close()
	if write_error != OK:
		DirAccess.remove_absolute(normalized_path)
		return _failure("zip_write", "规范化 ZIP 写入不完整，请检查磁盘空间。")
	var rename_error := DirAccess.rename_absolute(normalized_path, path)
	if rename_error != OK:
		DirAccess.remove_absolute(normalized_path)
		return _failure("zip_write", "无法完成 ZIP 规范化（" + error_string(rename_error) + "）。")
	return {"ok": true}


static func _verify_archive(path: String, expected_paths: Array, descriptor: Dictionary, expanded: int) -> Dictionary:
	var size_check := _file_check(path, MAX_ARCHIVE_BYTES)
	if not size_check.ok:
		return size_check
	var reader := ZIPReader.new()
	if reader.open(path) != OK:
		return _failure("zip_verify", "临时 ZIP 无法重新打开，未生成最终文件。")
	var actual: Array = Array(reader.get_files())
	actual.sort()
	var expected := expected_paths.duplicate()
	expected.append("manifest.json")
	expected.sort()
	if actual != expected:
		reader.close()
		return _failure("zip_members", "ZIP 成员校验失败，未生成最终文件。")
	var manifest := reader.read_file("manifest.json")
	var parsed: Variant = JSON.parse_string(manifest.get_string_from_utf8())
	if not parsed is Dictionary or parsed != descriptor:
		reader.close()
		return _failure("zip_manifest", "ZIP 构建描述校验失败，未生成最终文件。")
	var hash := HashingContext.new()
	hash.start(HashingContext.HASH_SHA256)
	var total := manifest.size()
	for member: String in expected_paths:
		var bytes := reader.read_file(member)
		if bytes.is_empty() or bytes.size() > MAX_FILE_BYTES or (bytes.size() >= 4 and bytes.decode_u32(0) in [0x04034b50, 0x06054b50, 0x08074b50]):
			reader.close()
			return _failure("zip_member_invalid", "ZIP 中出现空文件、超限文件或嵌套 ZIP：" + member)
		total += bytes.size()
		if total > MAX_EXPANDED_BYTES:
			reader.close()
			return _failure("expanded_limit", "ZIP 解压大小超过 256 MiB。")
		hash.update(member.to_utf8_buffer())
		hash.update(bytes)
	reader.close()
	if total != expanded or hash.finish().hex_encode() != descriptor.program_sha256:
		return _failure("zip_digest", "ZIP 内容与源文件摘要不一致；可能导出文件仍在变化。未生成最终文件。")
	return {"ok": true}


static func _cleanup(temporary: String, lock_path: String) -> void:
	if FileAccess.file_exists(temporary):
		DirAccess.remove_absolute(temporary)
	DirAccess.remove_absolute(lock_path)


static func _failure(code: String, message: String) -> Dictionary:
	return {"ok": false, "code": code, "error": message}
