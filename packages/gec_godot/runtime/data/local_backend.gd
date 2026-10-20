extends Node
## Independent local-only backend. This file has no GEC dependency or network calls.
## A local test is only finished when both the record commit and the completion
## set are durably stored: any storage failure is reported as ``storage_error``
## and the records stay available for a retry or an explicit result export.
var db
var lock_db
var records: Array = []
var buffer: Array = []
var session_id := ""
var segment_id := ""
var sequence := 0
var current := {"state":"unprepared"}
var fault_injection := ""  ## test-only hook; "store_failure" simulates a write that fails after a successful open
func identifier() -> String:
	var bytes = Crypto.new().generate_random_bytes(16)
	bytes[6] = (bytes[6] & 15) | 64;bytes[8] = (bytes[8] & 63) | 128
	var h = bytes.hex_encode()
	return h.substr(0,8)+"-"+h.substr(8,4)+"-"+h.substr(12,4)+"-"+h.substr(16,4)+"-"+h.substr(20,12)
func local_test() -> bool:
	return true
func results_directory() -> String:
	var directory = OS.get_environment("GEP_SYNTHETIC_RESULTS")
	if directory.is_empty(): directory = "user://local_results"
	return ProjectSettings.globalize_path(directory)
func initialize_queue() -> Dictionary:
	if not ClassDB.class_exists("SQLite"): return {"error":"sqlite_dependency_missing"}
	var storage = OS.get_environment("GEP_SYNTHETIC_STORAGE")
	if storage.is_empty(): storage = "user://local_experiment"
	DirAccess.make_dir_recursive_absolute(storage)
	lock_db = ClassDB.instantiate("SQLite");lock_db.path = storage.path_join("writer.sqlite")
	if not lock_db.open_db() or not lock_db.query("BEGIN EXCLUSIVE"): return {"error":"writer_busy"}
	db = ClassDB.instantiate("SQLite");db.path = storage.path_join("local.sqlite")
	if not db.open_db() or not db.query("PRAGMA synchronous=FULL") or not db.query("CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, value TEXT NOT NULL)"): return {"error":"storage_prepare"}
	return {"state":"ready"}
func prepare(_options: Dictionary = {}) -> Dictionary:
	if db == null:
		var prepared = initialize_queue()
		if prepared.has("error"): return prepared
	session_id = identifier();segment_id = identifier();current = {"state":"active","remote_status":"unsupported"}
	return current
func record(kind: String,payload: Dictionary,schema: Dictionary,observed: Dictionary = {}) -> Dictionary:
	if current.state != "active" or buffer.size() >= 64: return {"error":"not_recording"}
	# The backend still owns the schema contract: a declaration without a
	# usable id/version is a clear error instead of an engine-level key error,
	# and nothing enters the raw buffer for it.
	if not (schema.get("id") is String) or schema.id.is_empty() or not (schema.get("version") is String) or schema.version.is_empty():
		return {"error":"invalid_schema"}
	sequence += 1
	var event = {"protocol_version":"local/1","event_id":identifier(),"session_id":session_id,"segment_id":segment_id,"sequence":sequence,"event_type":kind,"schema_id":schema.id,"schema_version":schema.version,"payload":payload.duplicate(true)}
	if not observed.is_empty(): event.observed_time = observed.duplicate(true)
	buffer.append(event)
	return {"state":"buffered","event_id":event.event_id}
func _store_run(value: Dictionary) -> bool:
	if db == null: return false
	if not db.query("BEGIN IMMEDIATE"): return false
	if not db.query_with_bindings("INSERT INTO runs VALUES(?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",[session_id,JSON.stringify(value)]) or not db.query("COMMIT"):
		db.query("ROLLBACK");return false
	return true
func commit(checkpoint: Dictionary = {}) -> Dictionary:
	var next = records.duplicate(true);next.append_array(buffer)
	for id in checkpoint.get("dependencies",[]):
		if not next.any(func(e):return e.event_id == id): return {"error":"checkpoint_dependencies"}
	if not _store_run({"records":next,"checkpoint":checkpoint,"remote_status":"unsupported"}): return {"error":"local_commit"}
	records = next;buffer.clear()
	return {"state":"local_committed"}
func finish() -> Dictionary:
	var result = await commit()
	if result.has("error"):
		current = {"state":"storage_error","error":"storage_error","remote_status":"unsupported"}
		return {"error":"storage_error"}
	var completion = {"event_ids":records.map(func(e): return e.event_id),"segment_ids":[segment_id]}
	if not _store_run({"records":records,"checkpoint":null,"completion":completion,"remote_status":"unsupported","state":"finished_saved"}):
		current = {"state":"storage_error","error":"storage_error","remote_status":"unsupported"}
		return {"error":"storage_error"}
	current = {"state":"finished_saved","remote_status":"unsupported"}
	return {"state":"finished_saved","remote_status":"unsupported"}
func results_jsonl() -> String:
	var lines := PackedStringArray()
	for record in records:
		lines.append(JSON.stringify(record))
	return "\n".join(lines) + ("\n" if not lines.is_empty() else "")
func _write_atomic(target: String, content: String) -> bool:
	## Write a same-directory temporary file, verify the complete content, then
	## replace the target. A failed open, write, flush, verification or rename
	## removes the temporary and keeps the previous target untouched, so a
	## reported save always means the full document is on disk.
	var temporary = target + ".part-" + identifier()
	var file = FileAccess.open(temporary, FileAccess.WRITE)
	if file == null: return false
	if fault_injection == "store_failure":
		## Injected failure: the open succeeded and the write is made to fail.
		file.close()
		DirAccess.remove_absolute(temporary)
		return false
	if not file.store_string(content):
		file.close();DirAccess.remove_absolute(temporary);return false
	file.flush()
	if file.get_error() != OK:
		file.close();DirAccess.remove_absolute(temporary);return false
	file.close()
	if FileAccess.get_file_as_string(temporary) != content:
		DirAccess.remove_absolute(temporary);return false
	if DirAccess.rename_absolute(temporary, target) != OK:
		DirAccess.remove_absolute(temporary);return false
	return true
func save_results(path: String = "") -> Dictionary:
	var directory = results_directory()
	if path.is_empty():
		if DirAccess.make_dir_recursive_absolute(directory) != OK: return {"error":"results_export_unavailable"}
		path = directory.path_join("local-results-"+session_id+".jsonl")
	else:
		# The feedback must point at the actual final file, not at the default.
		directory = path.get_base_dir()
	if not _write_atomic(path, results_jsonl()): return {"error":"results_export_unavailable"}
	return {"state":"results_saved","path":path,"directory":directory}
func recovery_export() -> Dictionary:
	return {"format_version":1,"session_id":session_id,"records":records.duplicate(true),"remote_status":"unsupported"}
func status() -> Dictionary:
	return current
func _exit_tree() -> void:
	if db != null: db.close_db()
	if lock_db != null: lock_db.close_db()
