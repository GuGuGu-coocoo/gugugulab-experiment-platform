extends SceneTree
func _initialize() -> void:
	call_deferred("run")
func run() -> void:
	var request = HTTPRequest.new();request.max_redirects = 0;request.timeout = 15.0;request.use_threads = true;root.add_child(request)
	var error = request.request("https://api.github.com/repos/godotengine/godot",PackedStringArray(["User-Agent: GEP-synthetic-verification"]),HTTPClient.METHOD_GET)
	if error != OK: print("HTTPS_START_FAILED");quit(2);return
	var result = await request.request_completed
	if result[0] != HTTPRequest.RESULT_SUCCESS or result[1] != 200:
		print("HTTPS_FAILED ",result[0]," ",result[1]);quit(3);return
	var data = JSON.parse_string(result[3].get_string_from_utf8())
	if not data is Dictionary or data.get("full_name") != "godotengine/godot": quit(4);return
	print("HTTPS_SYSTEM_TRUST_VERIFIED");quit()
