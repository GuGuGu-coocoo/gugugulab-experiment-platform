extends SceneTree
## P03R10 harness: the experiment-side thin defaults wrapper over real backends.
##
## Phases (GEP_DEFAULTS_PHASE):
##   unit - the wrapper contract on one real local backend (real SQLite) plus a
##          spy backend for the copy-before-await timing: default/explicit
##          precedence, payload replacement, declared-source evaluation, the
##          observed rules, plain-finite-JSON validation, invalid schema and
##          backpressure errors that leave the raw buffer untouched, the
##          record/commit/finish split and the old explicit four-parameter call.
##   gep  - the defaults path over the real native backend against a real
##          isolated GEP server (GEP_DEFAULTS_API_URL/INSTANCE/STUDY/RELEASE/
##          BUILD): admission, two exp.rt trials from field callables with the
##          explicit host clock, one exp.interaction from a snapshot provider,
##          commit/finish and the real completion acknowledgement.
##   cycles - P03R10R bounded-value guard on a real local backend (real SQLite):
##          direct/indirect/mixed container cycles, the MAX_VALUE_DEPTH
##          boundary, shared-but-acyclic values, guarded schema configuration,
##          bounded details without payload echo, and record/commit/finish
##          recovery after a refusal and after a real storage failure.
##   gep_negative - P03R10R: a well-shaped but unregistered schema id/version
##          against a real isolated GEP server; the wrapper checks only the
##          shape, the server refuses the batch, no raw event is stored and the
##          original local record identity and pending data stay.
##
## Environment: GEP_DEFAULTS_PHASE, GEP_SYNTHETIC_STORAGE and, for gep and
## gep_negative, the GEP_DEFAULTS_* binding. Prints P03R10_VERIFIED (unit/gep)
## or P03R10R_VERIFIED (cycles/gep_negative) with {facts} and exits 0 only when
## every check passed; otherwise the _FAILED marker and exit 1.

const ExperimentData = preload("res://data/experiment_data.gd")
const LocalBackend = preload("res://data/local_backend.gd")
const NativeBackend = preload("res://addons/gec/native_backend.gd")

var failures: Array = []
var facts: Dictionary = {}
var phase := ""
var epoch := ""


class SpyBackend extends Node:
	## Stores the exact payload/observed references it receives (no copy), so a
	## later mutation of the source only shows up if the wrapper failed to copy
	## before the await.
	var calls: Array = []
	var response: Dictionary = {"state": "buffered", "event_id": "spy-event"}
	func record(kind: String, payload, schema: Dictionary, observed: Dictionary = {}) -> Dictionary:
		await get_tree().process_frame
		calls.append({"kind": kind, "payload": payload, "schema": schema.duplicate(true),
				"observed": observed.duplicate(true)})
		return response.duplicate(true)


func _initialize() -> void:
	call_deferred("run")


func check(condition: bool, label: String, detail = null) -> void:
	if not condition:
		failures.append(label + ("" if detail == null else " :: " + JSON.stringify(detail)))


func code(result: Dictionary) -> String:
	return str(result.get("error", ""))


func schema(id: String, version: String) -> Dictionary:
	return {"id": id, "version": version}


func rt_clock(value: float) -> Dictionary:
	return {"value": value, "unit": "ms", "clock_id": "host_monotonic", "epoch": epoch,
			"source": "Godot Time.get_ticks_usec"}


func run() -> void:
	phase = OS.get_environment("GEP_DEFAULTS_PHASE")
	if phase == "unit":
		await run_unit()
	elif phase == "gep":
		await run_gep()
	elif phase == "cycles":
		await run_cycles()
	elif phase == "gep_negative":
		await run_gep_negative()
	else:
		failures.append("unknown phase: " + phase)
	var marker := "P03R10R_VERIFIED" if phase in ["cycles", "gep_negative"] else "P03R10_VERIFIED"
	if failures.is_empty():
		print(marker + " ", JSON.stringify(facts))
		quit(0)
	else:
		print(marker + "_FAILED ", JSON.stringify(failures))
		quit(1)


func run_unit() -> void:
	epoch = "p03r10"
	var backend := LocalBackend.new()
	root.add_child(backend)
	var prepared: Dictionary = await backend.prepare()
	check(prepared.get("state") == "active", "the real local backend prepares", prepared)
	var data := ExperimentData.new(backend)

	# ---- configuration validation -----------------------------------------
	var configure_errors := {}
	configure_errors.missing = code(data.configure_defaults({}))
	configure_errors.event_type_empty = code(data.configure_defaults({"event_type": "", "schema": schema("x", "1")}))
	configure_errors.schema_id_empty = code(data.configure_defaults({"event_type": "exp.x", "schema": {"id": "", "version": "1"}}))
	configure_errors.schema_version_missing = code(data.configure_defaults({"event_type": "exp.x", "schema": {"id": "x"}}))
	configure_errors.schema_not_dictionary = code(data.configure_defaults({"event_type": "exp.x", "schema": "nope"}))
	configure_errors.exclusive = code(data.configure_defaults({"event_type": "exp.x", "schema": schema("x", "1"),
			"fields": {}, "snapshot_provider": func(): return {}}))
	configure_errors.fields_type = code(data.configure_defaults({"event_type": "exp.x", "schema": schema("x", "1"), "fields": "nope"}))
	configure_errors.fields_callable = code(data.configure_defaults({"event_type": "exp.x", "schema": schema("x", "1"),
			"fields": {"a": Callable()}}))
	configure_errors.provider_type = code(data.configure_defaults({"event_type": "exp.x", "schema": schema("x", "1"),
			"snapshot_provider": "nope"}))
	configure_errors.unknown_key = code(data.configure_defaults({"event_type": "exp.x", "schema": schema("x", "1"), "extra": 1}))
	facts.configure_errors = configure_errors
	check(configure_errors == {"missing": "invalid_config", "event_type_empty": "invalid_config",
			"schema_id_empty": "invalid_schema", "schema_version_missing": "invalid_schema",
			"schema_not_dictionary": "invalid_schema", "exclusive": "invalid_config",
			"fields_type": "invalid_config", "fields_callable": "invalid_field",
			"provider_type": "invalid_provider", "unknown_key": "invalid_config"},
			"every rejected configuration reports its explicit error code", configure_errors)
	check(backend.buffer.is_empty(), "no rejected configuration wrote a buffer entry", backend.buffer.size())

	# ---- declared fields, short record, re-evaluation, explicit precedence --
	var calls: Array = []
	var trial := {"id": "t1", "choice": "left", "rt_ms": 321.5}
	var configured := data.configure_defaults({
		"event_type": "exp.rt",
		"schema": schema("rt", "1"),
		"fields": {
			"trial_id": func(): calls.append("trial_id"); return trial.id,
			"choice": func(): calls.append("choice"); return trial.choice,
			"rt_ms": func(): calls.append("rt_ms"); return trial.rt_ms,
			"response_status": func(): calls.append("response_status"); return "responded",
		},
	})
	check(configured.get("state") == "defaults_configured", "defaults configure", configured)
	# A rejected configuration must not replace the accepted one.
	var rejected := data.configure_defaults({"event_type": "", "schema": schema("x", "1")})
	check(code(rejected) == "invalid_config", "rejected configure", rejected)
	check(backend.buffer.is_empty(), "rejected reconfigure wrote nothing", backend.buffer.size())

	var first: Dictionary = await data.record()
	check(first.get("state") == "buffered" and not first.has("error"), "short record from fields", first)
	check(calls == ["trial_id", "choice", "rt_ms", "response_status"],
			"each declared field is evaluated once, in declaration order", calls)
	check(backend.buffer.size() == 1, "one buffered event", backend.buffer.size())
	check(backend.records.is_empty(), "record did not commit", backend.records.size())
	var first_event: Dictionary = backend.buffer[0]
	check(first_event.event_type == "exp.rt" and first_event.schema_id == "rt" and first_event.schema_version == "1",
			"default event type and schema", first_event)
	check(first_event.payload == {"trial_id": "t1", "choice": "left", "rt_ms": 321.5, "response_status": "responded"},
			"the field payload has exactly the declared keys", first_event.payload)
	check(not first_event.has("observed_time"), "no observed time without an explicit observed value", first_event)

	calls.clear()
	trial.id = "t2"
	trial.choice = "right"
	trial.rt_ms = 217.25
	var second: Dictionary = await data.record()
	check(not second.has("error"), "second short record", second)
	check(backend.buffer[1].payload == {"trial_id": "t2", "choice": "right", "rt_ms": 217.25, "response_status": "responded"},
			"the declared fields are re-evaluated at each record", backend.buffer[1].payload)
	check(calls == ["trial_id", "choice", "rt_ms", "response_status"], "fields evaluated once per record", calls)

	var observed := {"value": 217.25, "unit": "ms", "clock_id": "host_monotonic", "epoch": "p03r10", "source": "host trial timer"}
	var explicit: Dictionary = await data.record("exp.special", {"note": "explicit"}, schema("special", "2"), observed)
	check(not explicit.has("error"), "explicit special record", explicit)
	var explicit_event: Dictionary = backend.buffer[2]
	check(explicit_event.event_type == "exp.special" and explicit_event.schema_id == "special" and explicit_event.schema_version == "2",
			"explicit kind and schema win over the defaults", explicit_event)
	check(explicit_event.payload == {"note": "explicit"}, "the explicit payload replaces the declared fields", explicit_event.payload)
	check(explicit_event.observed_time == observed, "the explicit observed value is kept verbatim", explicit_event.observed_time)
	var without: Dictionary = await data.record("exp.special", {"note": "no clock"}, schema("special", "2"))
	check(not without.has("error"), "record without an observed value", without)
	check(not backend.buffer[3].has("observed_time"), "an empty observed value stays absent", backend.buffer[3])

	# ---- snapshot provider and payload replacement -------------------------
	var snapshot := {"action": "revise", "selection": ["shape_a", "shape_c"], "confidence": null,
			"nested": {"changes": [{"from": null, "to": "001"}], "confirmed": false}}
	var configured_provider := data.configure_defaults({"event_type": "exp.interaction",
			"schema": schema("interaction", "1"), "snapshot_provider": func(): return snapshot})
	check(configured_provider.get("state") == "defaults_configured", "provider configure", configured_provider)
	var interaction: Dictionary = await data.record()
	check(not interaction.has("error"), "short record from provider", interaction)
	check(backend.buffer[4].payload == snapshot, "the provider output is the payload", backend.buffer[4].payload)
	check(not backend.buffer[4].has("observed_time"), "the provider record has no implicit clock", backend.buffer[4])
	var replaced: Dictionary = await data.record("exp.interaction", {"only": "explicit"}, schema("interaction", "1"))
	check(not replaced.has("error"), "explicit payload over a provider", replaced)
	check(backend.buffer[5].payload == {"only": "explicit"}, "a non-null payload never merges with the provider", backend.buffer[5].payload)

	# ---- rejected records leave the raw buffer untouched -------------------
	var before: int = backend.buffer.size()
	var sequence_before: int = backend.sequence
	var bare := ExperimentData.new(backend)
	var source_less := ExperimentData.new(backend)
	var source_less_configured := source_less.configure_defaults({"event_type": "exp.x", "schema": schema("x", "1")})
	check(source_less_configured.get("state") == "defaults_configured", "source-less configure", source_less_configured)
	var record_errors := {}
	record_errors.invalid_schema = code(await data.record("exp.rt", {"trial_id": "t9"}, {"id": "rt"}))
	record_errors.unknown_defaults = code(await bare.record())
	record_errors.missing_default_schema = code(await bare.record("exp.x"))
	record_errors.no_payload_source = code(await source_less.record("exp.x", null, schema("x", "1")))
	record_errors.payload_array = code(await data.record("exp.x", [1, 2], schema("x", "1")))
	record_errors.payload_node = code(await data.record("exp.x", {"node": self}, schema("x", "1")))
	record_errors.observed_non_finite = code(await data.record("exp.x", {}, schema("x", "1"),
			{"value": INF, "unit": "ms", "clock_id": "c", "epoch": "e", "source": "s"}))
	facts.record_errors = record_errors
	check(record_errors == {"invalid_schema": "invalid_schema", "unknown_defaults": "invalid_defaults",
			"missing_default_schema": "invalid_defaults", "no_payload_source": "invalid_payload",
			"payload_array": "invalid_payload", "payload_node": "invalid_value",
			"observed_non_finite": "invalid_value"},
			"every rejected record reports its explicit error code", record_errors)
	check(backend.buffer.size() == before and backend.sequence == sequence_before,
			"rejected records wrote nothing to the raw buffer", {"buffer": backend.buffer.size(), "sequence": backend.sequence})

	# provider/field failures with their own explicit error channel
	var failing_provider := data.configure_defaults({"event_type": "exp.p", "schema": schema("p", "1"),
			"snapshot_provider": func(): return {"error": "sensor offline"}})
	check(failing_provider.get("state") == "defaults_configured", "failing provider configure", failing_provider)
	var provider_error: Dictionary = await data.record()
	check(code(provider_error) == "provider_error", "a provider {error:...} fails explicitly", provider_error)
	var wrong_type_provider := data.configure_defaults({"event_type": "exp.p", "schema": schema("p", "1"),
			"snapshot_provider": func(): return 42})
	check(wrong_type_provider.get("state") == "defaults_configured", "wrong-type provider configure", wrong_type_provider)
	var wrong_type_result: Dictionary = await data.record()
	check(code(wrong_type_result) == "invalid_provider", "a non-Dictionary provider result is an error", wrong_type_result)
	var node_label := Label.new()
	node_label.text = "stimulus"
	root.add_child(node_label)
	var node_value := data.configure_defaults({"event_type": "exp.n", "schema": schema("n", "1"),
			"fields": {"stimulus": func(): return node_label}})
	check(node_value.get("state") == "defaults_configured", "node-value configure", node_value)
	var node_result: Dictionary = await data.record()
	check(code(node_result) == "invalid_value", "a node value is an error", node_result)
	var bad_number := data.configure_defaults({"event_type": "exp.n", "schema": schema("n", "1"),
			"fields": {"value": func(): return NAN}})
	check(bad_number.get("state") == "defaults_configured", "NaN configure", bad_number)
	var nan_result: Dictionary = await data.record()
	check(code(nan_result) == "invalid_value", "a non-finite field value is an error", nan_result)
	var vector_value := data.configure_defaults({"event_type": "exp.n", "schema": schema("n", "1"),
			"fields": {"value": func(): return {"v": Vector2(1, 2)}}})
	check(vector_value.get("state") == "defaults_configured", "vector configure", vector_value)
	var vector_result: Dictionary = await data.record()
	check(code(vector_result) == "invalid_value", "a nested non-JSON value is an error", vector_result)
	var stale_holder := Object.new()
	var stale := data.configure_defaults({"event_type": "exp.n", "schema": schema("n", "1"),
			"fields": {"value": Callable(stale_holder, "get_class")}})
	check(stale.get("state") == "defaults_configured", "callable configure", stale)
	stale_holder.free()
	var stale_result: Dictionary = await data.record()
	check(code(stale_result) == "invalid_field", "a Callable that became invalid is an error", stale_result)
	check(backend.buffer.size() == before, "failed sources never reach the buffer", backend.buffer.size())

	# ---- copy timing before the await (spy backend) ------------------------
	var spy := SpyBackend.new()
	root.add_child(spy)
	var spy_data := ExperimentData.new(spy)
	var source := {"nested": {"list": [1, {"deep": "original"}], "flag": true}, "name": "source"}
	var spy_configured := spy_data.configure_defaults({"event_type": "exp.copy", "schema": schema("copy", "1"),
			"snapshot_provider": func(): return source})
	check(spy_configured.get("state") == "defaults_configured", "copy configure", spy_configured)
	var observed_source := {"value": 1.5, "unit": "ms", "clock_id": "c", "epoch": "e", "source": "s"}
	var copied: Dictionary = await spy_data.record("", null, {}, observed_source)
	check(not copied.has("error"), "spy record", copied)
	check(spy.calls.size() == 1, "the spy received one call", spy.calls.size())
	source.nested.list.append("mutated")
	source.nested.list[1].deep = "mutated"
	source.name = "mutated"
	observed_source.value = 99.0
	check(spy.calls[0].payload == {"nested": {"list": [1, {"deep": "original"}], "flag": true}, "name": "source"},
			"the provider payload was deep-copied at record time", spy.calls[0].payload)
	check(spy.calls[0].observed == {"value": 1.5, "unit": "ms", "clock_id": "c", "epoch": "e", "source": "s"},
			"the observed value was copied at record time", spy.calls[0].observed)
	var label_text := data.configure_defaults({"event_type": "exp.copy", "schema": schema("copy", "1"),
			"fields": {"stimulus": func(): return node_label.text}})
	check(label_text.get("state") == "defaults_configured", "node-read configure", label_text)
	var label_result: Dictionary = await data.record()
	check(not label_result.has("error"), "a field reading a node records", label_result)
	var label_event: Dictionary = backend.buffer[backend.buffer.size() - 1]
	node_label.text = "changed later"
	check(label_event.payload == {"stimulus": "stimulus"}, "a later node change never rewrites history", label_event.payload)

	# ---- record / commit / finish stay separate ----------------------------
	var buffered_before: int = backend.buffer.size()
	var records_before: int = backend.records.size()
	var committed: Dictionary = await data.commit({"version": 1, "strategy": "trial_boundary_v1", "next_trial": 1,
			"dependencies": [first.event_id]})
	check(committed.get("state") == "local_committed", "commit", committed)
	check(backend.records.size() == records_before + buffered_before and backend.buffer.is_empty(),
			"commit moved the buffered events into the committed set",
			{"records": backend.records.size(), "buffer": backend.buffer.size()})
	check(data.status().get("state") == "active", "commit does not finish", data.status())

	# ---- backpressure through the real backend -----------------------------
	var fill := 0
	while backend.buffer.size() < 64:
		var filled: Dictionary = await data.record("exp.fill", {"i": fill}, schema("fill", "1"))
		check(not filled.has("error"), "fill record " + str(fill), filled)
		fill += 1
	var backpressure: Dictionary = await data.record("exp.fill", {"i": fill}, schema("fill", "1"))
	check(code(backpressure) == "not_recording" and backend.buffer.size() == 64,
			"the real backend backpressure error passes through without a buffer write",
			{"error": code(backpressure), "buffer": backend.buffer.size()})
	facts.backpressure = {"error": code(backpressure), "buffer": backend.buffer.size(), "filled": fill}
	var drained: Dictionary = await data.commit()
	check(drained.get("state") == "local_committed" and backend.buffer.is_empty(),
			"a commit after backpressure drains the buffer", {"commit": drained, "buffer": backend.buffer.size()})

	# ---- explicit old-style sequence with a checkpoint ---------------------
	var rt: Dictionary = await data.record("exp.rt", {"trial_id": "t1", "choice": "left", "rt_ms": 321.5,
			"response_status": "responded"}, schema("rt", "1"), rt_clock(321.5))
	check(not rt.has("error"), "old explicit four-parameter call still works", rt)
	var old_commit: Dictionary = await data.commit({"version": 1, "strategy": "trial_boundary_v1", "next_trial": 1,
			"dependencies": [rt.event_id]})
	check(old_commit.get("state") == "local_committed", "old checkpoint commit", old_commit)
	var stored_rt: Dictionary = backend.records[backend.records.size() - 1]
	check(stored_rt.event_type == "exp.rt" and stored_rt.payload.get("rt_ms") == 321.5
			and stored_rt.observed_time == rt_clock(321.5), "the old explicit record is stored verbatim", stored_rt)

	var finished: Dictionary = await data.finish()
	check(finished.get("state") == "finished_saved", "finish", finished)
	var post_finish: Dictionary = await data.record("exp.fill", {"i": 0}, schema("fill", "1"))
	check(code(post_finish) == "not_recording", "record never finishes the session", post_finish)
	facts.responsibility = {"records_before_commit": records_before, "buffered_before_commit": buffered_before,
			"status_before_finish": "active", "finish": finished.get("state", ""),
			"post_finish_error": code(post_finish)}
	facts.final_state = finished


func run_gep() -> void:
	var backend := NativeBackend.new()
	root.add_child(backend)
	backend.config = {
		"config_version": "1", "protocol_version": "gep/1", "purpose": "synthetic", "mode": "anonymous",
		"api_url": OS.get_environment("GEP_DEFAULTS_API_URL"),
		"instance_id": OS.get_environment("GEP_DEFAULTS_INSTANCE"),
		"study_id": OS.get_environment("GEP_DEFAULTS_STUDY"),
		"release_id": OS.get_environment("GEP_DEFAULTS_RELEASE"),
		"build_id": OS.get_environment("GEP_DEFAULTS_BUILD"),
	}
	var data := ExperimentData.new(backend)
	var prepared: Dictionary = await data.prepare()
	check(prepared.get("state") == "active", "real GEP admission", prepared)
	var session_id: String = backend.session_id
	facts.session_id = session_id
	epoch = str(Time.get_ticks_usec())

	# Trial 1: the same two structures as the scientific task, recorded through
	# the declared defaults instead of the explicit four parameters.
	var trial := {"id": "t1", "choice": "left", "rt_ms": 321.5}
	var rt_fields := {
		"trial_id": func(): return trial.id,
		"choice": func(): return trial.choice,
		"rt_ms": func(): return trial.rt_ms,
		"response_status": func(): return "responded",
	}
	var snapshot := {"action": "revise", "selection": ["shape_a", "shape_c"], "confidence": null,
			"nested": {"changes": [{"from": null, "to": "001"}], "confirmed": false}}
	var configured := data.configure_defaults({"event_type": "exp.rt", "schema": schema("rt", "1"), "fields": rt_fields})
	check(configured.get("state") == "defaults_configured", "GEP defaults", configured)
	var first: Dictionary = await data.record("", null, {}, rt_clock(321.5))
	check(not first.has("error"), "GEP short record from fields", first)
	# A malformed schema must be rejected before the backend is reached: the
	# sequence and the buffer stay exactly where the accepted record left them.
	var sequence_before: int = backend.sequence
	var buffer_before: int = backend.buffer.size()
	var bad_schema: Dictionary = await data.record("exp.rt", {"trial_id": "t9"}, {"id": "rt"})
	check(code(bad_schema) == "invalid_schema" and backend.sequence == sequence_before
			and backend.buffer.size() == buffer_before,
			"an unknown schema never enters the raw buffer or the wire",
			{"error": code(bad_schema), "sequence": backend.sequence, "buffer": backend.buffer.size()})
	var provider_configured := data.configure_defaults({"event_type": "exp.interaction", "schema": schema("interaction", "1"),
			"snapshot_provider": func(): return snapshot})
	check(provider_configured.get("state") == "defaults_configured", "GEP provider defaults", provider_configured)
	var interaction_one: Dictionary = await data.record()
	check(not interaction_one.has("error"), "GEP short record from provider", interaction_one)
	var boundary_one: Dictionary = await data.commit({"version": 1, "strategy": "trial_boundary_v1", "next_trial": 1,
			"dependencies": [first.event_id, interaction_one.event_id]})
	check(boundary_one.get("state") == "local_committed", "GEP trial boundary one", boundary_one)

	# Trial 2: the defaults are reconfigured for the changed stimulus state.
	trial.id = "t2"
	trial.choice = "right"
	trial.rt_ms = 217.25
	var reconfigured := data.configure_defaults({"event_type": "exp.rt", "schema": schema("rt", "1"), "fields": rt_fields})
	check(reconfigured.get("state") == "defaults_configured", "GEP reconfigured defaults", reconfigured)
	var second: Dictionary = await data.record("", null, {}, rt_clock(217.25))
	check(not second.has("error"), "GEP second short record", second)
	var provider_again := data.configure_defaults({"event_type": "exp.interaction", "schema": schema("interaction", "1"),
			"snapshot_provider": func(): return snapshot})
	check(provider_again.get("state") == "defaults_configured", "GEP provider defaults again", provider_again)
	var interaction_two: Dictionary = await data.record()
	check(not interaction_two.has("error"), "GEP second provider record", interaction_two)
	# record() alone did not commit the fourth event yet.
	var before_finish: Dictionary = backend.read_session(session_id)
	check(before_finish.records.size() == 2 and backend.buffer.size() == 2,
			"GEP record does not commit", {"records": before_finish.records.size(), "buffer": backend.buffer.size()})
	var boundary_two: Dictionary = await data.commit({"version": 1, "strategy": "trial_boundary_v1", "next_trial": 2,
			"dependencies": [first.event_id, interaction_one.event_id, second.event_id, interaction_two.event_id]})
	check(boundary_two.get("state") == "local_committed", "GEP trial boundary two", boundary_two)
	var finished: Dictionary = await data.finish()
	check(finished.get("state") == "local_committed", "GEP finish", finished)
	var declared: Dictionary = backend.read_session(session_id)
	facts.records = declared.records
	facts.completion = declared.completion
	facts.pending = declared.pending.size()
	check(declared.records.size() == 4, "the GEP run stored exactly four events", declared.records.size())
	check(declared.pending.size() == 4 and declared.get("completion") != null, "four pending events and a completion set",
			{"pending": declared.pending.size(), "completion": declared.get("completion") != null})

	var acknowledged := false
	for _i in range(90):
		if data.status().get("state") == "remote_acknowledged":
			acknowledged = true
			break
		await create_timer(1.0).timeout
	check(acknowledged, "the real GEP completion was acknowledged", data.status())
	var cleaned: Dictionary = backend.read_session(session_id)
	facts.status = data.status()
	facts.cleaned = {"kind": cleaned.get("kind", ""), "state": cleaned.get("state", "")}
	check(cleaned.get("kind") == "cleaned" and cleaned.get("state") == "remote_acknowledged",
			"the acknowledged session is a cleaned tombstone", cleaned)
	check([facts.records[0].event_type, facts.records[1].event_type, facts.records[2].event_type, facts.records[3].event_type]
			== ["exp.rt", "exp.interaction", "exp.rt", "exp.interaction"],
			"the two structures are recorded in the scientific task order",
			facts.records.map(func(entry): return entry.event_type))


func chain(levels: int) -> Dictionary:
	## A nested Dictionary chain of exactly ``levels`` containers; the root is
	## depth 1. Used to exercise the exact MAX_VALUE_DEPTH boundary.
	var root := {}
	var cursor := root
	for _i in range(levels - 1):
		var child := {}
		cursor["next"] = child
		cursor = child
	cursor["end"] = true
	return root


func run_cycles() -> void:
	epoch = "p03r10r"
	var backend := LocalBackend.new()
	root.add_child(backend)
	var prepared: Dictionary = await backend.prepare()
	check(prepared.get("state") == "active", "the real local backend prepares for the cycle phase", prepared)
	var data := ExperimentData.new(backend)
	var trial := {"id": "t1", "choice": "left", "rt_ms": 321.5}
	var configured := data.configure_defaults({"event_type": "exp.rt", "schema": schema("rt", "1"), "fields": {
			"trial_id": func(): return trial.id, "choice": func(): return trial.choice,
			"rt_ms": func(): return trial.rt_ms, "response_status": func(): return "responded"}})
	check(configured.get("state") == "defaults_configured", "the cycle phase configures the field defaults", configured)
	facts.max_value_depth = ExperimentData.MAX_VALUE_DEPTH

	# Direct, indirect and mixed container cycles in every shape.
	var buffer_before: int = backend.buffer.size()
	var sequence_before: int = backend.sequence
	var cycle_errors := {}
	var direct_dict: Dictionary = {}
	direct_dict["self"] = direct_dict
	cycle_errors.direct_dictionary = code(await data.record("exp.rt", direct_dict, schema("rt", "1")))
	var indirect_a: Dictionary = {}
	var indirect_b: Dictionary = {"back": indirect_a}
	indirect_a["forward"] = indirect_b
	cycle_errors.indirect_dictionary = code(await data.record("exp.rt", indirect_a, schema("rt", "1")))
	var direct_array: Array = []
	direct_array.append(direct_array)
	cycle_errors.direct_array = code(await data.record("exp.rt", {"list": direct_array}, schema("rt", "1")))
	var indirect_array: Array = []
	var indirect_list: Array = [indirect_array]
	indirect_array.append(indirect_list)
	cycle_errors.indirect_array = code(await data.record("exp.rt", {"list": indirect_list}, schema("rt", "1")))
	var mixed: Dictionary = {"items": []}
	mixed.items.append({"owner": mixed})
	cycle_errors.mixed = code(await data.record("exp.rt", mixed, schema("rt", "1")))
	var deep_cycle: Dictionary = {"a": {"b": {"c": {}}}}
	deep_cycle.a.b.c.loop = deep_cycle
	cycle_errors.deep = code(await data.record("exp.rt", deep_cycle, schema("rt", "1")))
	var cycle_observed: Dictionary = {}
	cycle_observed["self"] = cycle_observed
	cycle_errors.observed = code(await data.record("exp.rt", {"trial_id": "t1"}, schema("rt", "1"), cycle_observed))
	facts.cycle_errors = cycle_errors
	check(cycle_errors == {"direct_dictionary": "invalid_value", "indirect_dictionary": "invalid_value",
			"direct_array": "invalid_value", "indirect_array": "invalid_value", "mixed": "invalid_value",
			"deep": "invalid_value", "observed": "invalid_value"},
			"every direct/indirect/mixed cycle is an explicit invalid_value", cycle_errors)
	check(backend.buffer.size() == buffer_before and backend.sequence == sequence_before,
			"cyclic values never reached the backend sequence or buffer",
			{"buffer": backend.buffer.size(), "sequence": backend.sequence})

	# Cycles through the declared sources (provider, field, mutual fields).
	var provider_cycle: Dictionary = {}
	provider_cycle["self"] = provider_cycle
	var provider_configured := data.configure_defaults({"event_type": "exp.p", "schema": schema("p", "1"),
			"snapshot_provider": func(): return provider_cycle})
	check(provider_configured.get("state") == "defaults_configured",
			"a cyclic provider configures and is checked at record time", provider_configured)
	var provider_result: Dictionary = await data.record()
	check(code(provider_result) == "invalid_value", "a cyclic provider output is invalid_value", provider_result)
	var field_cycle: Dictionary = {}
	field_cycle["self"] = field_cycle
	var field_configured := data.configure_defaults({"event_type": "exp.f", "schema": schema("f", "1"),
			"fields": {"value": func(): return field_cycle}})
	check(field_configured.get("state") == "defaults_configured",
			"a cyclic field value configures and is checked at record time", field_configured)
	var field_result: Dictionary = await data.record()
	check(code(field_result) == "invalid_value", "a cyclic field value is invalid_value", field_result)
	var mutual_a: Dictionary = {}
	var mutual_b: Dictionary = {"other": mutual_a}
	mutual_a["other"] = mutual_b
	var mutual_configured := data.configure_defaults({"event_type": "exp.m", "schema": schema("m", "1"),
			"fields": {"a": func(): return mutual_a, "b": func(): return mutual_b}})
	check(mutual_configured.get("state") == "defaults_configured", "a mutual field cycle configures", mutual_configured)
	var mutual_result: Dictionary = await data.record()
	check(code(mutual_result) == "invalid_value", "a cycle spanning two declared fields is invalid_value", mutual_result)
	check(backend.buffer.size() == buffer_before and backend.sequence == sequence_before,
			"declared-source cycles never reached the backend",
			{"buffer": backend.buffer.size(), "sequence": backend.sequence})
	# The mutual-field declaration was an accepted configuration, so the working
	# field defaults are re-established before the recovery part.
	var restored := data.configure_defaults({"event_type": "exp.rt", "schema": schema("rt", "1"), "fields": {
			"trial_id": func(): return trial.id, "choice": func(): return trial.choice,
			"rt_ms": func(): return trial.rt_ms, "response_status": func(): return "responded"}})
	check(restored.get("state") == "defaults_configured", "the working defaults are re-established", restored)

	# The exact depth boundary and a legitimate shared acyclic subtree.
	var depth_ok: Dictionary = await data.record("exp.deep", chain(ExperimentData.MAX_VALUE_DEPTH), schema("deep", "1"))
	check(not depth_ok.has("error"), "a value at exactly MAX_VALUE_DEPTH is valid", depth_ok)
	var depth_over: Dictionary = await data.record("exp.deep", chain(ExperimentData.MAX_VALUE_DEPTH + 1), schema("deep", "1"))
	check(code(depth_over) == "invalid_value",
			"one level past MAX_VALUE_DEPTH is invalid_value before any copy", depth_over)
	facts.depth = {"max": ExperimentData.MAX_VALUE_DEPTH, "at_boundary": not depth_ok.has("error"),
			"boundary_plus_one": code(depth_over)}
	var shared := {"leaf": [1, 2, {"note": "shared"}]}
	var shared_result: Dictionary = await data.record("exp.shared",
			{"first": shared, "second": shared, "third": [shared, shared]}, schema("shared", "1"))
	check(not shared_result.has("error"), "a shared but acyclic subtree is valid", shared_result)
	var shared_event: Dictionary = backend.buffer[backend.buffer.size() - 1]
	shared.leaf.append("mutated")
	shared.leaf[2].note = "mutated"
	check(shared_event.payload == {"first": {"leaf": [1, 2, {"note": "shared"}]},
			"second": {"leaf": [1, 2, {"note": "shared"}]},
			"third": [{"leaf": [1, 2, {"note": "shared"}]}, {"leaf": [1, 2, {"note": "shared"}]}]},
			"the shared subtree was copied instead of pruned, rejected or aliased", shared_event.payload)
	facts.shared_leaf = shared_event.payload.first.leaf

	# Schema values are guarded before any copy and a rejected configuration
	# never replaces the working defaults.
	var cyclic_schema: Dictionary = {"id": "x", "version": "1"}
	cyclic_schema["self"] = cyclic_schema
	var schema_cycle := data.configure_defaults({"event_type": "exp.x", "schema": cyclic_schema})
	check(code(schema_cycle) == "invalid_value", "a cyclic schema is rejected before the copy", schema_cycle)
	var deep_schema := chain(ExperimentData.MAX_VALUE_DEPTH + 1)
	deep_schema.id = "x"
	deep_schema.version = "1"
	var schema_deep := data.configure_defaults({"event_type": "exp.x", "schema": deep_schema})
	check(code(schema_deep) == "invalid_value", "an over-deep schema is rejected before the copy", schema_deep)
	var still_configured: Dictionary = await data.record()
	check(not still_configured.has("error"), "a rejected schema configuration keeps the previous defaults", still_configured)
	facts.schema_guards = {"cycle": code(schema_cycle), "deep": code(schema_deep),
			"old_defaults_kept": not still_configured.has("error")}

	# Bounded details never echo the original payload.
	var canary := "CANARY-SECRET-9d2f"
	var canary_cycle: Dictionary = {"secret": canary}
	canary_cycle["self"] = canary_cycle
	var canary_result: Dictionary = await data.record("exp.rt", canary_cycle, schema("rt", "1"))
	var canary_detail := str(canary_result.get("detail", ""))
	facts.detail_bounds = {"length": canary_detail.length(), "echoes_payload": canary_detail.contains(canary)}
	check(code(canary_result) == "invalid_value" and canary_detail.length() <= 160
			and not canary_detail.contains(canary),
			"the rejection detail is bounded and never echoes the payload", facts.detail_bounds)

	# Real SQLite recovery: a refusal leaves the backend usable, a real storage
	# failure is explicit, and repairing it lets the retained records commit.
	var records_before: int = backend.records.size()
	var buffered_before: int = backend.buffer.size()
	trial.id = "t9"
	trial.choice = "right"
	trial.rt_ms = 99.25
	var recovered: Dictionary = await data.record()
	check(not recovered.has("error"), "record after the refusals", recovered)
	var recovered_event: Dictionary = backend.buffer[backend.buffer.size() - 1]
	var first_commit: Dictionary = await data.commit({"version": 1, "strategy": "trial_boundary_v1", "next_trial": 1,
			"dependencies": [recovered_event.event_id]})
	check(first_commit.get("state") == "local_committed", "the real SQLite commit after the refusals", first_commit)
	var retry_event: Dictionary = await data.record()
	check(not retry_event.has("error"), "record before the storage failure", retry_event)
	# A real SQLite refusal: the store's own table disappears under the backend,
	# so the next commit is a genuine engine error and must be reported as
	# ``local_commit`` without touching records, buffer or the retained rows.
	backend.db.query("DROP TABLE runs")
	var failed_commit: Dictionary = await data.commit()
	check(code(failed_commit) == "local_commit", "a real SQLite refusal is local_commit", failed_commit)
	check(backend.records.size() == records_before + buffered_before + 1 and backend.buffer.size() == 1,
			"a refused commit keeps the committed records and the buffer",
			{"records": backend.records.size(), "buffer": backend.buffer.size()})
	backend.db.query("CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, value TEXT NOT NULL)")
	var retried: Dictionary = await data.commit()
	check(retried.get("state") == "local_committed" and backend.buffer.is_empty(),
			"the retained records commit after the real SQLite refusal is repaired", retried)
	var finished: Dictionary = await data.finish()
	check(finished.get("state") == "finished_saved", "the real SQLite finish after the refusals", finished)
	facts.recovery = {"records": backend.records.size(), "buffer": backend.buffer.size(),
			"state": finished.get("state", ""), "last_event_id": retry_event.event_id,
			"stored_last": backend.records[backend.records.size() - 1].payload}
	var shared_golden := {"first": {"leaf": [1, 2, {"note": "shared"}]},
			"second": {"leaf": [1, 2, {"note": "shared"}]},
			"third": [{"leaf": [1, 2, {"note": "shared"}]}, {"leaf": [1, 2, {"note": "shared"}]}]}
	facts.shared_golden = shared_golden
	var stored_shared: Dictionary = {}
	for record in backend.records:
		if record.event_type == "exp.shared":
			stored_shared = record
	check(stored_shared.get("payload", {}) == shared_golden,
			"the stored shared payload kept its pre-mutation values", stored_shared.get("payload"))


func run_gep_negative() -> void:
	var backend := NativeBackend.new()
	root.add_child(backend)
	backend.config = {
		"config_version": "1", "protocol_version": "gep/1", "purpose": "synthetic", "mode": "anonymous",
		"api_url": OS.get_environment("GEP_DEFAULTS_API_URL"),
		"instance_id": OS.get_environment("GEP_DEFAULTS_INSTANCE"),
		"study_id": OS.get_environment("GEP_DEFAULTS_STUDY"),
		"release_id": OS.get_environment("GEP_DEFAULTS_RELEASE"),
		"build_id": OS.get_environment("GEP_DEFAULTS_BUILD"),
	}
	var data := ExperimentData.new(backend)
	var prepared: Dictionary = await data.prepare()
	check(prepared.get("state") == "active", "the real GEP admission for the negative phase", prepared)
	var session_id: String = backend.session_id
	facts.session_id = session_id
	epoch = str(Time.get_ticks_usec())
	var trial := {"id": "t1", "choice": "left", "rt_ms": 321.5}
	var configured := data.configure_defaults({"event_type": "exp.rt", "schema": schema("rt", "9"), "fields": {
			"trial_id": func(): return trial.id, "choice": func(): return trial.choice,
			"rt_ms": func(): return trial.rt_ms, "response_status": func(): return "responded"}})
	check(configured.get("state") == "defaults_configured",
			"a well-shaped but unregistered schema id/version configures locally", configured)
	var recorded: Dictionary = await data.record("", null, {}, rt_clock(321.5))
	check(not recorded.has("error"), "the client shape check forwards the unregistered id/version", recorded)
	facts.recorded = recorded
	var committed: Dictionary = await data.commit({"version": 1, "strategy": "trial_boundary_v1", "next_trial": 1,
			"dependencies": [recorded.event_id]})
	check(committed.get("state") == "local_committed", "the local commit before the server verdict", committed)
	var finished: Dictionary = await data.finish()
	check(finished.get("state") == "local_committed", "the local completion before the server verdict", finished)
	var verdict := {}
	for _i in range(45):
		var s: Dictionary = backend.read_session(session_id)
		var delivery: Dictionary = s.get("delivery", {})
		if not str(delivery.get("last_error_kind", "")).is_empty():
			verdict = {"kind": str(delivery.get("last_error_kind", "")), "error": str(delivery.get("last_error", "")),
					"paused": bool(s.get("paused", false)), "attempts": int(s.get("attempts", 0))}
			break
		await create_timer(1.0).timeout
	facts.verdict = verdict
	var kept: Dictionary = backend.read_session(session_id)
	var kept_record: Dictionary = kept.records[0]
	facts.kept = {"event_id": str(kept_record.event_id), "event_type": str(kept_record.event_type),
			"schema_id": str(kept_record.schema_id), "schema_version": str(kept_record.schema_version),
			"payload": kept_record.payload, "records": kept.records.size(), "pending": kept.pending.size(),
			"complete_ack": kept.get("complete_ack") != null}
	check(verdict.get("kind", "") == "schema_binding",
			"the real server refusal is recorded as a schema_binding failure", verdict)
	check(verdict.get("paused") == true, "the permanent refusal pauses further uploads", verdict)
	check(kept.records.size() == 1 and kept.pending.size() == 1 and kept.get("complete_ack") == null,
			"the original record identity and pending data stay",
			{"records": kept.records.size(), "pending": kept.pending.size()})
