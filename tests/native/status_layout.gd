extends SceneTree
func _initialize() -> void:
	call_deferred("verify_layout")
func verify_layout() -> void:
	var scene = load("res://main.tscn").instantiate()
	root.add_child(scene)
	await process_frame
	await process_frame
	var expected = scene.start.get_global_rect()
	var fields = [scene.code,scene.password,scene.recovery,scene.permit]
	var positions: Array = []
	for field in fields: positions.append(field.get_global_rect())
	for status in ["Trial 2: press LEFT or RIGHT","Cannot start: http_400","Task finished. Data state: remote_acknowledged"]:
		scene.message.text = status
		await process_frame
		await process_frame
		assert(scene.start.get_global_rect() == expected,"Status text shifted Start below stale Web inputs")
		for i in range(fields.size()): assert(fields[i].get_global_rect() == positions[i])
		assert(scene.permit.get_global_rect().end.y <= expected.position.y)
	print("STATUS_LAYOUT_VERIFIED")
	quit()
