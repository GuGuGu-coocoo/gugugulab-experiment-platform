extends RefCounted
## The experiment owns this interface; no platform-specific types or addresses.
var backend: Node
func _init(selected: Node) -> void:
	backend = selected
func prepare(options: Dictionary = {}) -> Dictionary:
	return await backend.prepare(options)
func record(kind: String, payload: Dictionary, schema: Dictionary, observed: Dictionary = {}) -> Dictionary:
	return await backend.record(kind, payload, schema, observed)
func commit(checkpoint: Dictionary = {}) -> Dictionary:
	return await backend.commit(checkpoint)
func status() -> Dictionary:
	return backend.status()
func finish() -> Dictionary:
	return await backend.finish()
