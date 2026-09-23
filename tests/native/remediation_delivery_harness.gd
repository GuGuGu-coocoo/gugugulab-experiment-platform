extends SceneTree
## P03R09B harness: the real native backend with real SQLite transactions and
## real HTTP against the synthetic fault server
## (tests/remediation/delivery_fault_app.py).
##
## Phases (GEP_DELIVERY_PHASE):
##   main   - first-failure/retry counting, durable store reopen, completion-only
##            failures, invalid ACK, malformed ACK shapes over real HTTP, the real
##            SQLite ACK-write failure (trigger) with rollback and dedup, the
##            empty completion set, multi-session isolation, Retry-After, the
##            independent 32-attempt budget, permanent authorization and
##            study_deleted terminals, authorized recovery, lost ACK.
##   kill   - one round is started against a delayed response and the process is
##            killed while it is inflight (before any ACK).
##   resume - reopens the same store: the interrupted round is conservatively
##            outcome_unknown (never a failure), the counts are intact, an old
##            queue record and a partially shaped version-2 record only gain the
##            missing defaults without losing attempts/records/binding, and the
##            real delivery then completes.
##   kill_ack - the batch ACK is committed and the completion is delayed; the
##            process is killed while that completion request is outstanding.
##   resume_ack - reopens the store: pending and progress are consistent, the
##            round is outcome_unknown and the retried completion inserts nothing
##            twice on the server.
##
## Environment: GEP_SYNTHETIC_STORAGE, GEP_DELIVERY_FAULT_URL, GEP_DELIVERY_PHASE,
## GEP_DELIVERY_KILL_SESSION, GEP_DELIVERY_KILL_ACK_SESSION,
## GEP_DELIVERY_OLD_SESSION, GEP_DELIVERY_OLD_TOKEN,
## GEP_DELIVERY_OLD_INSTANCE/STUDY/RELEASE/BUILD, GEP_DELIVERY_PARTIAL_*.

const NativeBackend = preload("res://addons/gec/native_backend.gd")
const RT_PAYLOAD := {"trial_id": "t1", "choice": "left", "rt_ms": 321.5}
const RT_SCHEMA := {"id": "rt", "version": "1"}
const INTERACTION_PAYLOAD := {"action": "revise", "confidence": null}
const INTERACTION_SCHEMA := {"id": "interaction", "version": "1"}

var failures: Array = []
var fault_url := ""
var storage_root := ""
var probe


func check(condition: bool, label: String) -> void:
	if not condition:
		failures.append(label)


func _initialize() -> void:
	call_deferred("run")


func random_id() -> String:
	return Crypto.new().generate_random_bytes(16).hex_encode()


func make_config() -> Dictionary:
	return {"config_version": "1", "protocol_version": "gep/1", "purpose": "synthetic",
			"api_url": fault_url, "instance_id": random_id(), "study_id": random_id(),
			"release_id": "release-1", "build_id": random_id()}


func fault_post(path: String, body: Dictionary) -> Dictionary:
	var request = HTTPRequest.new()
	request.timeout = 10.0
	root.add_child(request)
	var error = request.request(fault_url + path, PackedStringArray(["Content-Type: application/json"]),
			HTTPClient.METHOD_POST, JSON.stringify(body))
	if error != OK:
		request.queue_free()
		return {}
	var result = await request.request_completed
	request.queue_free()
	if result[0] != HTTPRequest.RESULT_SUCCESS or int(result[1]) != 200:
		return {}
	var parsed = JSON.parse_string(result[3].get_string_from_utf8())
	return parsed if parsed is Dictionary else {}


func fault_state() -> Dictionary:
	var request = HTTPRequest.new()
	request.timeout = 10.0
	root.add_child(request)
	var error = request.request(fault_url + "/control/state")
	if error != OK:
		request.queue_free()
		return {}
	var result = await request.request_completed
	request.queue_free()
	if result[0] != HTTPRequest.RESULT_SUCCESS or int(result[1]) != 200:
		return {}
	var parsed = JSON.parse_string(result[3].get_string_from_utf8())
	return parsed if parsed is Dictionary else {}


func script(sessions: Dictionary) -> void:
	var result = await fault_post("/control/script", {"sessions": sessions})
	check(str(result.get("state", "")) == "scripted", "fault script accepted: " + JSON.stringify(result))


func new_backend(storage: String, cfg: Dictionary) -> Array:
	## Real backend on a real store; the periodic uploader is stopped so every
	## round below is an explicit, observable flush.
	OS.set_environment("GEP_SYNTHETIC_STORAGE", storage)
	var backend = NativeBackend.new()
	root.add_child(backend)
	backend.config = cfg
	var prepared = await backend.prepare()
	if backend.timer != null:
		backend.timer.stop()
	return [backend, prepared]


func reopened_backend(storage: String, cfg: Dictionary) -> Array:
	## A fresh backend instance over the same store without a new admission:
	## the real durable reopen (same SQLite file, new process object).
	OS.set_environment("GEP_SYNTHETIC_STORAGE", storage)
	var backend = NativeBackend.new()
	root.add_child(backend)
	backend.config = cfg
	var prepared = backend.initialize_queue()
	if backend.timer != null:
		backend.timer.stop()
	return [backend, prepared]


func delivery_view(s: Dictionary) -> Dictionary:
	return probe.delivery_of(s)


func expect_delivery(s: Dictionary, expected: Dictionary, label: String) -> void:
	var actual = delivery_view(s)
	for key in expected:
		check(actual.get(key) == expected[key], label + " delivery." + key + ": " + str(actual.get(key)) + " != " + str(expected[key]))


func force_flush(backend, id: String = "") -> void:
	## The manual retry/backoff-expiry boundary: the automatic flush honours the
	## retry schedule, and a forced retry does not clear any counter.
	var target: String = id if not id.is_empty() else String(backend.session_id)
	var s = backend.read_session(target)
	if not s.is_empty() and s.get("kind") == "session":
		s.retry_at = 0
		backend.save(s)
	await backend.flush()


func batches_for(session_id: String) -> int:
	return int((await fault_state()).get("sessions", {}).get(session_id, {}).get("batch_requests", -1))


func record_two(backend) -> Array:
	var first: Dictionary = await backend.record("exp.rt", RT_PAYLOAD, RT_SCHEMA, {"value": 321.5, "unit": "ms", "source": "harness"})
	var second: Dictionary = await backend.record("exp.interaction", INTERACTION_PAYLOAD, INTERACTION_SCHEMA)
	return [first, second]


func record_committed(backend) -> Array:
	## Record two events and persist a real trial checkpoint with both dependencies.
	var recorded := await record_two(backend)
	var committed: Dictionary = await backend.commit({"version": 1, "strategy": "trial_boundary_v1", "next_trial": 1, "dependencies": [recorded[0].event_id, recorded[1].event_id]})
	check(str(committed.get("state", "")) == "local_committed", "records committed with a checkpoint: " + JSON.stringify(committed))
	return recorded


func run_main() -> void:
	var main_storage := storage_root.path_join("main")
	DirAccess.make_dir_recursive_absolute(main_storage)
	var cfg := make_config()
	var pair := await new_backend(main_storage, cfg)
	var backend = pair[0]
	var prepared: Dictionary = pair[1]
	check(str(prepared.get("state", "")) == "active", "admission became active: " + JSON.stringify(prepared))
	var session_id: String = backend.session_id
	var recorded := await record_two(backend)
	var committed: Dictionary = await backend.commit({"version": 1, "strategy": "trial_boundary_v1", "next_trial": 1, "dependencies": [recorded[0].event_id, recorded[1].event_id]})
	check(str(committed.get("state", "")) == "local_committed", "records committed: " + JSON.stringify(committed))

	# ---- an experiment-time failure never carries into the finish threshold --
	await script({session_id: {"events": [{"kind": "fail", "status": 503, "code": "unavailable", "retryable": true}]}})
	await backend.flush()
	var s = backend.read_session(session_id)
	check(int(s.get("attempts", 0)) == 1, "experiment-time failure advanced the legacy attempts: " + str(s.get("attempts", 0)))
	expect_delivery(s, {"initial_failed": false, "retry_failures": 0, "ack_progress": 0, "round_id": 0, "inflight": false}, "pre-finish failure")
	check(s.pending.size() == 2, "the experiment-time failure kept both pending records: " + str(s.pending.size()))
	var finished: Dictionary = await backend.finish()
	check(str(finished.get("state", "")) == "local_committed", "finish declared the completion set: " + JSON.stringify(finished))

	# ---- the first normal send failure only sets initial_failed -------------
	await script({session_id: {"events": [{"kind": "fail", "status": 503, "code": "unavailable", "retryable": true},
										 {"kind": "fail", "status": 503, "code": "unavailable", "retryable": true},
										 {"kind": "fail", "status": 503, "code": "unavailable", "retryable": true},
										 {"kind": "fail", "status": 503, "code": "unavailable", "retryable": true}]}})
	await force_flush(backend)
	s = backend.read_session(session_id)
	expect_delivery(s, {"initial_failed": true, "retry_failures": 0, "ack_progress": 0, "round_id": 1, "inflight": false}, "first normal send failure")
	check(int(s.get("attempts", 0)) == 2, "the first normal send failure used one legacy attempt: " + str(s.get("attempts", 0)))
	check(str(s.get("delivery", {}).get("last_error", "")) == "http_503", "the transport error keeps its status: " + str(s.get("delivery", {}).get("last_error", "")))
	check(str(s.get("delivery", {}).get("last_error_kind", "")) == "unavailable", "the server error code is retained: " + str(s.get("delivery", {}).get("last_error_kind", "")))
	check(s.pending.size() == 2, "the failed round kept the pending records: " + str(s.pending.size()))

	# ---- retry rounds 1/2/3 count persistently ------------------------------
	for expected in [1, 2, 3]:
		await force_flush(backend)
		s = backend.read_session(session_id)
		expect_delivery(s, {"initial_failed": true, "retry_failures": expected, "ack_progress": 0, "round_id": expected + 1, "inflight": false}, "retry failure " + str(expected))
	check(int(s.get("attempts", 0)) == 5, "three retry failures used three legacy attempts: " + str(s.get("attempts", 0)))
	check(not s.get("paused", false), "three delivery failures do not pause (the 32 budget is separate)")
	var summary: Dictionary = backend.summary()
	check(int(summary.get("delivery", {}).get("retry_failures", 0)) == 3, "summary reports three delivery failures: " + JSON.stringify(summary.get("delivery", {})))
	check(int(summary.get("pending", 0)) == 2, "summary reports the real pending count: " + str(summary.get("pending", 0)))

	# ---- a durable reopen keeps every count and the raw queue ---------------
	var proof_before: String = str(s.get("proof", ""))
	backend.queue_free()
	await process_frame
	await process_frame
	var reopened := await reopened_backend(main_storage, cfg)
	var backend2 = reopened[0]
	check(str(reopened[1].get("state", "")) == "ready", "reopened store is ready: " + JSON.stringify(reopened[1]))
	var kept = backend2.read_session(session_id)
	expect_delivery(kept, {"initial_failed": true, "retry_failures": 3, "ack_progress": 0, "round_id": 4, "inflight": false, "terminal_reason": ""}, "reopen")
	check(int(kept.get("attempts", 0)) == 5 and kept.pending.size() == 2 and kept.records.size() == 2, "the reopen kept attempts/pending/records: " + JSON.stringify([kept.get("attempts", 0), kept.pending.size(), kept.records.size()]))
	check(str(kept.get("proof", "")) == proof_before and str(kept.config.get("study_id", "")) == str(cfg.study_id), "the reopen kept the proof and binding")

	# ---- completion-only failures use the same retry logic ------------------
	await script({session_id: {"events": [{"kind": "ack"}],
							   "completion": [{"kind": "fail", "status": 503, "code": "unavailable", "retryable": true},
											  {"kind": "fail", "status": 503, "code": "unavailable", "retryable": true},
											  {"kind": "fail", "status": 503, "code": "unavailable", "retryable": true}]}})
	await force_flush(backend2, session_id)
	kept = backend2.read_session(session_id)
	expect_delivery(kept, {"initial_failed": true, "retry_failures": 0, "ack_progress": 1, "round_id": 5, "inflight": false}, "batch progress with a failed completion")
	check(kept.pending.is_empty(), "the validated batch ACK reduced pending: " + str(kept.pending.size()))
	check(str(kept.get("delivery", {}).get("last_error", "")) == "http_503", "the failed completion kept its error: " + str(kept.get("delivery", {}).get("last_error", "")))
	for expected in [1, 2]:
		await force_flush(backend2, session_id)
		kept = backend2.read_session(session_id)
		expect_delivery(kept, {"retry_failures": expected, "ack_progress": 1, "inflight": false}, "completion-only failure " + str(expected))
	check(kept.get("complete_ack") == null, "a failed completion saved no complete_ack")

	# ---- an invalid completion ACK is no progress and counts the same -------
	await script({session_id: {"completion": [{"kind": "invalid"}]}})
	await force_flush(backend2, session_id)
	kept = backend2.read_session(session_id)
	expect_delivery(kept, {"retry_failures": 3, "ack_progress": 1, "inflight": false}, "invalid completion ACK")
	check(str(kept.get("delivery", {}).get("last_error", "")) == "invalid_completion_ack", "the invalid completion is named: " + str(kept.get("delivery", {}).get("last_error", "")))
	check(kept.get("complete_ack") == null, "an invalid ACK never became a complete_ack")

	# ---- a real completion ACK is the progress that cleans the session ------
	await script({session_id: {"completion": [{"kind": "complete"}]}})
	await force_flush(backend2, session_id)
	var tombstone = backend2.read_session(session_id)
	check(tombstone.get("kind") == "cleaned" and tombstone.get("state") == "remote_acknowledged", "the validated completion cleaned the session: " + JSON.stringify(tombstone))
	check(tombstone.get("delivery") == null and tombstone.get("proof") == null, "the tombstone keeps no delivery state or proof")
	check(not tombstone.has("records") and not tombstone.has("pending"), "the tombstone keeps no raw records")
	var completion_responses: Array = []
	for entry in (await fault_state()).get("requests", []):
		if entry.get("kind") == "completion" and entry.get("session_id") == session_id:
			completion_responses.append(entry.get("response"))
	check(completion_responses == ["fail", "fail", "fail", "invalid", "complete"], "the completion failures and the final validated ACK: " + JSON.stringify(completion_responses))

	backend2.queue_free()
	await process_frame
	await run_multi()
	await run_policy()
	await run_malformed_ack()
	await run_empty_completion()
	await run_ack_failure()


func run_multi() -> void:
	# ---- two sessions never share a delivery count --------------------------
	var storage := storage_root.path_join("multi")
	DirAccess.make_dir_recursive_absolute(storage)
	var cfg := make_config()
	var pair := await new_backend(storage, cfg)
	var backend = pair[0]
	var first: String = backend.session_id
	await record_two(backend)
	await backend.finish()
	# A second admission in the same store/backend; the first session is locked
	# in the foreground but its pending delivery is untouched.
	await backend.prepare()
	if backend.timer != null:
		backend.timer.stop()
	var second_id: String = backend.session_id
	check(second_id != first, "the second admission created another session")
	await record_two(backend)
	await backend.finish()
	check(backend.read_session(first).get("front_locked", false), "the new participation locked the earlier session in the foreground only")

	await script({first: {"events": [{"kind": "fail", "status": 503, "code": "unavailable", "retryable": true},
								   {"kind": "fail", "status": 503, "code": "unavailable", "retryable": true}],
						  "completion": [{"kind": "fail", "status": 503, "code": "unavailable", "retryable": true}]},
				  second_id: {"events": [{"kind": "fail", "status": 429, "code": "throttled", "retryable": true}]}})
	await force_flush(backend, first)
	var first_row = backend.read_session(first)
	var second_row = backend.read_session(second_id)
	expect_delivery(first_row, {"initial_failed": true, "retry_failures": 0, "ack_progress": 0, "round_id": 1}, "first session failure")
	expect_delivery(second_row, {"initial_failed": true, "retry_failures": 0, "ack_progress": 0, "round_id": 1, "last_error_kind": "throttled"}, "second session failure")
	await force_flush(backend, first)
	first_row = backend.read_session(first)
	second_row = backend.read_session(second_id)
	expect_delivery(first_row, {"retry_failures": 1, "ack_progress": 0, "round_id": 2}, "first session retry")
	expect_delivery(second_row, {"retry_failures": 0, "round_id": 1}, "the second session keeps its own count")
	# The current session summary must never show the other session's failure.
	var summary: Dictionary = backend.summary()
	check(str(summary.get("delivery", {}).get("last_error_kind", "")) == "throttled" and int(summary.get("delivery", {}).get("retry_failures", -1)) == 0, "the current session summary shows its own delivery state: " + JSON.stringify(summary))
	check(int(summary.get("pending", 0)) == 2, "the current session summary shows its own pending: " + str(summary.get("pending", 0)))
	backend.queue_free()
	await process_frame


func run_policy() -> void:
	var storage := storage_root.path_join("policy")
	DirAccess.make_dir_recursive_absolute(storage)
	var cfg := make_config()
	var pair := await new_backend(storage, cfg)
	var backend = pair[0]

	# ---- Retry-After is honoured by the automatic schedule ------------------
	var session_id: String = backend.session_id
	await record_two(backend)
	await backend.finish()
	await script({session_id: {"events": [{"kind": "fail", "status": 429, "code": "throttled", "retryable": true, "retry_after": 5},
										   {"kind": "fail", "status": 503, "code": "unavailable", "retryable": true},
										   {"kind": "fail", "status": 503, "code": "unavailable", "retryable": true}]}})
	var before = Time.get_unix_time_from_system()
	await backend.flush()
	var s = backend.read_session(session_id)
	check(float(s.get("retry_at", 0)) >= before + 5.0, "Retry-After set the retry schedule: " + str(float(s.get("retry_at", 0)) - before))
	expect_delivery(s, {"initial_failed": true, "retry_failures": 0, "round_id": 1, "inflight": false}, "Retry-After failure")
	check(str(s.get("delivery", {}).get("last_error_kind", "")) == "throttled", "the throttle code is retained: " + str(s.get("delivery", {}).get("last_error_kind", "")))
	var waiting_batches: int = await batches_for(session_id)
	await backend.flush()
	check(int(await batches_for(session_id)) == waiting_batches, "the wait before retry_at sent no request")
	s = backend.read_session(session_id)
	expect_delivery(s, {"retry_failures": 0, "round_id": 1}, "a wait is not a round")
	check(int(s.get("attempts", 0)) == 1, "a wait advanced no legacy attempt: " + str(s.get("attempts", 0)))
	await force_flush(backend)
	s = backend.read_session(session_id)
	expect_delivery(s, {"retry_failures": 1, "round_id": 2}, "the forced retry after the wait")
	check(str(s.get("delivery", {}).get("last_error_kind", "")) == "unavailable", "the forced retry kept its own server code: " + str(s.get("delivery", {}).get("last_error_kind", "")))

	# ---- the 32-attempt budget is independent of the three-failure counter --
	s = backend.read_session(session_id)
	s.attempts = 31
	s.retry_at = 0
	backend.save(s)
	await backend.flush()
	s = backend.read_session(session_id)
	check(int(s.get("attempts", 0)) == 32 and s.get("paused", false), "the 32nd legacy attempt pauses the session: " + JSON.stringify([s.get("attempts", 0), s.get("paused", false)]))
	expect_delivery(s, {"retry_failures": 2, "round_id": 3}, "the 32 budget and the delivery counter are independent")
	var paused_batches: int = await batches_for(session_id)
	await backend.flush()
	check(int(await batches_for(session_id)) == paused_batches, "a paused session sends no request")

	# ---- a permanent authorization rejection pauses without counting --------
	await script({})
	await backend.prepare()
	if backend.timer != null:
		backend.timer.stop()
	var permanent: String = backend.session_id
	await record_two(backend)
	await backend.finish()
	await script({permanent: {"events": [{"kind": "fail", "status": 403, "code": "session_unavailable", "retryable": false}]}})
	await force_flush(backend)
	var p = backend.read_session(permanent)
	check(p.get("paused", false), "a permanent authorization rejection pauses safely")
	expect_delivery(p, {"initial_failed": false, "retry_failures": 0, "round_id": 1, "inflight": false}, "permanent authorization rejection")
	check(str(p.get("delivery", {}).get("last_error_kind", "")) == "session_unavailable", "the authorization code is retained: " + str(p.get("delivery", {}).get("last_error_kind", "")))
	var permanent_batches: int = await batches_for(permanent)
	await backend.flush()
	check(int(await batches_for(permanent)) == permanent_batches, "a manual flush cannot unpause a safe pause")
	# Only the authorized recovery path unpauses, and it keeps the delivery count.
	var recovered: Dictionary = await backend.prepare({"recovery_session": permanent, "permit": "synthetic-permit"})
	check(str(recovered.get("state", "")) == "data_only", "the authorized recovery is data only: " + JSON.stringify(recovered))
	var after_recovery = backend.read_session(permanent)
	check(not after_recovery.get("paused", false) and int(after_recovery.get("attempts", 0)) == 0, "the authorized recovery unpaused the legacy budget: " + JSON.stringify([after_recovery.get("paused", false), after_recovery.get("attempts", 0)]))
	expect_delivery(after_recovery, {"retry_failures": 0, "initial_failed": false, "round_id": 1}, "recovery keeps the delivery counters")
	await script({permanent: {"events": [{"kind": "ack"}], "completion": [{"kind": "complete"}]}})
	await force_flush(backend)
	check(backend.read_session(permanent).get("kind") == "cleaned", "the recovered session completes after the authorized retry")

	# ---- a permanent study_deleted refusal terminates immediately ----------
	await script({})
	await backend.prepare()
	if backend.timer != null:
		backend.timer.stop()
	var deleted: String = backend.session_id
	await record_two(backend)
	await backend.finish()
	await script({deleted: {"events": [{"kind": "fail", "status": 403, "code": "study_deleted", "retryable": false}]}})
	await force_flush(backend)
	var d = backend.read_session(deleted)
	check(d.get("paused", false), "study_deleted pauses sending immediately")
	expect_delivery(d, {"retry_failures": 0, "initial_failed": false, "terminal_reason": "study_deleted"}, "study_deleted terminal")
	var deleted_batches: int = await batches_for(deleted)
	await backend.flush()
	check(int(await batches_for(deleted)) == deleted_batches, "study_deleted sends nothing more")
	var exported: Dictionary = await backend.recovery_export()
	check(str(exported.get("session_id", "")) == deleted and exported.get("records", []).size() == 2, "the legal unlocked local export still works: " + str(exported.get("session_id", "")))
	var exported_text := JSON.stringify(exported)
	check(not exported_text.contains("proof") and not exported_text.contains("token") and not exported_text.contains("password"), "the failure export carries no proof/token/password")

	# ---- a non-authorization retryable:false refusal pauses safely ----------
	await script({})
	await backend.prepare()
	if backend.timer != null:
		backend.timer.stop()
	var refused: String = backend.session_id
	await record_committed(backend)
	await backend.finish()
	await script({refused: {"events": [{"kind": "fail", "status": 400, "code": "synthetic_refusal", "retryable": false}]}})
	await force_flush(backend, refused)
	var r = backend.read_session(refused)
	check(r.get("paused", false), "a retryable:false 400 pauses safely")
	expect_delivery(r, {"initial_failed": false, "retry_failures": 0, "ack_progress": 0, "round_id": 1, "last_error_kind": "synthetic_refusal", "terminal_reason": ""}, "retryable:false 400 refusal")
	var refused_batches: int = await batches_for(refused)
	await backend.flush()
	check(int(await batches_for(refused)) == refused_batches, "a retryable:false 400 sends nothing more")
	# The authorized recovery keeps the delivery counters and lets it finish.
	var refused_recovered: Dictionary = await backend.prepare({"recovery_session": refused, "permit": "synthetic-permit"})
	check(str(refused_recovered.get("state", "")) == "data_only", "the authorized recovery of the 400 refusal is data only: " + JSON.stringify(refused_recovered))
	await script({refused: {"events": [{"kind": "ack"}], "completion": [{"kind": "complete"}]}})
	await force_flush(backend, refused)
	check(backend.read_session(refused).get("kind") == "cleaned", "the recovered 400 refusal completed after the authorized retry")

	# ---- study_deleted during the experiment terminates immediately ---------
	await script({})
	await backend.prepare()
	if backend.timer != null:
		backend.timer.stop()
	var active_deleted: String = backend.session_id
	await record_committed(backend)
	await script({active_deleted: {"events": [{"kind": "fail", "status": 403, "code": "study_deleted", "retryable": false}]}})
	await force_flush(backend, active_deleted)
	var ad = backend.read_session(active_deleted)
	check(ad.get("paused", false), "an experiment-time study_deleted pauses immediately")
	expect_delivery(ad, {"retry_failures": 0, "initial_failed": false, "ack_progress": 0, "round_id": 0, "terminal_reason": "study_deleted"}, "experiment-time study_deleted")
	check(ad.records.size() == 2, "the experiment-time study_deleted kept the raw records: " + str(ad.records.size()))
	check(ad.checkpoint != null, "the experiment-time study_deleted kept the checkpoint dependency")
	var ad_batches: int = await batches_for(active_deleted)
	await backend.flush()
	check(int(await batches_for(active_deleted)) == ad_batches, "the experiment-time study_deleted sends nothing more")

	# ---- a same-round batch ACK does not hide the deletion terminal ---------
	await script({})
	await backend.prepare()
	if backend.timer != null:
		backend.timer.stop()
	var progress_deleted: String = backend.session_id
	await record_committed(backend)
	await backend.finish()
	await script({progress_deleted: {"events": [{"kind": "ack"}], "completion": [{"kind": "fail", "status": 403, "code": "study_deleted", "retryable": false}]}})
	await force_flush(backend, progress_deleted)
	var pd = backend.read_session(progress_deleted)
	check(pd.get("paused", false) and str(pd.get("delivery", {}).get("terminal_reason", "")) == "study_deleted", "a same-round batch ACK still ends in the study_deleted terminal: " + JSON.stringify(pd.get("delivery", {})))
	expect_delivery(pd, {"retry_failures": 0, "ack_progress": 1, "round_id": 1}, "same-round ACK then study_deleted")
	check(pd.pending.is_empty(), "the legal batch ACK really reduced pending before the deletion: " + str(pd.pending.size()))
	var progress_export: Dictionary = await backend.recovery_export()
	check(progress_export.get("records", []).size() == 2, "the deletion after an ACK still allows the legal export")

	# ---- a lost ACK is retried and deduplicated on the server --------------
	await script({})
	await backend.prepare()
	if backend.timer != null:
		backend.timer.stop()
	var lost: String = backend.session_id
	await record_two(backend)
	await backend.finish()
	await script({lost: {"events": [{"kind": "drop"}], "completion": [{"kind": "complete"}]}})
	await force_flush(backend)
	var l = backend.read_session(lost)
	expect_delivery(l, {"initial_failed": true, "retry_failures": 0, "round_id": 1}, "lost ACK failure")
	check(l.pending.size() == 2, "the lost ACK kept the pending records: " + str(l.pending.size()))
	await script({lost: {"events": [{"kind": "ack"}], "completion": [{"kind": "complete"}]}})
	await force_flush(backend)
	var lost_session: Dictionary = (await fault_state()).get("sessions", {}).get(lost, {})
	check(int(lost_session.get("batch_requests", 0)) == 2, "the lost ACK round was really retried: " + str(lost_session.get("batch_requests", 0)))
	check(lost_session.get("events", []).size() == 2, "the retried batch left exactly two server events: " + str(lost_session.get("events", []).size()))
	check(backend.read_session(lost).get("kind") == "cleaned", "the retried session completed")
	backend.queue_free()
	await process_frame


func run_malformed_ack() -> void:
	## Every malformed ACK shape is delivered over real HTTP to the real parser:
	## a missing/null/wrongly typed list is rejected, no receipt is saved, no
	## cleanup happens, and the raw records, the checkpoint dependency and the
	## credential survive. A valid ACK afterwards still works.
	var storage := storage_root.path_join("malformed")
	DirAccess.make_dir_recursive_absolute(storage)
	var cfg := make_config()
	var pair := await new_backend(storage, cfg)
	var backend = pair[0]
	var session_id: String = backend.session_id
	await record_committed(backend)
	await backend.finish()
	var before = backend.read_session(session_id)
	var token_before: String = str(before.context.token)
	var proof_before: String = str(before.proof)
	await script({session_id: {"events": [
		{"kind": "accepted_null"}, {"kind": "duplicate_null"}, {"kind": "accepted_absent"}, {"kind": "accepted_string"}]}})
	for shape in ["accepted_null", "duplicate_null", "accepted_absent", "accepted_string"]:
		await force_flush(backend, session_id)
		var s = backend.read_session(session_id)
		check(s.get("complete_ack") == null and s.get("kind") == "session", "malformed event ACK (" + shape + ") saved no receipt and no cleanup: " + JSON.stringify([s.get("complete_ack"), s.get("kind")]))
		check(s.pending.size() == 2 and s.records.size() == 2, "malformed event ACK (" + shape + ") kept the raw queue: " + JSON.stringify([s.pending.size(), s.records.size()]))
		check(str(s.get("delivery", {}).get("last_error", "")) == "invalid_ack", "malformed event ACK (" + shape + ") is named: " + str(s.get("delivery", {}).get("last_error", "")))
		check(s.checkpoint != null and str(s.context.token) == token_before and str(s.proof) == proof_before, "malformed event ACK (" + shape + ") kept the checkpoint dependency and credential")
	await script({session_id: {"events": [{"kind": "ack"}], "completion": [{"kind": "fail", "status": 503, "code": "unavailable", "retryable": true}]}})
	await force_flush(backend, session_id)
	var delivered = backend.read_session(session_id)
	check(delivered.pending.is_empty() and int(delivered.get("delivery", {}).get("ack_progress", 0)) == 1, "the valid batch ACK after the malformed shapes still made progress: " + JSON.stringify([delivered.pending.size(), delivered.get("delivery", {}).get("ack_progress", 0)]))
	for shape in ["missing_absent", "missing_null", "missing_string", "missing_object", "missing_nonempty",
				  "declaration_absent", "declaration_null", "declaration_events_string", "declaration_segments_null"]:
		await script({session_id: {"completion": [{"kind": shape}]}})
		await force_flush(backend, session_id)
		var s = backend.read_session(session_id)
		check(s.get("complete_ack") == null and s.get("kind") == "session", "malformed completion ACK (" + shape + ") saved no receipt and no cleanup: " + JSON.stringify([s.get("complete_ack"), s.get("kind")]))
		check(s.records.size() == 2 and s.checkpoint != null, "malformed completion ACK (" + shape + ") kept the records and the checkpoint dependency")
		check(str(s.get("delivery", {}).get("last_error", "")) == "invalid_completion_ack", "malformed completion ACK (" + shape + ") is named: " + str(s.get("delivery", {}).get("last_error", "")))
		check(str(s.context.token) == token_before and str(s.proof) == proof_before, "malformed completion ACK (" + shape + ") kept the credential and proof")
	await script({session_id: {"completion": [{"kind": "complete"}]}})
	await force_flush(backend, session_id)
	check(backend.read_session(session_id).get("kind") == "cleaned", "the valid completion ACK after the malformed shapes still cleaned the session")
	backend.queue_free()
	await process_frame


func run_empty_completion() -> void:
	## An empty declared completion set must still carry complete, correctly typed
	## arrays: a malformed shape is rejected and the structurally valid one is
	## accepted.
	var storage := storage_root.path_join("empty_completion")
	DirAccess.make_dir_recursive_absolute(storage)
	var cfg := make_config()
	var pair := await new_backend(storage, cfg)
	var backend = pair[0]
	var session_id: String = backend.session_id
	var s = backend.read_session(session_id)
	s.completion = {"event_ids": [], "segment_ids": []}
	check(backend.save(s), "the empty completion set was persisted")
	await script({session_id: {"completion": [{"kind": "missing_absent"}]}})
	await force_flush(backend, session_id)
	s = backend.read_session(session_id)
	check(s.get("complete_ack") == null and s.get("kind") == "session", "an empty completion set with a missing field is rejected: " + JSON.stringify([s.get("complete_ack"), s.get("kind")]))
	check(str(s.get("delivery", {}).get("last_error", "")) == "invalid_completion_ack", "the malformed empty completion is named: " + str(s.get("delivery", {}).get("last_error", "")))
	await script({session_id: {"completion": [{"kind": "complete"}]}})
	await force_flush(backend, session_id)
	check(backend.read_session(session_id).get("kind") == "cleaned", "a structurally valid empty completion set is accepted")
	backend.queue_free()
	await process_frame


func run_ack_failure() -> void:
	## A real SQLite trigger aborts exactly the ACK state write: the transaction
	## rolls back, pending/records/counts survive, the failure is visible as a
	## local storage error (not a remote authorization denial, not a permanent
	## pause), and removing the injection lets a reopened store retry and dedupe.
	var storage := storage_root.path_join("ackfail")
	DirAccess.make_dir_recursive_absolute(storage)
	var cfg := make_config()
	var pair := await new_backend(storage, cfg)
	var backend = pair[0]
	var session_id: String = backend.session_id
	await record_committed(backend)
	await backend.finish()
	var before = backend.read_session(session_id)
	var token_before: String = str(before.context.token)
	var proof_before: String = str(before.proof)
	await script({session_id: {"events": [{"kind": "ack"}], "completion": [{"kind": "complete"}]}})
	var queue_path := storage.path_join("queue.sqlite")
	check(install_ack_block_trigger(queue_path), "the SQLite ACK-write trigger was installed")
	await force_flush(backend, session_id)
	var failed = backend.read_session(session_id)
	check(failed.pending.size() == 2 and failed.records.size() == 2, "the blocked ACK write rolled the pending change back: " + JSON.stringify([failed.pending.size(), failed.records.size()]))
	check(str(failed.get("delivery", {}).get("last_error_kind", "")) == "local_storage_error", "the blocked ACK write is visible as a local storage error: " + str(failed.get("delivery", {}).get("last_error_kind", "")))
	check(int(failed.get("delivery", {}).get("ack_progress", -1)) == 0 and int(failed.get("delivery", {}).get("retry_failures", -1)) == 0, "the blocked ACK write produced no false progress or failure count: " + JSON.stringify(failed.get("delivery", {})))
	check(failed.get("complete_ack") == null and failed.get("kind") == "session", "the blocked ACK write saved no receipt and no cleanup")
	check(not failed.get("paused", false), "a local ACK write failure does not permanently pause the session")
	check(failed.checkpoint != null and str(failed.context.token) == token_before and str(failed.proof) == proof_before, "the blocked ACK write kept the checkpoint, credential and proof")
	check(drop_ack_block_trigger(queue_path), "the SQLite ACK-write trigger was removed")
	backend.queue_free()
	await process_frame
	await process_frame
	var reopened := await reopened_backend(storage, cfg)
	var backend2 = reopened[0]
	check(str(reopened[1].get("state", "")) == "ready", "the ackfail store reopened: " + JSON.stringify(reopened[1]))
	var kept = backend2.read_session(session_id)
	check(str(kept.get("delivery", {}).get("last_error_kind", "")) == "local_storage_error" and kept.pending.size() == 2, "the reopen kept the visible local error and the raw queue: " + JSON.stringify([kept.get("delivery", {}).get("last_error_kind", ""), kept.pending.size()]))
	await script({session_id: {"events": [{"kind": "ack"}], "completion": [{"kind": "fail", "status": 503, "code": "unavailable", "retryable": true}]}})
	await force_flush(backend2, session_id)
	var delivered = backend2.read_session(session_id)
	check(delivered.pending.is_empty() and int(delivered.get("delivery", {}).get("ack_progress", 0)) == 1, "the retried batch cleared pending and counted progress after the injection was removed: " + JSON.stringify([delivered.pending.size(), delivered.get("delivery", {}).get("ack_progress", 0)]))
	await script({session_id: {"completion": [{"kind": "complete"}]}})
	await force_flush(backend2, session_id)
	check(backend2.read_session(session_id).get("kind") == "cleaned", "the session completed after the real retry")
	var state: Dictionary = (await fault_state()).get("sessions", {}).get(session_id, {})
	check(int(state.get("batch_requests", 0)) == 2, "the blocked round was really retried exactly once: " + str(state.get("batch_requests", 0)))
	check(state.get("events", []).size() == 2, "the retry deduplicated to exactly two server events: " + str(state.get("events", []).size()))
	print("P03R09B_ACKFAIL ", session_id)
	backend2.queue_free()
	await process_frame


func _exec_raw_sql(path: String, sql: String) -> bool:
	var raw = ClassDB.instantiate("SQLite")
	raw.path = path
	if not raw.open_db():
		return false
	var ok = raw.query(sql)
	raw.close_db()
	return ok


func install_ack_block_trigger(path: String) -> bool:
	## The injection is a real database trigger, not a mocked save: it aborts the
	## UPDATE that would persist the batch-ACK state (pending reduction and the
	## delivery progress in one transaction).
	return _exec_raw_sql(path, "CREATE TRIGGER IF NOT EXISTS block_ack_progress BEFORE UPDATE ON sessions WHEN NEW.value LIKE '%\"ack_progress\":1%' AND OLD.value NOT LIKE '%\"ack_progress\":1%' BEGIN SELECT RAISE(ABORT,'synthetic_ack_block'); END")


func drop_ack_block_trigger(path: String) -> bool:
	return _exec_raw_sql(path, "DROP TRIGGER IF EXISTS block_ack_progress")


func run_kill() -> void:
	## One round is persisted as inflight, then the process is killed while the
	## delayed response is still outstanding; the next process must not count it.
	var storage := storage_root.path_join("kill")
	DirAccess.make_dir_recursive_absolute(storage)
	var cfg := make_config()
	var pair := await new_backend(storage, cfg)
	var backend = pair[0]
	check(str(pair[1].get("state", "")) == "active", "kill-phase admission became active: " + JSON.stringify(pair[1]))
	await record_two(backend)
	await backend.finish()
	await script({backend.session_id: {"events": [{"kind": "delay", "delay_ms": 30000}]}})
	print("P03R09B_KILL_READY ", backend.session_id)
	await backend.flush()
	print("P03R09B_KILL_NOT_KILLED")


func run_kill_ack() -> void:
	## The batch ACK is locally committed and the completion request is still
	## outstanding when the process is killed, so the next process must find the
	## pending reduction and the delivery progress already durable and consistent.
	var storage := storage_root.path_join("kill_ack")
	DirAccess.make_dir_recursive_absolute(storage)
	var cfg := make_config()
	var pair := await new_backend(storage, cfg)
	var backend = pair[0]
	check(str(pair[1].get("state", "")) == "active", "kill-ack admission became active: " + JSON.stringify(pair[1]))
	await record_two(backend)
	await backend.finish()
	await script({backend.session_id: {"events": [{"kind": "ack"}], "completion": [{"kind": "delay", "delay_ms": 30000}]}})
	# The driver polls this file for the real session id, waits until the fault
	# server has received the completion request (the ACK transaction is then
	# committed) and kills the process mid-request.
	var marker := FileAccess.open(storage.path_join("kill_ack_session.txt"), FileAccess.WRITE)
	if marker != null:
		marker.store_string(backend.session_id)
		marker.close()
	print("P03R09B_KILL_ACK_READY ", backend.session_id)
	await backend.flush()
	print("P03R09B_KILL_ACK_NOT_KILLED")


func run_resume_ack() -> void:
	## Reopen the store killed after the ACK commit: the pending reduction and
	## the progress are durable, the interrupted round is outcome_unknown and the
	## real retry continues without a duplicate server insertion.
	var storage := storage_root.path_join("kill_ack")
	var cfg := make_config()
	var killed := OS.get_environment("GEP_DELIVERY_KILL_ACK_SESSION")
	check(not killed.is_empty(), "kill-ack session id is provided")
	var pair := await reopened_backend(storage, cfg)
	var backend = pair[0]
	check(str(pair[1].get("state", "")) == "ready", "kill-ack store is ready: " + JSON.stringify(pair[1]))
	var s = backend.read_session(killed)
	check(not s.is_empty(), "the kill-ack session survived the process exit")
	check(s.pending.is_empty(), "the committed ACK left no pending record: " + str(s.pending.size()))
	expect_delivery(s, {"initial_failed": false, "retry_failures": 0, "ack_progress": 1, "round_id": 1, "inflight": false,
						"last_error_kind": "outcome_unknown", "terminal_reason": ""}, "ACK committed before the kill")
	check(s.records.size() == 2, "the committed ACK kept both raw records: " + str(s.records.size()))
	await script({killed: {"events": [{"kind": "ack"}], "completion": [{"kind": "complete"}]}})
	await force_flush(backend, killed)
	check(backend.read_session(killed).get("kind") == "cleaned", "the interrupted completion completed after the real retry")
	var killed_session: Dictionary = (await fault_state()).get("sessions", {}).get(killed, {})
	check(int(killed_session.get("batch_requests", 0)) == 1, "the committed batch was never resent: " + str(killed_session.get("batch_requests", 0)))
	check(killed_session.get("events", []).size() == 2, "the retry added no duplicate server event: " + str(killed_session.get("events", []).size()))
	var completions: Array = []
	for entry in (await fault_state()).get("requests", []):
		if entry.get("kind") == "completion" and entry.get("session_id") == killed:
			completions.append(entry.get("response"))
	check(completions == ["delay", "complete"], "the completion was interrupted then really retried: " + JSON.stringify(completions))


func run_resume() -> void:
	## Reopen the killed store and the fixture old-queue record; the interrupted
	## round is outcome_unknown and no count was lost or invented.
	var storage := storage_root.path_join("kill")
	var cfg := make_config()
	var killed := OS.get_environment("GEP_DELIVERY_KILL_SESSION")
	var old_session := OS.get_environment("GEP_DELIVERY_OLD_SESSION")
	var old_token := OS.get_environment("GEP_DELIVERY_OLD_TOKEN")
	var old_cfg := {"config_version": "1", "protocol_version": "gep/1", "purpose": "synthetic", "api_url": fault_url,
					"instance_id": OS.get_environment("GEP_DELIVERY_OLD_INSTANCE"), "study_id": OS.get_environment("GEP_DELIVERY_OLD_STUDY"),
					"release_id": OS.get_environment("GEP_DELIVERY_OLD_RELEASE"), "build_id": OS.get_environment("GEP_DELIVERY_OLD_BUILD")}
	var partial_session := OS.get_environment("GEP_DELIVERY_PARTIAL_SESSION")
	var partial_token := OS.get_environment("GEP_DELIVERY_PARTIAL_TOKEN")
	var partial_cfg := {"config_version": "1", "protocol_version": "gep/1", "purpose": "synthetic", "api_url": fault_url,
					"instance_id": OS.get_environment("GEP_DELIVERY_PARTIAL_INSTANCE"), "study_id": OS.get_environment("GEP_DELIVERY_PARTIAL_STUDY"),
					"release_id": OS.get_environment("GEP_DELIVERY_PARTIAL_RELEASE"), "build_id": OS.get_environment("GEP_DELIVERY_PARTIAL_BUILD")}
	check(not killed.is_empty(), "kill session id is provided")
	check(not old_session.is_empty() and not old_token.is_empty(), "old-queue fixture is provided")
	check(not partial_session.is_empty() and not partial_token.is_empty(), "partial version-2 fixture is provided")
	check(insert_old_session(storage.path_join("queue.sqlite"), old_session, old_token, old_cfg), "the old-queue fixture row was inserted")
	check(insert_partial_session(storage.path_join("queue.sqlite"), partial_session, partial_token, partial_cfg), "the partial version-2 fixture row was inserted")

	var pair := await reopened_backend(storage, cfg)
	var backend = pair[0]
	check(str(pair[1].get("state", "")) == "ready", "resume-phase store is ready: " + JSON.stringify(pair[1]))
	var s = backend.read_session(killed)
	check(not s.is_empty(), "the killed session survived the process exit")
	expect_delivery(s, {"initial_failed": false, "retry_failures": 0, "ack_progress": 0, "round_id": 1, "inflight": false,
						"last_error_kind": "outcome_unknown", "terminal_reason": ""}, "interrupted round on reopen")
	check(int(s.get("attempts", 0)) == 0, "the process exit itself counted no failure: " + str(s.get("attempts", 0)))
	check(s.pending.size() == 2 and s.records.size() == 2, "the interrupted round kept the raw queue: " + JSON.stringify([s.pending.size(), s.records.size()]))

	# ---- an old queue record only gains the missing defaults ---------------
	var old = backend.read_session(old_session)
	check(not old.is_empty(), "the old-queue record is readable")
	check(int(old.get("delivery_version", 0)) == 2 and old.get("delivery") is Dictionary, "the old record gained the delivery version and defaults")
	expect_delivery(old, {"initial_failed": false, "retry_failures": 0, "round_id": 0, "inflight": false}, "old queue defaults")
	check(int(old.get("attempts", 0)) == 7 and float(old.get("retry_at", 0)) == 0.0 and not old.get("paused", false), "the old attempts/backoff were not zeroed: " + JSON.stringify([old.get("attempts", 0), old.get("retry_at", 0), old.get("paused", false)]))
	check(old.records.size() == 1 and old.pending.size() == 1 and str(old.proof) == "old-proof", "the old records/pending/proof were kept")
	check(str(old.config.get("study_id", "")) == str(old_cfg.study_id) and str(old.config.get("release_id", "")) == "release-1", "the old binding was kept")

	# ---- a partially shaped version-2 delivery persists the missing defaults -
	var partial = backend.read_session(partial_session)
	check(not partial.is_empty(), "the partial version-2 record is readable")
	check(int(partial.get("delivery_version", 0)) == 2 and partial.get("delivery") is Dictionary, "the partial record kept its version")
	var raw_delivery: Dictionary = partial.get("delivery", {})
	var missing_keys: Array = []
	for key in probe.default_delivery():
		if not raw_delivery.has(key):
			missing_keys.append(key)
	check(missing_keys.is_empty(), "the partial version-2 delivery persisted every missing default: " + JSON.stringify(missing_keys))
	expect_delivery(partial, {"initial_failed": false, "retry_failures": 2, "round_id": 0, "ack_progress": 0, "terminal_reason": "", "inflight": false}, "partial version-2 defaults")
	check(int(partial.get("attempts", 0)) == 7 and float(partial.get("retry_at", 0)) == 0.0 and not partial.get("paused", false), "the partial record kept attempts/retry_at/paused: " + JSON.stringify([partial.get("attempts", 0), partial.get("retry_at", 0), partial.get("paused", false)]))
	check(partial.records.size() == 1 and partial.pending.size() == 1 and str(partial.proof) == "partial-proof", "the partial record kept records/pending/proof")
	check(str(partial.config.get("study_id", "")) == str(partial_cfg.study_id), "the partial record kept its binding")

	# ---- both sessions really deliver --------------------------------------
	await script({killed: {"events": [{"kind": "ack"}], "completion": [{"kind": "fail", "status": 503, "code": "unavailable", "retryable": true}]},
				  old_session: {"events": [{"kind": "ack"}]}})
	await force_flush(backend, killed)
	s = backend.read_session(killed)
	expect_delivery(s, {"initial_failed": false, "retry_failures": 0, "ack_progress": 1, "round_id": 2, "inflight": false}, "the real retry after the interruption")
	check(s.pending.is_empty(), "the retried batch cleared pending after the real ACK")
	old = backend.read_session(old_session)
	check(old.pending.is_empty() and old.records.size() == 1, "the old record delivered without losing its record")
	check(int(old.get("attempts", 0)) == 7, "the successful round left the old attempts untouched: " + str(old.get("attempts", 0)))
	await script({killed: {"events": [{"kind": "ack"}], "completion": [{"kind": "complete"}]}})
	await force_flush(backend, killed)
	check(backend.read_session(killed).get("kind") == "cleaned", "the interrupted session completed after the real retry")
	var killed_session: Dictionary = (await fault_state()).get("sessions", {}).get(killed, {})
	check(int(killed_session.get("batch_requests", 0)) == 2, "the killed round was retried exactly once: " + str(killed_session.get("batch_requests", 0)))
	check(killed_session.get("events", []).size() == 2, "the retried batch deduplicated the killed-round events")


func insert_old_session(path: String, session_id: String, token: String, cfg: Dictionary) -> bool:
	## The fixture is an old-shaped durable row: no delivery fields at all, with
	## its own records, pending id, proof, binding and legacy attempts history.
	var raw = ClassDB.instantiate("SQLite")
	raw.path = path
	if not raw.open_db():
		return false
	var event = {"protocol_version": "gep/1", "event_id": random_id(), "session_id": session_id, "segment_id": random_id(),
				 "sequence": 1, "event_type": "exp.rt", "schema_id": "rt", "schema_version": "1",
				 "payload": {"trial_id": "old", "choice": "left", "rt_ms": 111.0}}
	var row = {"id": session_id, "kind": "session", "config": cfg, "context": {"token": token}, "proof": "old-proof",
			   "records": [event], "pending": [event.event_id], "segments": [event.segment_id], "checkpoint": null,
			   "completion": null, "complete_ack": null, "front_locked": false, "attempts": 7, "retry_at": 0, "paused": false}
	var ok = raw.query_with_bindings("INSERT INTO sessions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",
			[session_id, JSON.stringify(row)])
	raw.close_db()
	return ok


func insert_partial_session(path: String, session_id: String, token: String, cfg: Dictionary) -> bool:
	## A version-2 record that is missing most delivery keys (older build or
	## partial write): reconcile must persist the defaults and keep the existing
	## retry_failures/attempts/records/proof/binding.
	var raw = ClassDB.instantiate("SQLite")
	raw.path = path
	if not raw.open_db():
		return false
	var event = {"protocol_version": "gep/1", "event_id": random_id(), "session_id": session_id, "segment_id": random_id(),
				 "sequence": 1, "event_type": "exp.rt", "schema_id": "rt", "schema_version": "1",
				 "payload": {"trial_id": "partial", "choice": "left", "rt_ms": 222.0}}
	var row = {"id": session_id, "kind": "session", "config": cfg, "context": {"token": token}, "proof": "partial-proof",
			   "records": [event], "pending": [event.event_id], "segments": [event.segment_id], "checkpoint": null,
			   "completion": null, "complete_ack": null, "front_locked": false, "attempts": 7, "retry_at": 0, "paused": false,
			   "delivery_version": 2, "delivery": {"retry_failures": 2}}
	var ok = raw.query_with_bindings("INSERT INTO sessions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",
			[session_id, JSON.stringify(row)])
	raw.close_db()
	return ok


func run() -> void:
	await process_frame
	probe = NativeBackend.new()
	fault_url = OS.get_environment("GEP_DELIVERY_FAULT_URL")
	storage_root = OS.get_environment("GEP_SYNTHETIC_STORAGE")
	check(not fault_url.is_empty(), "fault url environment is set")
	check(not storage_root.is_empty(), "storage environment is set")
	DirAccess.make_dir_recursive_absolute(storage_root)
	var phase := OS.get_environment("GEP_DELIVERY_PHASE")
	if phase == "kill":
		await run_kill()
	elif phase == "resume":
		await run_resume()
	elif phase == "kill_ack":
		await run_kill_ack()
	elif phase == "resume_ack":
		await run_resume_ack()
	else:
		await run_main()
	if failures.is_empty():
		print("P03R09B_HARNESS_VERIFIED")
		quit(0)
	else:
		print("P03R09B_HARNESS_FAILED ", JSON.stringify(failures))
		quit(1)
