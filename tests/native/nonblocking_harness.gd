extends SceneTree
func _initialize() -> void: call_deferred("run")
func run() -> void:
	var backend = load("res://addons/gec/native_backend.gd").new()
	backend.config = JSON.parse_string(FileAccess.get_file_as_string(OS.get_environment("GEP_TEST_CONFIG")))
	root.add_child(backend)
	assert(not (await backend.prepare({"expected_version":"synthetic-1"})).has("error"));backend.timer.stop()
	var task = load("res://task.gd").new(load("res://data/experiment_data.gd").new(backend))
	assert(not (await task.response("left",321.5)).has("error"))
	backend.call_deferred("flush")
	await create_timer(0.2).timeout
	assert(backend.sending)
	var started = Time.get_ticks_usec()
	assert(not (await task.response("right",217.25)).has("error"))
	assert(not (await backend.finish()).has("error"))
	var elapsed_ms = float(Time.get_ticks_usec()-started)/1000.0
	var saved = backend.read_session(backend.session_id)
	assert(backend.sending and saved.records.size() == 4 and saved.pending.size() == 4)
	var file = FileAccess.open(OS.get_environment("GEP_SYNTHETIC_STORAGE").path_join("saved_while_upload_pending"),FileAccess.WRITE)
	file.store_string(JSON.stringify({"response_and_finish_ms":elapsed_ms,"records":4,"pending":4}));file.close()
	while backend.sending: await create_timer(0.05).timeout
	await backend.flush()
	assert(backend.read_session(backend.session_id).kind == "cleaned")
	print("NATIVE_RECORDING_CONTINUED_WITH_ACK_HELD");quit()
