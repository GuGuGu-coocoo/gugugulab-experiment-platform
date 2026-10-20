extends SceneTree
## Real shell UI with controlled queue setup failures. No database or remote
## admission is involved; storage/API evidence is collected separately.
const Entry = preload("res://../moss_duel_legacy_adapter/gec_entry.gd")
const Shell = preload("res://addons/gec/shell.gd")
var checks := 0
var failures := 0

class SetupBackend extends Node:
	var config := {"locale": "zh", "mode": "anonymous"}
	var failure := ""
	func initialize_queue() -> Dictionary:
		return {"state": "ready"} if failure.is_empty() else {"error": failure}
	func recover_code(_value: String) -> Dictionary:
		return {"error": "fixture_not_used"}

func _initialize() -> void:
	call_deferred("run_checks")

func check(condition: bool, name: String) -> void:
	checks += 1
	if not condition:
		failures += 1
		push_error("MOSS_DUEL_LANGUAGE_FAIL " + name)

func run_checks() -> void:
	for failure in ["unsupported_configuration", "writer_busy", "storage_open", ""]:
		var backend := SetupBackend.new()
		backend.failure = failure
		if failure == "unsupported_configuration":
			backend.config.mode = "unknown"
		var frozen := JSON.stringify(backend.config)
		root.add_child(backend)
		var shell = Shell.new()
		root.add_child(shell)
		var prepared: Dictionary = shell.setup(backend)
		check(shell.locale == "zh", "fixture starts in Chinese: " + failure)
		var state: String = shell.state
		var disabled: bool = shell.start.disabled
		var entry = Entry.new()
		entry.shell = shell
		entry._apply_native_language(prepared)
		check(shell.state == state and shell.start.disabled == disabled, "state and disabled controls preserved: " + failure)
		var expected: String = shell.t("status")
		if failure == "unsupported_configuration":
			expected = shell.t("unsupported")
			check(shell.state == "unsupported" and shell.start.disabled, "unsupported never becomes usable")
		else:
			if not failure.is_empty():
				expected = shell.t("error_prefix", {"code": failure})
			check(shell.state == ("ready" if failure.is_empty() else "error"), "queue failure retains its error state")
		check(shell.locale == "en" and shell.last_announcement == expected and shell.message.text == expected,
			"actual announcement remains readable and truthful: " + failure)
		check(shell.recover_button == null and shell.permit_button == null,
			"recovery controls remain unexposed in the current UI: " + failure)
		check(JSON.stringify(backend.config) == frozen and JSON.stringify(shell.config) == frozen,
			"frozen backend and shell config preserved: " + failure)
		entry.free()
		shell.queue_free()
		backend.queue_free()
		await process_frame
	print("MOSS_DUEL_LANGUAGE_RESULT ", JSON.stringify({"checks": checks, "failures": failures}))
	quit(0 if failures == 0 else 1)
