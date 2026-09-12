extends SceneTree
func _initialize() -> void: call_deferred("run")
func run() -> void:
	var backend = load("res://addons/gec/native_backend.gd").new()
	backend.config = JSON.parse_string(FileAccess.get_file_as_string(OS.get_environment("GEP_TEST_CONFIG")))
	root.add_child(backend)
	assert(not (await backend.prepare({"expected_version":"synthetic-1"})).has("error")); backend.timer.stop()
	var task = load("res://task.gd").new(load("res://data/experiment_data.gd").new(backend))
	assert(not (await task.response("left",321.5)).has("error"))
	var old_id = backend.session_id
	var old_records = backend.read_session(old_id).records
	assert(not (await backend.prepare({"expected_version":"synthetic-1"})).has("error"))
	var new_id = backend.session_id
	assert(new_id != old_id and backend.read_session(old_id).front_locked)
	assert((await backend.recovery_export()).records.is_empty())
	backend.session_id = old_id
	assert((await backend.recovery_export()).error == "recovery_export_unavailable")
	backend.session_id = new_id
	var ids: Array = []
	for i in range(64):
		var r = await backend.record("exp.rt",{"trial_id":"bounded","choice":"left","rt_ms":321.5,"response_status":"responded"},{"id":"rt","version":"1"})
		assert(r.state == "buffered");ids.append(r.event_id)
	assert((await backend.record("exp.rt",{},{})).error == "not_recording_or_backpressure")
	assert(backend.buffer.size() == 64 and backend.read_session(new_id).records.is_empty())
	assert((await backend.commit({"version":1,"dependencies":ids,"next_trial":1})).state == "local_committed")
	assert(backend.read_session(new_id).records.size() == 64)
	assert(not (await backend.finish()).has("error"))
	await backend.flush(); await backend.flush()
	var tombstone = backend.read_session(new_id)
	assert(tombstone == {"id":new_id,"kind":"cleaned","state":"remote_acknowledged"})
	assert(backend.read_session(old_id).records == old_records)
	assert(backend.read_session(old_id).checkpoint != null)
	assert(not (await backend.prepare({"expected_version":"synthetic-1"})).has("error"))
	assert(backend.read_session(new_id) == tombstone,"New participation must not mutate a cleaned tombstone")
	assert(backend.read_session(old_id).front_locked)
	print("NATIVE_SHARED_DEVICE_AND_BACKPRESSURE_VERIFIED"); quit()
