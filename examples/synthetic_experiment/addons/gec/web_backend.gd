extends Node
## Web companion adapter: the browser SDK owns durable local records, the bridge
## forwards the same shell contract as the native backend. No JavaScript is used
## by the science task.
var current := {"state":"unprepared"}
var bridge
var config: Dictionary = {}
var calls := 0
func call_bridge(op: String, args: Array = []) -> Variant:
	if bridge == null: bridge = JavaScriptBridge.get_interface("GECBridge")
	if bridge == null: return {"error":"bridge_unavailable"}
	# The shell polls the summary while the task records and commits, so two
	# bridge calls can be in flight at once. The key must stay unique even when
	# the platform clock cannot distinguish two calls in the same tick.
	calls += 1
	var key = str(Time.get_ticks_usec()) + "-" + str(calls)
	bridge.call(key,op,JSON.stringify(args))
	while true:
		var value = bridge.take(key)
		if value != null:
			var result = JSON.parse_string(value)
			if result is Dictionary or result is Array:
				if result is Dictionary: current = result
				return result
			return {"error":"invalid_bridge_result"}
		await get_tree().process_frame
	return {"error":"bridge_unavailable"}
func context() -> Dictionary:
	if bridge == null: bridge = JavaScriptBridge.get_interface("GECBridge")
	if bridge == null: return {}
	var parsed = JSON.parse_string(bridge.context_json())
	return parsed if parsed is Dictionary else {}
func local_test() -> bool:
	## The Web isolated preview runs against the local store only: no server
	## admission and no remote acknowledgement.
	var parsed := context()
	if parsed.has("preview"):
		return bool(parsed.preview)
	return bool(config.get("preview", false))
func prepare(options: Dictionary = {}) -> Dictionary:
	return await call_bridge("start", [options])
func record(kind: String, payload: Dictionary, schema: Dictionary, observed: Dictionary = {}) -> Dictionary:
	return await call_bridge("record", [kind,payload,schema,observed if not observed.is_empty() else null])
func commit(checkpoint: Dictionary = {}) -> Dictionary:
	return await call_bridge("commit", [checkpoint if not checkpoint.is_empty() else null])
func finish() -> Dictionary:
	return await call_bridge("finish")
func status() -> Dictionary:
	return JSON.parse_string(bridge.status_json()) if bridge != null else current
func summary() -> Dictionary:
	return await call_bridge("summary")
func candidates() -> Array:
	var result = await call_bridge("candidates")
	return result if result is Array else []
func recover_code(code: String) -> Dictionary:
	return await call_bridge("recover_code", [code])
func recover_named(participant_code: String, password: String) -> Dictionary:
	return await call_bridge("recover_named", [participant_code,password])
func confirm_recovery(session_id: String, token: String, can_resume: bool) -> Dictionary:
	return await call_bridge("confirm_recovery", [session_id,token,can_resume])

func recovery_export() -> Dictionary:
	return await call_bridge("recovery_export")
func download_recovery() -> Dictionary:
	return await call_bridge("download_recovery")
func download_results() -> Dictionary:
	return await call_bridge("download_results")
