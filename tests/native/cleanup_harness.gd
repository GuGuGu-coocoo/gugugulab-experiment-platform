extends SceneTree
# Faults interrupt only local durability; admissions, uploads and ACKs use real HTTP.
class FaultBackend:
	extends "res://addons/gec/native_backend.gd"
	var fault := ""
	var cleaning := false
	func pause_at_boundary() -> void:
		var f = FileAccess.open(OS.get_environment("GEP_SYNTHETIC_STORAGE").path_join("fault_boundary"), FileAccess.WRITE)
		f.store_string("ready"); f.close()
		OS.delay_msec(30000)
	func query(sql: String, values: Array = []) -> bool:
		if cleaning and sql == "COMMIT" and fault == "cleanup_before_commit": pause_at_boundary()
		return super.query(sql, values)
	func save(s: Dictionary) -> bool:
		if fault == "ack_abort" and s.get("kind") == "session" and s.pending.is_empty() and not s.records.is_empty(): return false
		if fault == "completion_abort" and s.get("complete_ack") != null: return false
		if fault == "cleanup_abort" and s.get("kind") == "cleaned": return false
		cleaning = s.get("kind") == "cleaned"
		var ok = super.save(s)
		if ok and cleaning and fault == "cleanup_after_commit": pause_at_boundary()
		cleaning = false
		return ok
func _initialize() -> void:
	call_deferred("run")
func run() -> void:
	var backend = FaultBackend.new()
	backend.config = JSON.parse_string(FileAccess.get_file_as_string(OS.get_environment("GEP_TEST_CONFIG")))
	root.add_child(backend)
	var args = OS.get_cmdline_user_args()
	if args.has("--reopen"):
		assert(not backend.initialize_queue().has("error"))
		backend.timer.stop()
		await backend.flush()
		for s in backend.rows(): assert(s.kind == "cleaned")
		print("NATIVE_CLEANUP_REOPEN_VERIFIED"); quit(); return
	assert(not (await backend.prepare({"expected_version":"synthetic-1"})).has("error"))
	backend.timer.stop()
	var data = load("res://data/experiment_data.gd").new(backend)
	var task = load("res://task.gd").new(data)
	assert(not (await task.response("left",321.5)).has("error"))
	assert(not (await task.response("right",217.25)).has("error"))
	assert(not (await backend.finish()).has("error"))
	var before = backend.read_session(backend.session_id)
	assert(before.records.size() == 4 and before.pending.size() == 4)
	backend.fault = args[0]
	await backend.flush()
	var saved = backend.read_session(backend.session_id)
	assert(saved.records == before.records)
	if backend.fault == "ack_abort":
		assert(saved.pending.size() == 4 and saved.checkpoint != null and saved.complete_ack == null)
	elif backend.fault == "completion_abort":
		assert(saved.pending.is_empty() and saved.checkpoint != null and saved.complete_ack == null)
	elif backend.fault == "cleanup_abort":
		assert(saved.pending.is_empty() and saved.checkpoint == null and saved.complete_ack.state == "complete")
	else: assert(false,"Kill-at-boundary test was not interrupted")
	backend.fault = ""
	await backend.flush()
	assert(backend.read_session(backend.session_id).kind == "cleaned")
	print("NATIVE_CLEANUP_FAILURE_PRESERVED_DATA"); quit()
