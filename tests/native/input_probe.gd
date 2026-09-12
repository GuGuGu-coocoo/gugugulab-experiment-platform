extends SceneTree
## Explicit test harness: observes setup fields without exporting credentials.
var scene
var expected: Dictionary
var output_path: String
var last_report := ""
var matched_once := false
func _initialize() -> void:
	var fixture = OS.get_environment("GEP_INPUT_PROBE_FIXTURE")
	assert(not fixture.is_empty())
	expected = JSON.parse_string(FileAccess.get_file_as_string(fixture))
	output_path = OS.get_environment("GEP_INPUT_PROBE_REPORT")
	assert(not output_path.is_empty())
	scene = load("res://main.tscn").instantiate()
	root.add_child.call_deferred(scene)
func _process(_delta: float) -> bool:
	if not is_instance_valid(scene) or scene.permit == null: return false
	var actual: String = scene.permit.text
	matched_once = matched_once or (scene.recovery.text == expected.session_id and actual == expected.permit)
	var wrong_positions: Array = []
	for i in range(mini(actual.length(),str(expected.permit).length())):
		if actual[i] != expected.permit[i]: wrong_positions.append(i)
	var report = {
		"session_matches": scene.recovery.text == expected.session_id,
		"session_length": scene.recovery.text.length(),
		"permit_matches": actual == expected.permit,
		"permit_length": actual.length(),
		"expected_length": str(expected.permit).length(),
		"mismatch_positions": wrong_positions,
		"matches_without_underscores": actual == str(expected.permit).replace("_", ""),
		"matches_lowercase": actual == str(expected.permit).to_lower(),
		"both_fields_matched_once": matched_once,
		"status": scene.message.text,
	}
	var encoded = JSON.stringify(report)
	if encoded != last_report:
		var file = FileAccess.open(output_path,FileAccess.WRITE)
		if file != null: file.store_string(encoded);file.close()
		last_report = encoded
	return false
