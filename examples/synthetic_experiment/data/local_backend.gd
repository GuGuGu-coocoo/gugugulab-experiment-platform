extends Node
## Independent local-only backend. This file has no GEC dependency or network calls.
var db
var lock_db
var records: Array = []
var buffer: Array = []
var session_id := ""
var segment_id := ""
var sequence := 0
var current := {"state":"unprepared"}
func identifier() -> String:
	var bytes = Crypto.new().generate_random_bytes(16)
	bytes[6] = (bytes[6] & 15) | 64;bytes[8] = (bytes[8] & 63) | 128
	var h = bytes.hex_encode()
	return h.substr(0,8)+"-"+h.substr(8,4)+"-"+h.substr(12,4)+"-"+h.substr(16,4)+"-"+h.substr(20,12)
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
	session_id = identifier();segment_id = identifier();current = {"state":"active"}
	return current
func record(kind: String,payload: Dictionary,schema: Dictionary,observed: Dictionary = {}) -> Dictionary:
	if current.state != "active" or buffer.size() >= 64: return {"error":"not_recording"}
	sequence += 1
	var event = {"protocol_version":"local/1","event_id":identifier(),"session_id":session_id,"segment_id":segment_id,"sequence":sequence,"event_type":kind,"schema_id":schema.id,"schema_version":schema.version,"payload":payload.duplicate(true)}
	if not observed.is_empty(): event.observed_time = observed.duplicate(true)
	buffer.append(event)
	return {"state":"buffered","event_id":event.event_id}
func commit(checkpoint: Dictionary = {}) -> Dictionary:
	var next = records.duplicate(true);next.append_array(buffer)
	for id in checkpoint.get("dependencies",[]):
		if not next.any(func(e):return e.event_id == id): return {"error":"checkpoint_dependencies"}
	var value = {"records":next,"checkpoint":checkpoint,"remote_status":"unsupported"}
	if not db.query("BEGIN IMMEDIATE"): return {"error":"local_commit"}
	if not db.query_with_bindings("INSERT INTO runs VALUES(?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",[session_id,JSON.stringify(value)]) or not db.query("COMMIT"):
		db.query("ROLLBACK");return {"error":"local_commit"}
	records = next;buffer.clear()
	return {"state":"local_committed"}
func finish() -> Dictionary:
	var result = await commit()
	if not result.has("error"): current = {"state":"local_committed","remote_status":"unsupported"}
	return result
func status() -> Dictionary:
	return current
func _exit_tree() -> void:
	if db != null: db.close_db()
	if lock_db != null: lock_db.close_db()
