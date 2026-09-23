extends Control
## P03R10R Web defaults demo assembly for a test-only project copy.
##
## The P03R10R browser test copies the synthetic experiment project to a fresh
## build root and replaces only its bootstrap script with this file, so the real
## exported engine, bridge and browser SDK stay in place: every record below
## goes through the real GDScript wrapper (``data/experiment_data.gd``) and the
## real SDK/IndexedDB path, not through JavaScript called from the test.
##
## The demo records the same two scientific structures as the R10 Web golden
## through ``configure_defaults`` (declared fields and one snapshot provider),
## plus an explicit four-parameter override, then mutates the declared sources
## and proves the stored values keep their record-time copies. A direct cycle
## and an over-deep value are rejected as ``invalid_value`` and add no event.
## Nothing here touches the scientific task: the copy's ``task.gd`` stays
## byte-identical and is never instantiated.
##
## It is driven by the real shell: a participant clicks start, the local-only
## preview admits, this script runs the demo, commits and finishes, and the
## shell announces the local test completion. One ``DATA_DEFAULTS_WEB {summary}``
## line is printed for the browser test to reconcile with IndexedDB and the
## downloaded JSONL.
const Shell = preload("res://addons/gec/shell.gd")
const DataModule = preload("res://data/experiment_data.gd")

var shell
var data
var failures: Array = []


func check(condition: bool, label: String) -> void:
	if not condition:
		failures.append(label)


func _ready() -> void:
	var backend: Node = load("res://addons/gec/web_backend.gd").new()
	add_child(backend)
	data = DataModule.new(backend)
	shell = Shell.new()
	shell.name = "GECShell"
	add_child(shell)
	shell.session_started.connect(_on_session_started)
	var prepared: Dictionary = shell.setup(backend)
	if prepared.has("error"):
		failures.append("shell setup: " + JSON.stringify(prepared))


func _on_session_started(_checkpoint) -> void:
	await run_demo()


func run_demo() -> void:
	var trial := {"id": "t1", "choice": "left", "rt_ms": 321.5}
	var configured: Dictionary = data.configure_defaults({"event_type": "exp.rt", "schema": {"id": "rt", "version": "1"},
			"fields": {"trial_id": func(): return trial.id, "choice": func(): return trial.choice,
					"rt_ms": func(): return trial.rt_ms, "response_status": func(): return "responded"}})
	check(configured.get("state") == "defaults_configured", "configure fields: " + JSON.stringify(configured))
	var first: Dictionary = await data.record("", null, {}, {"value": 321.5, "unit": "ms",
			"clock_id": "host_monotonic", "epoch": "p03r10r", "source": "Godot Time.get_ticks_usec"})
	check(not first.has("error"), "first short record: " + JSON.stringify(first))
	# The second structure is the declared snapshot provider.
	var snapshot := {"action": "revise", "selection": ["shape_a", "shape_c"], "confidence": null,
			"nested": {"changes": [{"from": null, "to": "001"}], "confirmed": false}}
	var provider_configured: Dictionary = data.configure_defaults({"event_type": "exp.interaction",
			"schema": {"id": "interaction", "version": "1"}, "snapshot_provider": func(): return snapshot})
	check(provider_configured.get("state") == "defaults_configured",
			"configure provider: " + JSON.stringify(provider_configured))
	var second: Dictionary = await data.record()
	check(not second.has("error"), "provider record: " + JSON.stringify(second))
	# The declared source changes right after the record; the stored event must
	# still be the trial-1 copy, and the explicit four-parameter call replaces
	# the declared fields and schema for the second trial.
	trial.id = "t2"
	trial.choice = "right"
	trial.rt_ms = 217.25
	var third: Dictionary = await data.record("exp.rt", {"trial_id": "t2", "choice": "right", "rt_ms": 217.25,
			"response_status": "responded"}, {"id": "rt", "version": "1"}, {"value": 217.25, "unit": "ms",
			"clock_id": "host_monotonic", "epoch": "p03r10r", "source": "Godot Time.get_ticks_usec"})
	check(not third.has("error"), "explicit override record: " + JSON.stringify(third))
	var fourth: Dictionary = await data.record()
	check(not fourth.has("error"), "second provider record: " + JSON.stringify(fourth))
	# A direct cycle and an over-deep value are explicit errors with no event.
	var cyclic: Dictionary = {}
	cyclic["self"] = cyclic
	var cycle_result: Dictionary = await data.record("exp.rt", cyclic, {"id": "rt", "version": "1"})
	check(cycle_result.get("error") == "invalid_value", "cycle rejected: " + JSON.stringify(cycle_result))
	var deep := {}
	var cursor := deep
	for _i in range(70):
		var child := {}
		cursor["next"] = child
		cursor = child
	cursor["end"] = true
	var deep_result: Dictionary = await data.record("exp.rt", deep, {"id": "rt", "version": "1"})
	check(deep_result.get("error") == "invalid_value", "depth rejected: " + JSON.stringify(deep_result))
	# Both declared sources keep changing after the records; the copies must not.
	snapshot.selection.append("shape_z")
	snapshot.nested.confirmed = true
	trial.id = "t9"
	trial.choice = "left"
	trial.rt_ms = -1.0
	var committed: Dictionary = await data.commit({"version": 1, "strategy": "trial_boundary_v1", "next_trial": 2,
			"dependencies": [first.event_id, second.event_id, third.event_id, fourth.event_id]})
	check(not committed.has("error"), "commit: " + JSON.stringify(committed))
	var finished: Dictionary = await data.finish()
	check(finished.get("state") == "finished_saved", "finish: " + JSON.stringify(finished))
	var summary := {"failures": failures, "cycle": cycle_result, "deep": deep_result,
			"state": finished.get("state", ""), "events": [first, second, third, fourth]}
	print("DATA_DEFAULTS_WEB " + JSON.stringify(summary))
	shell.announce_finished(finished)
