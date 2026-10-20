# Add GEC to the standalone Moss Duel game

English · [中文](moss_duel_gec.md)

There are two projects: the [clean standalone game](../examples/moss_duel/readme_en.md) and the [GEC-connected reference](../examples/moss_duel_gec/readme_en.md). This exercise starts with the **clean game**. Use the connected project to compare your work, not as the starting point.

Work on a copy and use synthetic data only. The game keeps responsibility for movement, combat, damage and outcomes; GEC handles participation entry, local saving, upload and completion.

## 1. Run the clean game

1. Open `examples/moss_duel/`, or extract the maintainer's `moss_duel_standalone.zip`.
2. Import its `project.godot` in **Godot 4.7.2 Standard** and run it.
3. Choose **Enter the arena**. J is a light attack; hold and release K for a heavy attack; L/Shift parries; E dashes; Space jumps; Esc pauses. The game can run repeated rounds without GEP or a network connection.
4. Close the project and copy the folder to a separate location, named `moss_duel_gec`. Make all following changes in that copy.

## 2. Install the generic GEC dependency

Extract `gec_godot_0_1_0.zip` and copy the dependencies and licences listed in the [generic installation guide](gec_godot_en.md#2-install-into-a-project-copy). It has no Moss Duel scene, wiring or export presets. Do not copy the prewired reference's entry script yet.

The copy now contains the original `main.tscn`, `scripts/` and `assets/`, plus `addons/`, `gec_web/`, `package_web.gd` and `package_native.gd`. Running it still starts the original game: installing files alone has not connected it.

## 3. Create the entry scene

1. Choose **Scene → New Scene**, create a **Node** root and name it **GECEntry**.
2. Drag `main.tscn` onto the root as a child scene. Name the child **Game**, with that capitalization. Do not edit the internals of the original scene.
3. Save the new scene as `gec_entry.tscn` in the project root.
4. Attach a GDScript to GECEntry, disable the script template, and save it as `res://gec_entry.gd`.
5. Replace the new script with the following. Original game scripts stay unchanged.

```gdscript
extends Node
const GEC = preload("res://addons/gec/session.gd")
var gec
var record_failed := false
@onready var game = $Game

func _enter_tree() -> void:
	$Game.set_host_controlled(true)
	$Game.process_mode = Node.PROCESS_MODE_DISABLED

func _ready() -> void:
	gec = GEC.new()
	add_child(gec)
	gec.configure_defaults({"event_type": "game.duel", "schema": {"id": "duel_event", "version": "1"}})
	game.telemetry_event.connect(_record)
	game.run_finished.connect(_finished)
	gec.prepare("moss-duel-3", game, _start, _retire)

func _start() -> void:
	game.process_mode = Node.PROCESS_MODE_INHERIT
	game.show()
	game.start_round()

func _record(kind: String, payload: Dictionary) -> void:
	if record_failed:
		return
	var result: Dictionary = gec.record({"kind": kind, "data": payload})
	if result.has("error"):
		record_failed = true
		game.process_mode = Node.PROCESS_MODE_DISABLED
		gec.shell.set_overlay_visible(true)
		gec.shell.announce("Game event rejected: " + str(result.error))

func _finished(summary: Dictionary) -> void:
	_record("round_summary", summary)
	if not record_failed:
		gec.finish()

func _retire() -> void:
	game.set_host_controlled(true)
	game.hide()
```

6. Right-click `gec_entry.tscn` and choose **Set As Main Scene**.

The wiring uses the game's existing signals and start method. `_retire()` retires the HUD as well as the world: a CanvasLayer does not inherit its parent's visibility. GEC defaults to Chinese and can switch to English.

Without connection configuration, editor play cannot start remote participation. Do not hard-code a server address; use GEP preview and publication below.

## 4. Create the descriptor

Create `gec_descriptor.json` in the project root, without an extra `.txt` suffix. Its version must match `prepare()`.

```json
{
  "version": "moss-duel-3",
  "platform": "godot_web",
  "host_version": "4.7.2",
  "sdk_version": "0.1.0",
  "protocol_version": "gep/1",
  "program_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "schemas": {
    "game.duel": {
      "id": "duel_event",
      "version": "1",
      "schema": {
        "type": "object",
        "properties": {
          "kind": {"type": "string", "minLength": 1},
          "data": {"type": "object"}
        },
        "required": ["kind", "data"],
        "additionalProperties": false
      }
    }
  },
  "codebook": {
    "purpose": "Synthetic game integration exercise.",
    "kind": "Explicit game signal name; round_summary is the game's end summary.",
    "data": "Unchanged explicit game payload; no implicit scene or credential collection.",
    "elapsed_ms": {"unit": "ms", "source": "game-owned clock", "meaning": "Game elapsed time, not a validated reaction time."},
    "recovery": "Saved-data upload only; no battle checkpoint or replay."
  }
}
```

The packaging helper replaces the zero digest with one calculated from the actual export. Do not guess a digest. This is a program descriptor, not a connection file or a place for credentials.

## 5. Export Web and run through GEP

1. Update the copy's existing **Web** preset using the [generic Web guide](gec_godot_en.md#web): append the HTML bridge and resource exclusions, keeping its other settings.
2. Export to `exports/web/index.html`, with debug export disabled. Run `package_web.gd` in the script editor and select that file.
3. Use the ZIP path in the success dialog. Existing packages are not overwritten; a repeated name receives a suffix.
4. Create a synthetic study in GEP and choose anonymous participation in **Participation & roster**.
5. Upload the ZIP in **Upload & publish experiment**. Choose **Preview (no data upload)**. GEC appears first; choose Start to enter the game. Play to a natural win or defeat and check local saving. Preview does not upload into live participation records.
6. Choose **Publish version**, select it as the current version in **Recruitment & portal**, and choose **Open new participation**. A directly shared study entry can remain private.
7. Open that participant entry, play a round and wait for server completion.
8. Download the overview/detail ZIP and the same snapshot's JSONL. Events have `event_type: game.duel`; their action is `record.payload.kind`, with one final `round_summary`. The total number depends on play.

For ID or ID/password participation, configure another synthetic study and roster. The same game does not need different entry code for each mode.

## 6. Native export

1. Use the [native export instructions](gec_godot_en.md#native). Retain SQLite; Windows uses x86_64 and a separate PCK.
2. Export macOS as `exports/native/moss_duel.zip`, or Windows to `exports/windows/Moss Duel/Moss Duel.exe`.
3. Run `package_native.gd` in the script editor and select the exported ZIP or EXE.
4. In a new synthetic study, register the full companion descriptor JSON and upload its matching program ZIP.
5. Publish it, select the current version, open participation and download the **complete experiment package**.
6. Extract that package and run its app or EXE. Use its frozen `connection.json`, not an original export without configuration.
7. Play a round and compare completion and JSONL. Cross-exporting is not target-system acceptance; actual recorded environments and remaining human checks are in the [verification record](verification.md).

Use an approved secure connection or a controlled loopback tunnel for another device. Do not change global certificate trust or hard-code ordinary LAN HTTP.

## 7. Saving and interrupted runs

Initial admission needs a connection. An admitted game can continue offline; committed events remain local for later upload. `record()` first queues a snapshot in memory, while saving runs asynchronously. Uncommitted records are not guaranteed after a forced close.

On a local-save failure, keep the process open and use the retry control. Retrying reuses the buffered event identity instead of creating another copy.

This game declares **no battle checkpoint**. It does not restore health, replay a fight or attach a new round to an old session. GEC 0.1.0 does not expose recovery-code or permit controls. Retain pending data and distinguish task completion, local saving and server receipt. A new round starts from a new participation entry.

Automated checks of this recipe do not mean a researcher has manually completed it. Record human help, failures and results separately.
