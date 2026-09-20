# Export the official engine's own notice documents for the frozen third-party
# notice source. Run headless with GEP_NOTICE_OUTPUT pointing at the target file:
#   godot --headless --script tests/native/engine_notices_export.gd
# Engine.get_license_text / get_copyright_info / get_license_info are the
# engine's authoritative license interface; the platform freezes exactly this
# output and the complete-package tests compare the committed source against a
# fresh export, so the notices can never drift from the shipped engine.
extends SceneTree
func _initialize() -> void:
	var result = {"engine_version":Engine.get_version_info(),"engine_license":Engine.get_license_text(),"third_party_copyright":Engine.get_copyright_info(),"third_party_licenses":Engine.get_license_info()}
	var f = FileAccess.open(OS.get_environment("GEP_NOTICE_OUTPUT"),FileAccess.WRITE)
	assert(f != null);f.store_string(JSON.stringify(result,"  "));f.close();quit()
