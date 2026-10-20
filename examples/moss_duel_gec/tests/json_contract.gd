extends RefCounted
## Test-only check of the game's transport-independent JSON signal contract.


static func problem(value: Variant, path: String = "$") -> String:
	match typeof(value):
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_STRING:
			return ""
		TYPE_FLOAT:
			return "" if is_finite(value) else path + ": non-finite number"
		TYPE_ARRAY:
			for index in range(value.size()):
				var nested: String = problem(value[index], path + "[%d]" % index)
				if not nested.is_empty():
					return nested
			return ""
		TYPE_DICTIONARY:
			for key in value:
				if typeof(key) != TYPE_STRING:
					return path + ": object key must be String, got " + type_string(typeof(key))
				var nested: String = problem(value[key], path + "." + key)
				if not nested.is_empty():
					return nested
			return ""
	return path + ": unsupported JSON value " + type_string(typeof(value))
