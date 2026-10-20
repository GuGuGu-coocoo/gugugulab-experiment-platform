extends RefCounted
## Synthetic policy: two complete trial boundaries; fixed order, no reconstructed RT.
var data
var next_trial := 0
var dependencies: Array = []
var epoch: String
func _init(module) -> void:
	data = module
	epoch = str(Time.get_ticks_usec())
func response(choice: String, rt_ms: float) -> Dictionary:
	var result = await data.record("exp.rt", {"trial_id": "t" + str(next_trial + 1), "choice": choice, "rt_ms": rt_ms, "response_status": "responded"}, {"id":"rt", "version":"1"}, {"value":rt_ms,"unit":"ms","clock_id":"host_monotonic","epoch":epoch,"source":"Godot Time.get_ticks_usec"})
	if result.has("error"): return result
	dependencies.append(result.event_id)
	result = await data.record("exp.interaction", {"action":"revise","selection":["shape_a","shape_c"],"confidence":null,"nested":{"changes":[{"from":null,"to":"001"}],"confirmed":false}}, {"id":"interaction","version":"1"})
	if result.has("error"): return result
	dependencies.append(result.event_id)
	var checkpoint = {"version":1,"strategy":"trial_boundary_v1","next_trial":next_trial+1,"order":["left","right"],"random_state":"fixed_order_no_rng","dependencies":dependencies.duplicate()}
	result = await data.commit(checkpoint)
	if not result.has("error"): next_trial += 1
	return result
func restore(checkpoint: Dictionary) -> void:
	assert(checkpoint.version == 1 and checkpoint.strategy == "trial_boundary_v1")
	next_trial = int(checkpoint.next_trial)
	dependencies = checkpoint.dependencies.duplicate()
