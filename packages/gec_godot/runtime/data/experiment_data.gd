extends RefCounted
## The experiment owns this interface; no platform-specific types or addresses.
##
## P03R10 thin wrapper: ``configure_defaults()`` declares the usual event type,
## schema and either an explicit field map or one snapshot provider. A short
## ``record()`` then evaluates those declared values and deep-copies them at the
## call, before any await, and forwards the unchanged four-parameter contract to
## the selected backend. An explicit payload always replaces the declared
## source (no implicit merge); explicit kind/schema win over the defaults;
## ``record()`` never commits or finishes and never reads an implicit clock or
## the scene tree. The backend still validates the schema binding and
## backpressure, and every configuration/record problem is an explicit error.
##
## Every value that would be copied (payload, observed, schema, provider output,
## field output) is checked first: direct/indirect/mixed container cycles and
## nesting deeper than ``MAX_VALUE_DEPTH`` are explicit errors instead of engine
## recursion failures; a shared but acyclic subtree stays valid. Only the shape
## of ``schema`` is checked here - whether an id/version pair is registered is a
## server-side fact, and this wrapper has no schema registry.
const ERROR_INVALID_DEFAULTS := "invalid_defaults"
const ERROR_INVALID_CONFIG := "invalid_config"
const ERROR_INVALID_SCHEMA := "invalid_schema"
const ERROR_INVALID_PAYLOAD := "invalid_payload"
const ERROR_INVALID_PROVIDER := "invalid_provider"
const ERROR_INVALID_FIELD := "invalid_field"
const ERROR_INVALID_VALUE := "invalid_value"
const ERROR_PROVIDER_ERROR := "provider_error"
const ERROR_FIELD_ERROR := "field_error"
const CONFIG_KEYS := ["event_type", "schema", "fields", "snapshot_provider"]
## Maximum container nesting of a copied value: the root Dictionary/Array is
## depth 1 and every nested container adds one level. A value that would need
## more is rejected as ``invalid_value`` before any copy or recursion limit.
const MAX_VALUE_DEPTH := 64
const _DETAIL_LIMIT := 160
const _KEY_LIMIT := 32

var backend: Node
var defaults: Dictionary = {}
func _init(selected: Node) -> void:
	backend = selected

func configure_defaults(config: Dictionary) -> Dictionary:
	## One explicit declaration of the usual event: ``event_type``, ``schema``
	## and either ``fields`` ({field_name: Callable}, evaluated one by one at
	## record time) or ``snapshot_provider`` (one Callable returning the whole
	## payload Dictionary). The two sources are mutually exclusive. A rejected
	## configuration never replaces the previous one.
	var checked := _checked_config(config)
	if checked.has("error"):
		return checked
	defaults = {"event_type": config.event_type, "schema": config.schema.duplicate(true)}
	if config.has("fields"):
		var fields := {}
		for name in config.fields:
			fields[name] = config.fields[name]
		defaults.fields = fields
	if config.has("snapshot_provider"):
		defaults.snapshot_provider = config.snapshot_provider
	return {"state": "defaults_configured"}

func _checked_config(config: Dictionary) -> Dictionary:
	for key in config:
		if not CONFIG_KEYS.has(key):
			return _error(ERROR_INVALID_CONFIG, "unknown configuration key: " + str(key))
	if not (config.get("event_type") is String) or config.event_type.is_empty():
		return _error(ERROR_INVALID_CONFIG, "event_type must be a non-empty String")
	var schema_check := _checked_schema(config.get("schema"))
	if schema_check.has("error"):
		return schema_check
	var schema_value_check := _checked_value(config.schema, "schema")
	if schema_value_check.has("error"):
		return schema_value_check
	if config.has("fields") and config.has("snapshot_provider"):
		return _error(ERROR_INVALID_CONFIG, "fields and snapshot_provider are mutually exclusive")
	if config.has("fields"):
		if not (config.fields is Dictionary):
			return _error(ERROR_INVALID_CONFIG, "fields must be a Dictionary of field_name -> Callable")
		for name in config.fields:
			if not (name is String) or name.is_empty():
				return _error(ERROR_INVALID_CONFIG, "field names must be non-empty Strings")
			var callable = config.fields[name]
			if not (callable is Callable) or not callable.is_valid():
				return _error(ERROR_INVALID_FIELD, "field " + str(name) + " is not a valid Callable")
	if config.has("snapshot_provider"):
		var provider = config.snapshot_provider
		if not (provider is Callable) or not provider.is_valid():
			return _error(ERROR_INVALID_PROVIDER, "snapshot_provider is not a valid Callable")
	return {}

func _checked_schema(schema) -> Dictionary:
	if not (schema is Dictionary):
		return _error(ERROR_INVALID_SCHEMA, "schema must be a Dictionary")
	if not (schema.get("id") is String) or schema.id.is_empty():
		return _error(ERROR_INVALID_SCHEMA, "schema.id must be a non-empty String")
	if not (schema.get("version") is String) or schema.version.is_empty():
		return _error(ERROR_INVALID_SCHEMA, "schema.version must be a non-empty String")
	return {}

func record(kind: String = "", payload = null, schema: Dictionary = {}, observed: Dictionary = {}) -> Dictionary:
	## The original four parameters stay: explicit kind/schema win; a non-null
	## payload completely replaces the declared source; a null payload reads the
	## declared snapshot provider or field list. Everything is validated and
	## deep-copied before the first await, so a later change of the source
	## dictionary or node never rewrites the recorded event.
	var prepared := _prepared(kind, payload, schema, observed)
	if prepared.has("error"):
		return prepared
	return await backend.record(prepared.kind, prepared.payload, prepared.schema, prepared.observed)

func _prepared(kind: String, payload, schema: Dictionary, observed: Dictionary) -> Dictionary:
	var resolved_kind := kind
	var resolved_schema: Dictionary = schema
	if resolved_kind.is_empty() or resolved_schema.is_empty():
		if defaults.is_empty():
			return _error(ERROR_INVALID_DEFAULTS, "no defaults configured; pass kind and schema explicitly")
		if resolved_kind.is_empty():
			resolved_kind = defaults.event_type
		if resolved_schema.is_empty():
			resolved_schema = defaults.schema
	var schema_check := _checked_schema(resolved_schema)
	if schema_check.has("error"):
		return schema_check
	var schema_value_check := _checked_value(resolved_schema, "schema")
	if schema_value_check.has("error"):
		return schema_value_check
	var resolved_payload = payload
	if resolved_payload == null:
		var source := _declared_source()
		if source.has("error"):
			return source
		resolved_payload = source.value
	if not (resolved_payload is Dictionary):
		return _error(ERROR_INVALID_PAYLOAD, "payload must be a Dictionary")
	var payload_check := _checked_value(resolved_payload, "payload")
	if payload_check.has("error"):
		return payload_check
	var observed_check := _checked_value(observed, "observed")
	if observed_check.has("error"):
		return observed_check
	return {"kind": resolved_kind, "schema": resolved_schema.duplicate(true),
			"payload": resolved_payload.duplicate(true), "observed": observed.duplicate(true)}

func _declared_source() -> Dictionary:
	if defaults.has("snapshot_provider"):
		var provider: Callable = defaults.snapshot_provider
		if not provider.is_valid():
			return _error(ERROR_INVALID_PROVIDER, "snapshot_provider is no longer a valid Callable")
		var produced = provider.call()
		if not (produced is Dictionary):
			return _error(ERROR_INVALID_PROVIDER, "snapshot_provider must return a Dictionary")
		if produced.has("error"):
			return _error(ERROR_PROVIDER_ERROR, str(produced.error))
		var provider_check := _checked_value(produced, "provider")
		if provider_check.has("error"):
			return provider_check
		return {"value": produced}
	if defaults.has("fields"):
		var built := {}
		for name in defaults.fields:
			var callable: Callable = defaults.fields[name]
			if not callable.is_valid():
				return _error(ERROR_INVALID_FIELD, "field " + str(name) + " is no longer a valid Callable")
			var value = callable.call()
			if value is Dictionary and value.has("error"):
				return _error(ERROR_FIELD_ERROR, "field " + str(name) + ": " + str(value.error))
			var field_check := _checked_value(value, "field " + str(name))
			if field_check.has("error"):
				return field_check
			built[name] = value
		return {"value": built}
	return _error(ERROR_INVALID_PAYLOAD, "no snapshot_provider or fields declared; pass payload explicitly")

func _checked_value(value, path: String) -> Dictionary:
	## Plain finite JSON only: dictionaries with String keys, arrays, Strings,
	## finite numbers, booleans and null. Nodes/objects, non-finite floats and
	## non-String keys are explicit errors instead of values that cannot be
	## stored faithfully. Cycles are detected against the current ancestor path
	## only (identity, not equality), so a shared but acyclic subtree is valid
	## instead of being misreported; the depth counter stops before the engine
	## recursion limit can fail silently. Error details carry the truncated path
	## and reason only, never the original values.
	return _checked_plain(value, path, [], 1)

func _checked_plain(value, path: String, ancestors: Array, depth: int) -> Dictionary:
	match typeof(value):
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_STRING:
			return {}
		TYPE_FLOAT:
			if not is_finite(value):
				return _error(ERROR_INVALID_VALUE, path + " is not a finite number")
			return {}
		TYPE_ARRAY, TYPE_DICTIONARY:
			if depth > MAX_VALUE_DEPTH:
				return _error(ERROR_INVALID_VALUE,
						path + " is deeper than MAX_VALUE_DEPTH=" + str(MAX_VALUE_DEPTH))
			for ancestor in ancestors:
				if is_same(ancestor, value):
					return _error(ERROR_INVALID_VALUE, path + " contains a cyclic reference")
			ancestors.append(value)
			var child_error := {}
			if typeof(value) == TYPE_ARRAY:
				for index in value.size():
					child_error = _checked_plain(value[index], path + "[" + str(index) + "]", ancestors, depth + 1)
					if child_error.has("error"):
						break
			else:
				for key in value:
					if not (key is String):
						child_error = _error(ERROR_INVALID_VALUE, path + " has a non-String key")
						break
					child_error = _checked_plain(value[key], path + "." + _key_text(key), ancestors, depth + 1)
					if child_error.has("error"):
						break
			ancestors.pop_back()
			return child_error
		_:
			return _error(ERROR_INVALID_VALUE, path + " is not a plain JSON value")

func _key_text(key) -> String:
	var text := str(key)
	if text.length() > _KEY_LIMIT:
		return text.substr(0, _KEY_LIMIT - 3) + "..."
	return text

func _error(code: String, detail: String = "") -> Dictionary:
	var result := {"error": code}
	if not detail.is_empty():
		result.detail = detail if detail.length() <= _DETAIL_LIMIT else detail.substr(0, _DETAIL_LIMIT - 3) + "..."
	return result

func prepare(options: Dictionary = {}) -> Dictionary:
	return await backend.prepare(options)
func commit(checkpoint: Dictionary = {}) -> Dictionary:
	return await backend.commit(checkpoint)
func status() -> Dictionary:
	return backend.status()
func finish() -> Dictionary:
	return await backend.finish()

func recovery_export() -> Dictionary:
	return await backend.recovery_export()
