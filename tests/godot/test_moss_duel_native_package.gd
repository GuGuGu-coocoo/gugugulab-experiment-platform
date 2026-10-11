extends SceneTree
## Packaging boundary checks, with optional real previously exported inputs.
## -- <helper.gd> <NEW-directory> [<Windows.exe> <macOS.zip> <extension-file>]
## Synthetic binaries check packaging only; GEP separately validates real ones.
var helper: GDScript
var base := ""
var checks := 0
var failures := 0
var template: Dictionary
var entry := ""
var extension := ""
var mac := ""

func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() not in [2, 5]:
		printerr("Expected helper path, NEW fixture directory, optional three real input paths.")
		quit(2)
		return
	helper = load(args[0])
	base = args[1].simplify_path()
	if helper == null or helper._occupied(base):
		printerr("Helper missing or fixture path occupied; nothing modified.")
		quit(2)
		return
	DirAccess.make_dir_recursive_absolute(base)
	template = {"version": "moss-duel-native-boundary", "platform": "godot_web",
		"host_version": "4.7.2", "sdk_version": "0.1.0", "protocol_version": "gep/1",
		"program_sha256": "0".repeat(64),
		"schemas": {"game.duel": {"id": "duel_event", "version": "1", "schema": {"type": "object"}}},
		"codebook": {"note": "Synthetic structural fixtures are not runnable programs."}}
	entry = base.path_join("windows/Moss Duel/Moss Duel.exe")
	extension = base.path_join("extension.gdextension")
	DirAccess.make_dir_recursive_absolute(entry.get_base_dir())
	_write(entry, "synthetic exe".to_utf8_buffer())
	_write(entry.get_basename() + ".pck", "synthetic pck".to_utf8_buffer())
	_write(entry.get_base_dir().path_join(helper.SQLITE_DLL), "synthetic dll".to_utf8_buffer())
	_write(extension, "[configuration]\n".to_utf8_buffer())
	_write(entry.get_base_dir().path_join("connection.json"), "must not be packaged".to_utf8_buffer())
	var original_template := template.duplicate(true)
	var win: Dictionary = helper._windows(entry, template, extension)
	_expect(not win.has("error"), "Windows allowlist packages: " + str(win))
	if not win.has("error"):
		_check_pair(win, "windows_x64")
		var reader := ZIPReader.new()
		reader.open(win.archive)
		_expect(not reader.get_files().has("Moss Duel/connection.json"), "extra configuration stays outside Windows archive")
		reader.close()
		var original_hash := FileAccess.get_sha256(win.archive)
		var second: Dictionary = helper._windows(entry, template, extension)
		_expect(not second.has("error") and second.archive.ends_with("_2.zip"), "existing results get a fresh bounded name")
		_expect(FileAccess.get_sha256(win.archive) == original_hash and FileAccess.get_sha256(second.get("archive", "")) == original_hash, "old ZIP unchanged and identical input produces identical ZIP")
	_expect(template == original_template, "packaging does not mutate the input descriptor")
	mac = base.path_join("native/source.zip")
	DirAccess.make_dir_recursive_absolute(mac.get_base_dir())
	_mac_fixture(mac)
	var apple: Dictionary = helper._macos(mac, template)
	_expect(not apple.has("error"), "macOS package succeeds: " + str(apple))
	if not apple.has("error"):
		_check_pair(apple, "macos_arm64")
		_expect(FileAccess.get_sha256(apple.archive) == FileAccess.get_sha256(mac), "macOS export bytes are preserved exactly")
	_boundary_failures()
	_reservation_boundaries()
	if args.size() == 5:
		_real_packages(args)
	print("MOSS_NATIVE_PACKAGE_BOUNDARY: ", checks, " checks, ", failures, " failures; fixtures: ", base)
	quit(0 if failures == 0 else 1)

func _boundary_failures() -> void:
	var pck := entry.get_basename() + ".pck"
	var original := FileAccess.get_file_as_bytes(pck)
	DirAccess.remove_absolute(pck)
	_expect(helper._windows(entry, template, extension).has("error"), "missing PCK rejected")
	_write(pck, PackedByteArray())
	_expect(helper._windows(entry, template, extension).has("error"), "empty PCK rejected")
	var large := FileAccess.open(pck, FileAccess.WRITE)
	large.resize(helper.MAX_EXPANDED_BYTES + 1)
	large.close()
	_expect(helper._windows(entry, template, extension).has("error"), "oversize source rejected before reading")
	DirAccess.remove_absolute(pck)
	var directory := DirAccess.open(entry.get_base_dir())
	_expect(directory.create_link(extension, pck.get_file()) == OK, "file link fixture created")
	_expect(helper._windows(entry, template, extension).has("error"), "source file link refused")
	DirAccess.remove_absolute(pck)
	_write(pck, original)
	var alias := base.path_join("alias")
	_expect(DirAccess.open(base).create_link(entry.get_base_dir().get_base_dir(), "alias") == OK, "ancestor link fixture created")
	_expect(helper._windows(alias.path_join("Moss Duel/Moss Duel.exe"), template, extension).has("error"), "source ancestor link refused")
	DirAccess.remove_absolute(alias)
	var invalid := template.duplicate(true)
	invalid["administrator_password"] = "synthetic-never-distribute"
	_expect(helper._windows(entry, invalid, extension).has("error"), "extra descriptor credential field refused")
	var oversize := template.duplicate(true)
	oversize.codebook.note = "x".repeat(helper.MAX_DESCRIPTOR_BYTES + 1)
	var before := DirAccess.get_files_at(entry.get_base_dir().get_base_dir())
	_expect(helper._windows(entry, oversize, extension).has("error"), "failed descriptor write never publishes a ZIP")
	_expect(DirAccess.get_files_at(entry.get_base_dir().get_base_dir()) == before, "failed descriptor leaves previous output files unchanged")
	_expect(DirAccess.get_directories_at(entry.get_base_dir().get_base_dir()).size() == 1, "failure removes its own reservation")
	var private_zip := base.path_join("native/contains_config.zip")
	_mac_fixture(private_zip, "Moss Duel.app/Contents/Resources/connection.json")
	_expect(helper._macos(private_zip, template).has("error"), "macOS source ZIP with configuration refused")
	var unsafe_zip := base.path_join("native/traversal.zip")
	_mac_fixture(unsafe_zip, "../outside.txt")
	_expect(helper._macos(unsafe_zip, template).has("error"), "macOS source ZIP traversal refused")
	var malformed := base.path_join("native/truncated.zip")
	var bytes := FileAccess.get_file_as_bytes(mac)
	_write(malformed, bytes.slice(0, bytes.size() - 4))
	_expect(helper._macos(malformed, template).has("error"), "truncated source ZIP refused")
	var declared := base.path_join("native/declared_oversize.zip")
	for cursor in range(bytes.size() - 46):
		if bytes.decode_u32(cursor) == 0x02014b50:
			bytes.encode_u32(cursor + 24, helper.MAX_EXPANDED_BYTES + 1)
			break
	_write(declared, bytes)
	_expect(helper._macos(declared, template).has("error"), "oversize ZIP metadata refused before decompression")
	_expect(FileAccess.get_file_as_bytes(pck) == original, "failed packaging preserves original export")

func _reservation_boundaries() -> void:
	var output := base.path_join("output")
	DirAccess.make_dir_recursive_absolute(output)
	_write(output.path_join("pair.zip"), "existing result".to_utf8_buffer())
	DirAccess.make_dir_absolute(output.path_join("pair_2_descriptor.json"))
	var outside := base.path_join("must_not_create.txt")
	_expect(DirAccess.open(output).create_link(outside, "pair_3_descriptor.json") == OK, "dangling output link fixture created")
	DirAccess.make_dir_absolute(output.path_join("pair_4.zip"))
	DirAccess.make_dir_absolute(output.path_join("pair_5.zip.lock"))
	var reserved: Dictionary = helper._reserve(output, "pair")
	_expect(not reserved.has("error") and reserved.archive.ends_with("pair_6.zip"), "files, directories, dangling links and old locks remain occupied")
	var concurrent: Dictionary = helper._reserve(output, "pair")
	_expect(not concurrent.has("error") and concurrent.archive.ends_with("pair_7.zip"), "second helper uses a separate reservation")
	helper._cleanup(concurrent)
	_write(reserved.archive_tmp, "own temporary archive".to_utf8_buffer())
	var description := template.duplicate(true)
	description.program_sha256 = FileAccess.get_sha256(reserved.archive_tmp)
	_write(reserved.archive, "new external result".to_utf8_buffer())
	var result: Dictionary = helper._publish(reserved, description)
	_expect(result.has("error"), "late final path collision blocks publication")
	_expect(FileAccess.get_file_as_bytes(reserved.archive).get_string_from_utf8() == "new external result", "late collision is not overwritten or deleted")
	_expect(not helper._occupied(reserved.lock), "failed publish cleans its reservation")
	_expect(not FileAccess.file_exists(outside), "dangling link target was not created")
	_expect(FileAccess.get_file_as_bytes(output.path_join("pair.zip")).get_string_from_utf8() == "existing result", "pre-existing final output preserved")
	for index in range(1, 101):
		var stem := "full" if index == 1 else "full_" + str(index)
		_write(output.path_join(stem + ".zip"), "occupied".to_utf8_buffer())
	_expect(helper._reserve(output, "full").has("error"), "100 occupied names fail clearly")

func _real_packages(args: PackedStringArray) -> void:
	var root := base.path_join("real")
	var win := root.path_join("windows/Moss Duel/Moss Duel.exe")
	var apple := root.path_join("native/source.zip")
	DirAccess.make_dir_recursive_absolute(win.get_base_dir())
	DirAccess.make_dir_recursive_absolute(apple.get_base_dir())
	for suffix in [".exe", ".pck"]:
		_expect(DirAccess.copy_absolute(args[2].get_basename() + suffix, win.get_basename() + suffix) == OK, "real Windows input copied without modifying original")
	_expect(DirAccess.copy_absolute(args[2].get_base_dir().path_join(helper.SQLITE_DLL), win.get_base_dir().path_join(helper.SQLITE_DLL)) == OK, "real DLL copied")
	_expect(DirAccess.copy_absolute(args[3], apple) == OK, "real macOS input copied")
	var windows_result: Dictionary = helper._windows(win, template, args[4])
	var mac_result: Dictionary = helper._macos(apple, template)
	_expect(not windows_result.has("error"), "real Windows packaging: " + str(windows_result))
	_expect(not mac_result.has("error"), "real macOS packaging: " + str(mac_result))
	_write(base.path_join("real_results.json"), JSON.stringify({"windows": windows_result, "macos": mac_result}).to_utf8_buffer())

func _check_pair(result: Dictionary, platform: String) -> void:
	var descriptor: Variant = JSON.parse_string(FileAccess.get_file_as_string(result.descriptor))
	_expect(descriptor is Dictionary and descriptor.platform == platform and descriptor.program_sha256 == FileAccess.get_sha256(result.archive), "published descriptor matches complete ZIP")
	_expect(not helper._occupied(result.archive + ".lock"), "successful reservation cleaned")

func _mac_fixture(path: String, extra: String = "") -> void:
	var writer := ZIPPacker.new()
	writer.open(path)
	var files := {"Moss Duel.app/Contents/Info.plist": "synthetic plist",
		"Moss Duel.app/Contents/Resources/Moss Duel.pck": "synthetic pck",
		"Moss Duel.app/Contents/Frameworks/libgdsqlite.macos.template_release.framework/libgdsqlite": "synthetic framework"}
	if not extra.is_empty():
		files[extra] = "must not publish"
	for name in files:
		writer.start_file(name, 420, helper.ZIP_TIMESTAMP)
		writer.write_file(str(files[name]).to_utf8_buffer())
		writer.close_file()
	writer.close()

func _write(path: String, bytes: PackedByteArray) -> void:
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file == null:
		printerr("Cannot write owned fixture: " + path)
		quit(2)
		return
	file.store_buffer(bytes)
	file.close()

func _expect(condition: bool, message: String) -> void:
	checks += 1
	if not condition:
		failures += 1
		printerr("FAIL: " + message)
