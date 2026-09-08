extends Node
## Native SQLite transactions and asynchronous HTTP; no JavaScript dependency.
var db
var writer_lock
var config: Dictionary = {}
var local_only := false
var current := {"state":"unprepared"}
var session_id := ""
var segment := ""
var sequence := 0
var buffer: Array = []
var sending := false
var timer: Timer
func uuid() -> String:
	var bytes = Crypto.new().generate_random_bytes(16)
	bytes[6] = (bytes[6] & 15) | 64
	bytes[8] = (bytes[8] & 63) | 128
	var h = bytes.hex_encode()
	return h.substr(0,8)+"-"+h.substr(8,4)+"-"+h.substr(12,4)+"-"+h.substr(16,4)+"-"+h.substr(20,12)
func query(sql: String, values: Array = []) -> bool:
	return db.query_with_bindings(sql, values)
func rows() -> Array:
	if not query("SELECT value FROM sessions"): return []
	var result: Array = []
	for row in db.query_result: result.append(JSON.parse_string(row.value))
	return result
func read_session(id: String) -> Dictionary:
	if not query("SELECT value FROM sessions WHERE id=?",[id]) or db.query_result.is_empty(): return {}
	return JSON.parse_string(db.query_result[0].value)
func save(s: Dictionary) -> bool:
	if not query("BEGIN IMMEDIATE"): return false
	if not query("INSERT INTO sessions(id,value) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",[s.id,JSON.stringify(s)]):
		query("ROLLBACK")
		return false
	if not query("COMMIT"):
		query("ROLLBACK")
		return false
	return true
func initialize_queue() -> Dictionary:
	if not ClassDB.class_exists("SQLite"): return {"error":"sqlite_dependency_missing"}
	var storage = OS.get_environment("GEP_SYNTHETIC_STORAGE")
	if storage.is_empty(): storage = "user://gec"
	DirAccess.make_dir_recursive_absolute(storage)
	writer_lock = ClassDB.instantiate("SQLite")
	writer_lock.path = storage.path_join("writer.sqlite")
	if not writer_lock.open_db() or not writer_lock.query("BEGIN EXCLUSIVE"): return {"error":"writer_busy"}
	db = ClassDB.instantiate("SQLite")
	db.path = storage.path_join("queue.sqlite")
	if not db.open_db(): return {"error":"storage_open"}
	if not query("PRAGMA user_version"): return {"error":"storage_version"}
	if int(db.query_result[0].user_version) > 1: return {"error":"unsupported_storage_version"}
	if not query("PRAGMA journal_mode=WAL") or not query("PRAGMA synchronous=FULL") or not query("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,value TEXT NOT NULL)"): return {"error":"storage_prepare"}
	if not query("PRAGMA user_version=1"): return {"error":"storage_version"}
	segment = uuid()
	start_uploader()
	return {"state":"ready"}
func prepare(options: Dictionary = {}) -> Dictionary:
	if db == null:
		var prepared = initialize_queue()
		if prepared.has("error"): return prepared
	if local_only:
		session_id = uuid()
		if not save({"id":session_id,"kind":"session","records":[],"pending":[],"segments":[segment],"checkpoint":null,"completion":null}): return {"error":"local_commit"}
		current = {"state":"active"}
		return current
	if config.get("config_version") != "1" or config.get("protocol_version") != "gep/1" or config.get("purpose") != "synthetic": return {"error":"invalid_configuration"}
	var api: String = config.get("api_url","")
	if not api.begins_with("https://") and not api.begins_with("http://127.0.0.1:") and not api.begins_with("http://experiment.localhost:"): return {"error":"https_required"}
	if options.has("recovery_session"):
		var old = read_session(options.recovery_session)
		if old.is_empty() or old.get("kind") != "session" or old.completion != null: return {"error":"not_recoverable"}
		var can_resume = old.checkpoint != null and old.checkpoint.get("version") == 1 and old.checkpoint.get("strategy") == "trial_boundary_v1"
		var recovered = await http(old.config,"/v1/participant/sessions/"+old.id+"/recover",{"proof":old.proof,"permit":options.get("permit","")})
		if recovered.has("error"): return recovered
		old.context.token = recovered.token
		old.front_locked = false
		if can_resume: old.segments.append(segment)
		if not save(old): return {"error":"local_commit"}
		session_id = old.id;config = old.config
		current = {"state":"active" if can_resume else "data_only","checkpoint":old.checkpoint if can_resume else null}
		start_uploader()
		return current
	var draft: Dictionary = {}
	for old in rows():
		if old.get("kind") == "admission" and old.config == config: draft = old
	if draft.is_empty():
		draft = {"id":uuid(),"kind":"admission","config":config,"proof":Crypto.new().generate_random_bytes(32).hex_encode()}
		# Lock earlier front sessions and persist admission proof in one transaction.
		if not query("BEGIN IMMEDIATE"): return {"error":"local_commit"}
		var ok := true
		for old in rows():
			old.front_locked = true
			ok = query("UPDATE sessions SET value=? WHERE id=?",[JSON.stringify(old),old.id]) and ok
		ok = query("INSERT INTO sessions VALUES(?,?)",[draft.id,JSON.stringify(draft)]) and ok
		if not ok or not query("COMMIT"):
			query("ROLLBACK")
			return {"error":"local_commit"}
	var request = {"operation_id":draft.id,"proof":draft.proof}
	for k in ["instance_id","study_id","release_id","build_id"]: request[k] = config[k]
	request.merge(options)
	var response = await http(config,"/v1/participant/sessions",request)
	if response.has("error"): return response
	for k in ["instance_id","study_id","release_id","build_id"]:
		if response.get(k) != config[k]: return {"error":"admission_binding"}
	session_id = response.session_id
	var s = {"id":session_id,"kind":"session","config":config,"context":response,"proof":draft.proof,"records":[],"pending":[],"segments":[segment],"checkpoint":null,"completion":null,"complete_ack":null,"front_locked":false}
	if not query("BEGIN IMMEDIATE"): return {"error":"local_commit"}
	if not query("DELETE FROM sessions WHERE id=?",[draft.id]) or not query("INSERT INTO sessions VALUES(?,?)",[s.id,JSON.stringify(s)]) or not query("COMMIT"):
		query("ROLLBACK")
		return {"error":"local_commit"}
	current = {"state":"active"}
	start_uploader()
	return current
func start_uploader() -> void:
	if timer != null: return
	timer = Timer.new();timer.wait_time = 1.0;timer.timeout.connect(flush);add_child(timer);timer.start()
func http(target: Dictionary, path: String, body: Dictionary, token: String = "") -> Dictionary:
	var request = HTTPRequest.new()
	request.max_redirects = 0;request.timeout = 10.0;request.use_threads = true
	add_child(request)
	var headers = PackedStringArray(["Content-Type: application/json"])
	if not token.is_empty(): headers.append("Authorization: Bearer " + token)
	var error = request.request(target.api_url + path,headers,HTTPClient.METHOD_POST,JSON.stringify(body))
	if error != OK:
		request.queue_free();return {"error":"network_start"}
	var result = await request.request_completed
	request.queue_free()
	if result[0] != HTTPRequest.RESULT_SUCCESS or result[1] != 200: return {"error":"http_"+str(result[1])}
	var parsed = JSON.parse_string(result[3].get_string_from_utf8())
	return parsed if parsed is Dictionary else {"error":"invalid_response"}
func record(kind: String, payload: Dictionary, schema: Dictionary, observed: Dictionary = {}) -> Dictionary:
	if current.state != "active" or buffer.size() >= 64: return {"error":"not_recording_or_backpressure"}
	sequence += 1
	var event = {"protocol_version":"gep/1","event_id":uuid(),"session_id":session_id,"segment_id":segment,"sequence":sequence,"event_type":kind,"schema_id":schema.id,"schema_version":schema.version,"payload":payload.duplicate(true)}
	if not observed.is_empty(): event.observed_time = observed.duplicate(true)
	buffer.append(event)
	return {"state":"buffered","event_id":event.event_id}
func commit(checkpoint: Dictionary = {}) -> Dictionary:
	var s = read_session(session_id)
	if s.is_empty() or s.completion != null: return {"error":"not_recording"}
	for e in buffer:
		s.records.append(e)
		if not local_only: s.pending.append(e.event_id)
	if not checkpoint.is_empty():
		for id in checkpoint.dependencies:
			if not s.records.any(func(e): return e.event_id == id): return {"error":"checkpoint_dependencies"}
		s.checkpoint = checkpoint.duplicate(true)
	if not save(s): return {"error":"local_commit"}
	buffer.clear()
	return {"state":"local_committed"}
func finish() -> Dictionary:
	var result = await commit()
	if result.has("error"): return result
	var s = read_session(session_id)
	s.completion = {"event_ids":s.records.map(func(e):return e.event_id),"segment_ids":s.segments}
	if not save(s): return {"error":"local_commit"}
	current = {"state":"local_committed"}
	return current
func same_set(a: Array,b: Array) -> bool:
	if a.size() != b.size(): return false
	var seen: Dictionary = {}
	for x in a:
		if seen.has(x) or not b.has(x): return false
		seen[x] = true
	return true
func retry_later(s: Dictionary, message: String) -> void:
	current.error = message
	s.attempts = int(s.get("attempts",0))+1
	s.paused = message in ["http_401","http_403","http_409","http_422"] or s.attempts >= 32
	var bytes = Crypto.new().generate_random_bytes(1)
	s.retry_at = Time.get_unix_time_from_system()+minf(60.0,pow(2.0,minf(s.attempts,6))) * (1.0+float(bytes[0])/255.0)
	save(s)
func flush() -> void:
	if sending or local_only: return
	sending = true
	for s in rows():
		if s.get("kind") != "session" or s.get("paused",false) or s.get("retry_at",0) > Time.get_unix_time_from_system(): continue
		if s.get("complete_ack") != null and s.pending.is_empty() and s.checkpoint == null:
			save({"id":s.id,"kind":"cleaned","state":"remote_acknowledged"})
			continue
		if s.pending.size() > 0:
			var events: Array = s.records.filter(func(e):return s.pending.has(e.event_id)).slice(0,32)
			var batch = {"batch_id":uuid(),"events":events}
			var ack = await http(s.config,"/v1/participant/sessions/"+s.id+"/event-batches",batch,s.context.token)
			var ids: Array = events.map(func(e):return e.event_id)
			if ack.get("protocol_version") != "gep/1" or ack.get("instance_id") != s.config.instance_id or ack.get("session_id") != s.id or ack.get("batch_id") != batch.batch_id or not same_set(ack.get("accepted",[])+ack.get("duplicate",[]),ids):
				retry_later(read_session(s.id),ack.get("error","invalid_ack"));continue
			s = read_session(s.id)
			s.pending = s.pending.filter(func(id):return not ids.has(id))
			if not save(s): current.error = "ack_local_commit";continue
		if s.completion != null:
			var ack = await http(s.config,"/v1/participant/sessions/"+s.id+"/completion",s.completion,s.context.token)
			if ack.get("state") == "complete" and ack.get("protocol_version") == "gep/1" and ack.get("instance_id") == s.config.instance_id and ack.get("session_id") == s.id and same_set(ack.declaration.event_ids,s.completion.event_ids) and same_set(ack.declaration.segment_ids,s.completion.segment_ids) and ack.get("missing",[1]).is_empty() and s.pending.is_empty():
				s.complete_ack = ack;s.checkpoint = null
				if save(s) and save({"id":s.id,"kind":"cleaned","state":"remote_acknowledged"}):
					if s.id == session_id: current = {"state":"remote_acknowledged"}
	sending = false
func recovery_export() -> Dictionary:
	var s = read_session(session_id)
	if s.is_empty() or s.get("kind") != "session" or s.get("front_locked",false): return {"error":"recovery_export_unavailable"}
	var binding: Dictionary = {}
	for key in ["instance_id","study_id","release_id","build_id","protocol_version"]:
		if s.get("config",{}).has(key): binding[key] = s.config[key]
	return {"format_version":1,"session_id":s.id,"binding":binding,"records":s.records,"checkpoint":s.checkpoint,"pending":s.pending,"completion":s.completion}
func status() -> Dictionary:
	return current
func _exit_tree() -> void:
	if db != null: db.close_db()
	if writer_lock != null: writer_lock.close_db()
