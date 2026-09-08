extends Node
var current := {"state":"unprepared"}
var bridge
func call_bridge(op: String, args: Array = []) -> Dictionary:
	if bridge == null: bridge = JavaScriptBridge.get_interface("GECBridge")
	if bridge == null: return {"error":"bridge_unavailable"}
	var key = str(Time.get_ticks_usec())
	bridge.call(key,op,JSON.stringify(args))
	while true:
		var value = bridge.take(key)
		if value != null:
			var result = JSON.parse_string(value)
			if result is Dictionary:
				current = result
				return result
			return {"error":"invalid_bridge_result"}
		await get_tree().process_frame
	return {"error":"bridge_unavailable"}
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
