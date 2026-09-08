extends SceneTree
class FaultBackend:
	extends "res://addons/gec/native_backend.gd"
	var fault_mode := ""
	func query(sql: String,values: Array = []) -> bool:
		if sql == "COMMIT" and fault_mode == "pause":
			var f = FileAccess.open(OS.get_environment("GEP_SYNTHETIC_STORAGE").path_join("before_commit"),FileAccess.WRITE)
			f.store_string("transaction open");f.close()
			OS.delay_msec(30000)
		if sql == "COMMIT" and fault_mode == "abort": return false
		return super.query(sql,values)
func _initialize() -> void:
	call_deferred("run")
func run() -> void:
	var backend = FaultBackend.new();backend.local_only = true;root.add_child(backend)
	var prepared = await backend.prepare()
	if prepared.has("error"): print(prepared.error);quit(2);return
	var first = await backend.record("exp.rt",{"rt_ms":321.5},{"id":"rt","version":"1"})
	var result = await backend.commit({"version":1,"dependencies":[first.event_id],"next_trial":1})
	assert(result.state == "local_committed")
	var second = await backend.record("exp.rt",{"rt_ms":217.25},{"id":"rt","version":"1"})
	backend.fault_mode = "pause" if OS.get_cmdline_user_args().has("--pause") else "abort"
	result = await backend.commit({"version":1,"dependencies":[first.event_id,second.event_id],"next_trial":2})
	if backend.fault_mode == "abort":
		assert(result.error == "local_commit")
		var saved = backend.read_session(backend.session_id)
		assert(saved.records.size() == 1 and saved.checkpoint.next_trial == 1)
		print("TRANSACTION_FAILURE_PRESERVED_BOUNDARY")
	quit()
