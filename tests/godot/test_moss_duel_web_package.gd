extends SceneTree
## Structural boundary checks only. Real Web export + GEP intake are separate.
## godot --headless --path examples/moss_duel --script <this.gd> -- <helper.gd> <new-test-dir>

var failures := 0
var checks := 0
var helper: GDScript
var base := ""
var source := ""
var sdk := ""
var descriptor_path := ""


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() != 2:
		printerr("Expected helper absolute path and a new isolated test directory.")
		quit(2)
		return
	helper = load(args[0])
	base = args[1].simplify_path()
	if helper == null or DirAccess.dir_exists_absolute(base) or FileAccess.file_exists(base):
		printerr("Helper missing or test directory already exists; nothing modified.")
		quit(2)
		return
	source = base.path_join("web")
	sdk = base.path_join("sdk")
	descriptor_path = base.path_join("descriptor.json")
	DirAccess.make_dir_recursive_absolute(source)
	DirAccess.make_dir_recursive_absolute(sdk)
	var descriptor := {
		"version": "moss-duel-package-boundary-test", "platform": "godot_web",
		"host_version": "4.7.2", "sdk_version": "0.1.0", "protocol_version": "gep/1",
		"program_sha256": "0".repeat(64),
		"schemas": {"game.event": {"id": "event", "version": "1", "schema": {"type": "object"}}},
		"codebook": {"note": "Structural packaging test; this is not a runnable experiment."},
	}
	_write(descriptor_path, JSON.stringify(descriptor).to_utf8_buffer())
	for name: String in helper.EXPORT_FILES:
		_write(source.path_join(name), ("fixture-runtime:" + name).to_utf8_buffer())
	_write(source.path_join("index.html"), '<html><head><script type="module" src="gec/bridge.js"></script></head></html>'.to_utf8_buffer())
	for name: String in helper.GEC_FILES:
		_write(sdk.path_join(name), ("// fixture-sdk:" + name).to_utf8_buffer())
	# Unrelated files and another ZIP are not collected by a recursive pack.
	_write(source.path_join("private-note.txt"), "do not package".to_utf8_buffer())
	_write(source.path_join("unrelated.zip"), PackedByteArray([80, 75, 3, 4, 1]))
	var result: Dictionary = helper.package_export(source.path_join("index.html"), descriptor_path, sdk)
	_expect(result.ok, "allowlisted package succeeds: " + str(result))
	if result.ok:
		_expect(result.path == base.path_join("moss_duel_gep_web.zip"), "output is export's parent")
		var archive := ZIPReader.new()
		_expect(archive.open(result.path) == OK, "published archive is readable")
		var names := archive.get_files()
		_expect(names.size() == 12, "exactly one manifest plus eleven runtime files")
		_expect(not names.has("web/private-note.txt") and not names.has("web/unrelated.zip"), "extras remain outside archive")
		var digest := HashingContext.new()
		digest.start(HashingContext.HASH_SHA256)
		var sorted_names: Array = Array(names)
		sorted_names.sort()
		for name: String in sorted_names:
			if name != "manifest.json":
				digest.update(name.to_utf8_buffer())
				digest.update(archive.read_file(name))
		var actual: Variant = JSON.parse_string(archive.read_file("manifest.json").get_string_from_utf8())
		_expect(actual.program_sha256 == digest.finish().hex_encode(), "digest uses sorted archive paths and bytes")
		archive.close()
		var original := FileAccess.get_file_as_bytes(result.path)
		var second: Dictionary = helper.package_export(source.path_join("index.html"), descriptor_path, sdk)
		_expect(second.ok and second.path.ends_with("moss_duel_gep_web_2.zip"), "existing output uses second bounded name")
		_expect(FileAccess.get_file_as_bytes(result.path) == original, "existing ZIP is preserved")
	var expected_source := FileAccess.get_file_as_bytes(source.path_join("index.pck"))
	DirAccess.remove_absolute(source.path_join("index.pck"))
	_expect_failure("missing_file", "missing runtime rejected")
	_write(source.path_join("index.pck"), PackedByteArray())
	_expect_failure("empty_file", "empty runtime rejected")
	var large := FileAccess.open(source.path_join("index.pck"), FileAccess.WRITE)
	large.resize(helper.MAX_FILE_BYTES + 1)
	large.close()
	_expect_failure("file_limit", "oversize runtime rejected before reading")
	DirAccess.remove_absolute(source.path_join("index.pck"))
	var dir := DirAccess.open(source)
	var linked := dir.create_link(descriptor_path, "index.pck")
	_expect(linked == OK, "symbolic link fixture created")
	if linked == OK:
		_expect_failure("linked_path", "linked runtime rejected")
	DirAccess.remove_absolute(source.path_join("index.pck"))
	_write(source.path_join("index.pck"), PackedByteArray([80, 75, 3, 4, 1, 2, 3]))
	_expect_failure("zip_member_invalid", "nested ZIP payload rejected")
	_write(source.path_join("index.pck"), expected_source)
	var expected_html := FileAccess.get_file_as_bytes(source.path_join("index.html"))
	_write(source.path_join("index.html"), "<html>unconnected export</html>".to_utf8_buffer())
	_expect_failure("bridge_missing", "missing module bridge rejected")
	_write(source.path_join("index.html"), expected_html)
	var wrong: Dictionary = helper.package_export(source.path_join("game.html"), descriptor_path, sdk)
	_expect(not wrong.ok and wrong.code == "wrong_entry", "wrong entry name rejected")
	_write(descriptor_path, "[]".to_utf8_buffer())
	_expect_failure("descriptor_json", "non-object descriptor rejected")
	descriptor["administrator_password"] = "test-value-never-in-output"
	_write(descriptor_path, JSON.stringify(descriptor).to_utf8_buffer())
	_expect_failure("descriptor_invalid", "unknown descriptor field rejected")
	_expect(FileAccess.get_file_as_bytes(source.path_join("index.pck")) == expected_source, "original export survives all failures")
	var top_files := DirAccess.get_files_at(base)
	_expect(top_files.size() == 3, "no failed temporary files remain")
	_expect(DirAccess.get_directories_at(base).size() == 2, "no output locks remain")
	# Exercise the finite output namespace without creating more ZIP data.
	for index in range(3, helper.MAX_OUTPUT_NAMES + 1):
		_write(base.path_join("moss_duel_gep_web_" + str(index) + ".zip"), "occupied".to_utf8_buffer())
	descriptor.erase("administrator_password")
	_write(descriptor_path, JSON.stringify(descriptor).to_utf8_buffer())
	_expect_failure("output_names_exhausted", "100 occupied output names stop clearly")
	print("MOSS_DUEL_PACKAGE_BOUNDARY: ", checks, " checks, ", failures, " failures; fixtures: ", base)
	quit(0 if failures == 0 else 1)


func _write(path: String, bytes: PackedByteArray) -> void:
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file == null:
		printerr("Cannot write fixture: " + path)
		quit(2)
		return
	file.store_buffer(bytes)
	file.close()


func _expect(ok: bool, label: String) -> void:
	checks += 1
	if not ok:
		failures += 1
		printerr("FAIL: " + label)


func _expect_failure(code: String, label: String) -> void:
	var result: Dictionary = helper.package_export(source.path_join("index.html"), descriptor_path, sdk)
	_expect(not result.ok and result.get("code") == code, label + ": " + str(result))
