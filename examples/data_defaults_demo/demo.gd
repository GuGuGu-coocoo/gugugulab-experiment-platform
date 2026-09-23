extends SceneTree
## P03R10 developer example: declare the usual event once, then record short.
##
## This is a standalone demonstration, not the scientific task. It uses the real
## independent local backend (real SQLite), so the recorded values are durable
## and the result JSONL can be inspected. Nothing here touches the scientific
## task, the server or the network.
##
## Run from the repository root:
##   godot --headless --path examples/synthetic_experiment --script ../data_defaults_demo/demo.gd
##
## Environment (optional):
##   GEP_SYNTHETIC_STORAGE  local store directory (default user://data_defaults_demo)
##   GEP_SYNTHETIC_RESULTS  result directory    (default user://data_defaults_demo_results)
##
## Prints one ``DATA_DEFAULTS_DEMO {...}`` JSON line and exits 0 on success.

const ExperimentData = preload("res://data/experiment_data.gd")
const LocalBackend = preload("res://data/local_backend.gd")

var failures: Array = []


func _initialize() -> void:
	call_deferred("run")


func check(condition: bool, label: String) -> void:
	if not condition:
		failures.append(label)


func run() -> void:
	var backend := LocalBackend.new()
	root.add_child(backend)
	var data := ExperimentData.new(backend)
	var prepared: Dictionary = await data.prepare()
	if prepared.has("error"):
		print("DATA_DEFAULTS_DEMO_FAILED ", prepared.error)
		quit(2)
		return

	# 1) Declared field callables. The stimulus state stays the developer's; the
	# callables read it explicitly at record() time and no scene tree is scanned.
	var trial := {"id": "t1", "choice": "left", "rt_ms": 321.5}
	var configured := data.configure_defaults({
		"event_type": "exp.rt",
		"schema": {"id": "rt", "version": "1"},
		"fields": {
			"trial_id": func(): return trial.id,
			"choice": func(): return trial.choice,
			"rt_ms": func(): return trial.rt_ms,
			"response_status": func(): return "responded",
		},
	})
	check(not configured.has("error"), "configure_defaults fields: " + str(configured))
	# The observed time stays an explicit host value; the wrapper never reads a
	# clock of its own.
	var observed := {"value": trial.rt_ms, "unit": "ms", "clock_id": "host_monotonic",
			"epoch": "demo", "source": "host trial timer"}
	var response: Dictionary = await data.record("", null, {}, observed)
	check(not response.has("error"), "short record from fields: " + str(response))

	# 2) One snapshot provider instead of a field list.
	var snapshot := {"action": "revise", "selection": ["shape_a", "shape_c"], "confidence": null,
			"nested": {"changes": [{"from": null, "to": "001"}], "confirmed": false}}
	var configured_provider := data.configure_defaults({
		"event_type": "exp.interaction",
		"schema": {"id": "interaction", "version": "1"},
		"snapshot_provider": func(): return snapshot,
	})
	check(not configured_provider.has("error"), "configure_defaults provider: " + str(configured_provider))
	var interaction: Dictionary = await data.record()
	check(not interaction.has("error"), "short record from provider: " + str(interaction))

	# 3) A special event keeps the original explicit four-parameter call.
	var special: Dictionary = await data.record("exp.special", {"note": "explicit override"},
			{"id": "special", "version": "1"})
	check(not special.has("error"), "explicit special record: " + str(special))

	# record() alone never commits: commit() and finish() stay explicit.
	var committed: Dictionary = await data.commit({"version": 1, "strategy": "trial_boundary_v1", "next_trial": 1,
			"dependencies": [response.event_id, interaction.event_id, special.event_id]})
	check(not committed.has("error"), "commit: " + str(committed))
	var finished: Dictionary = await data.finish()
	check(finished.get("state") == "finished_saved", "finish: " + str(finished))

	var saved: Dictionary = await backend.save_results()
	check(not saved.has("error"), "save_results: " + str(saved))
	var summary := {"state": finished.get("state", ""), "records": backend.records,
			"path": saved.get("path", ""), "failures": failures}
	print("DATA_DEFAULTS_DEMO ", JSON.stringify(summary))
	quit(0 if failures.is_empty() else 1)
