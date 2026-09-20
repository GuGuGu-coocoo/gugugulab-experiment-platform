extends SceneTree
## Native verification of the recovery-export automation entry (P0307WR).
##
## This runs the real native SQLite backend and the real reusable GEC shell in an
## isolated store (GEP_SYNTHETIC_STORAGE) without a server and without a dialog.
## It proves that shell.export_recovery_to() writes exactly the document the GUI
## save dialog writes, keeps the local queue (records/pending) untouched, contains
## no session token or recovery proof, and reports an error for a write path that
## cannot be opened. It never touches a real participant store.
##
## Run:
##   GEP_SYNTHETIC_STORAGE=<fresh dir> godot --headless \
##     --path examples/synthetic_experiment --script tests/native/recovery_export_harness.gd

const Shell = preload("res://addons/gec/shell.gd")
const Backend = preload("res://addons/gec/native_backend.gd")

const TOKEN = "SYNTHETIC-TOKEN-MUST-NOT-BE-EXPORTED"
const PROOF = "SYNTHETIC-PROOF-MUST-NOT-BE-EXPORTED"
const PARTICIPANT_CODE = "SYNTHETIC-CODE-MUST-NOT-BE-EXPORTED"

var failures: Array = []

func _initialize() -> void:
	call_deferred("run")

func check(condition: bool, label: String) -> void:
	if not condition:
		failures.append(label)

func synthetic_session() -> Dictionary:
	return {
		"id": "11111111-2222-3333-4444-555555555555",
		"kind": "session",
		"records": [
			{"event_id": "aaaaaaaa-0000-0000-0000-000000000001", "event_type": "exp.rt",
				"payload": {"trial_id": "t1", "choice": "left", "rt_ms": 321.5}},
			{"event_id": "aaaaaaaa-0000-0000-0000-000000000002", "event_type": "exp.rt",
				"payload": {"trial_id": "t2", "choice": "right", "rt_ms": 217.25}},
		],
		"pending": ["aaaaaaaa-0000-0000-0000-000000000002"],
		"segments": ["segment-1"],
		"checkpoint": {"version": 1, "strategy": "trial_boundary_v1", "next_trial": 2, "dependencies": []},
		"completion": null,
		"front_locked": false,
		"config": {
			"config_version": "1", "purpose": "synthetic", "protocol_version": "gep/1",
			"instance_id": "99999999-1111-2222-3333-444444444444",
			"study_id": "99999999-aaaa-bbbb-cccc-dddddddddddd",
			"release_id": "99999999-eeee-ffff-0000-111111111111",
			"build_id": "99999999-2222-3333-4444-555555555555",
			"api_url": "http://127.0.0.1:1",
		},
		"context": {"token": TOKEN, "participant_code": PARTICIPANT_CODE},
		"proof": PROOF,
	}

func run() -> void:
	await process_frame
	var storage := OS.get_environment("GEP_SYNTHETIC_STORAGE")
	if storage.is_empty():
		print("NATIVE_RECOVERY_EXPORT_FAILED ", JSON.stringify(["GEP_SYNTHETIC_STORAGE is required for isolation"]))
		quit(1)
		return

	var backend := Backend.new()
	backend.config = {"config_version": "1", "purpose": "synthetic"}
	root.add_child(backend)
	var shell = Shell.new()
	root.add_child(shell)
	var prepared: Dictionary = shell.setup(backend)
	if prepared.has("error"):
		print("NATIVE_RECOVERY_EXPORT_FAILED ", JSON.stringify([str(prepared.error)]))
		quit(1)
		return
	# The isolated check must not let the uploader touch the seeded queue.
	if backend.timer != null:
		backend.timer.stop()

	var session := synthetic_session()
	check(backend.save(session), "the synthetic session is committed to the native store")
	backend.session_id = session.id
	check(backend.read_session(session.id).pending.size() == 1, "the seeded pending queue exists before the export")
	var before := JSON.stringify(backend.read_session(session.id))

	var direct: Dictionary = await backend.recovery_export()
	check(not direct.has("error"), "the backend produces the recovery document")
	check(direct.pending.size() == 1 and direct.records.size() == 2, "the document carries records and pending")

	# (1) A write path that cannot be opened must report an error and write nothing.
	var missing := storage.path_join("missing-dir").path_join("out.json")
	var invalid: Dictionary = await shell.export_recovery_to(missing)
	check(invalid.get("error", "") == "recovery_export_unavailable", "an unopenable write path returns the export error")
	check(not FileAccess.file_exists(missing), "the failed export wrote no file")

	# (2) The real entry writes the same document the GUI save dialog writes.
	var folder := storage.path_join("导出 目录 'quoted'")
	DirAccess.make_dir_recursive_absolute(folder)
	var target := folder.path_join("recovery 数据.json")
	var exported: Dictionary = await shell.export_recovery_to(target)
	check(exported.get("state", "") == "exported" and exported.get("path", "") == target,
		"the automation entry reports the exported path")
	check(FileAccess.file_exists(target), "the automation entry writes the file")
	if not FileAccess.file_exists(target):
		print("NATIVE_RECOVERY_EXPORT_FAILED ", JSON.stringify(failures))
		quit(1)
		return
	var text := FileAccess.get_file_as_string(target)
	check(text == JSON.stringify(direct), "the file is byte-identical to the GUI export document")
	var parsed = JSON.parse_string(text)
	check(parsed is Dictionary, "the written document is valid JSON")
	if parsed is Dictionary:
		check(parsed.has("format_version") and parsed.has("session_id") and parsed.has("binding"),
			"the document keeps the GUI format keys")
		check(parsed.get("session_id", "") == session.id, "the document binds the exported session")
		check(parsed.get("pending", []).size() == 1, "the document keeps the pending queue")
		check(not parsed.has("context") and not parsed.has("proof"), "the document has no credential fields")
		check(parsed.get("binding", {}).get("instance_id", "") == session.config.instance_id
			and parsed.get("binding", {}).get("release_id", "") == session.config.release_id,
			"the document keeps the instance/release binding")

	# (3) No secret of the local session may appear in the export.
	check(not text.contains(TOKEN) and not text.contains(PROOF) and not text.contains(PARTICIPANT_CODE),
		"the export contains no session token, proof or participant code")

	# (4) The export never deletes the local queue.
	var after := JSON.stringify(backend.read_session(session.id))
	check(after == before, "the local session is unchanged after the export")
	var stored = backend.read_session(session.id)
	check(stored.get("kind", "") == "session" and stored.pending.size() == 1 and stored.records.size() == 2,
		"the local session, records and pending queue are preserved")

	if failures.is_empty():
		print("NATIVE_RECOVERY_EXPORT_VERIFIED")
		quit(0)
	else:
		print("NATIVE_RECOVERY_EXPORT_FAILED ", JSON.stringify(failures))
		quit(1)
