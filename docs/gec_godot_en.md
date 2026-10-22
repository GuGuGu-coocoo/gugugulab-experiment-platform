# GEC developer guide

English · [中文](gec_godot.md)

GEC is an integration dependency, not an experiment runner. Your Godot project controls stimuli, input, randomization, scoring and event meaning. GEC handles admission, local transactions, upload and the completion screen. Researchers select anonymous, ID or ID/password participation in GEP; you do not build a different login screen for each mode.

This guide targets GEC 0.1.0, protocol `gep/1` and Godot 4.7.2 standard. Web uses Compatibility, single-threaded export and Chrome. Native dependencies cover macOS universal and Windows x64; actual macOS runtime checks used arm64. Other browsers, ARM Windows and native Linux experiments are outside the current validation scope.

## 1. Get the generic dependency

The GEP server release is Docker-only, not a Godot plugin installer. The repository's [GEC_SDK folder](../GEC_SDK/) contains the Godot integration code and Web SDK. If a maintainer supplied `gec_godot_0_1_0.zip`, continue with installation. A generic package must not include a particular game, account or fixed study connection.

To build it yourself, download and extract the [current source](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/archive/refs/heads/main.zip). On your development computer, install Python 3.12 and download the original official [godot-sqlite v4.7 demo.zip](https://github.com/2shady4u/godot-sqlite/releases/download/v4.7/demo.zip). From the source directory:

```sh
python3 tools/build_gec_addon.py --output build/gec_download \
  --sqlite-archive /path/to/demo.zip
```

Use the actual downloaded path; on Windows, `py -3.12` can replace `python3`. This standard-library tool checks the dependency and creates `build/gec_download/gec_godot_0_1_0.zip`. The output directory must be new. Python here is a developer packaging tool, not a GEP server requirement.

## 2. Install into a project copy

Copy the original project and confirm that it runs before integrating. Place these package contents beside `project.godot`:

- `addons/` and `gec_web/`.
- `package_web.gd` and `package_native.gd`.
- `SQLITE_LICENSE.md`, `SQLITE_NOTICE.txt`, `GEC_LICENSE.txt`, `GEC_GODOT_NOTICES.txt` and `GEC_THIRD_PARTY_NOTICES.txt`.

Review existing dependencies before copying over matching names. Do not replace the project's `LICENSE`, main scene or `export_presets.cfg`. The package README and example descriptor are reference material. After Godot imports the project, check for `addons/gec/session.gd`, SQLite native dependencies and the four `gec_web/*.js` files.

## 3. Connect the task

Create an entry scene with the original task as a child, for example `Task`. The task must wait for an explicit start call. Disabling process alone cannot prevent one-off logic in `_ready()`; add a host-controlled start mode if necessary without changing the scientific task.

The following example assumes **your task** implements `start_task()` and `retire_task()`. These are not GEC methods. Connect the example event callbacks to the task's own signals:

```gdscript
extends Node

const GECSession = preload("res://addons/gec/session.gd")
var gec = GECSession.new()
@onready var task = $Task

func _ready() -> void:
    add_child(gec)
    var configured = gec.configure_defaults({
        "event_type": "task.response",
        "schema": {"id": "response", "version": "1"}
    })
    if configured.has("error"):
        push_error(str(configured))
        return
    gec.failed.connect(_on_gec_failed)
    var prepared = gec.prepare("my-task-1", task, _start_task, _retire_task)
    if prepared.has("error"):
        push_error(str(prepared))

func _start_task() -> void:
    task.start_task()

func _retire_task() -> void:
    task.retire_task()

func on_trial_response(trial_id: String, choice: String, rt_ms: float) -> void:
    var result = gec.record({"trial_id": trial_id, "choice": choice, "rt_ms": rt_ms})
    if result.has("error"):
        _on_gec_failed(result)

func on_task_finished() -> void:
    var result = gec.finish()
    if result.has("error"):
        _on_gec_failed(result)

func _on_gec_failed(result: Dictionary) -> void:
    task.process_mode = Node.PROCESS_MODE_DISABLED
    push_error(str(result))
```

After admission, GEC hides the entry form and calls `_start_task`. At the end, use `_retire_task` to retire your own displays and input, including CanvasLayers that do not inherit ordinary node visibility. GEC then displays completion and upload state. See the [Moss Duel walkthrough](moss_duel_gec_en.md) for a complete integration example.

`record()` copies input synchronously and queues it. `queued` is not a durable-save acknowledgement. The helper serializes later recording and commits. `local_saved` signals a local commit; `completed` signals a local completion declaration, not server receipt. Handle errors and pause as appropriate: an error return is not a saved record, and logging an error alone is not a reason to continue collecting unchecked data.

## 4. Describe events and program version

Create `gec_descriptor.json` in the project root. Its version must match `prepare()`. This descriptor matches the example above:

```json
{
  "version": "my-task-1",
  "platform": "godot_web",
  "host_version": "4.7.2",
  "sdk_version": "0.1.0",
  "protocol_version": "gep/1",
  "program_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "schemas": {
    "task.response": {
      "id": "response",
      "version": "1",
      "schema": {
        "type": "object",
        "properties": {
          "trial_id": {"type": "string"},
          "choice": {"type": "string"},
          "rt_ms": {"type": "number", "minimum": 0}
        },
        "required": ["trial_id", "choice", "rt_ms"],
        "additionalProperties": false
      }
    }
  },
  "codebook": {
    "trial_id": {"description": "Trial identifier supplied by the task"},
    "choice": {"description": "Response value supplied by the task"},
    "rt_ms": {"unit": "ms", "source": "Task-owned response clock"}
  }
}
```

The zero digest is a pre-packaging placeholder. The packaging helper computes it from the actual export. Use a codebook that reflects your task's sources, units and fields; this example does not establish a scientific response-time method.

The descriptor is not connection configuration. Never include Owner passwords, roster passwords or participant tokens. GEP injects Web configuration; a native complete release freezes public `connection.json` beside the program. The existing protocol field `purpose: synthetic` remains unchanged for compatibility. Removing preview wording from the interface does not mean changing this field or rewriting old pending queues.

The audit branch temporarily rejects Schema `pattern` and `patternProperties` rules, including nested definitions. Use types, enums, lengths and numeric bounds where appropriate. Existing releases with these rules also refuse new admission and affected events with `schema_regex_unsupported`, rather than silently bypassing validation. Preserve pending data under its original binding; do not delete or relabel it. An ordinary field named `pattern` remains legal. See the [protocol compatibility limits](protocol.md).

## 5. Export and package

The security-audit branch accepts Stored/Deflate program ZIPs only. Keep using the packaging helpers below; do not switch to BZIP2/LZMA. See the [audit status (Chinese)](SECURITY_AUDIT.md); the released image has not been updated automatically.

Use export templates matching Godot 4.7.2. Adjust the project's own presets; do not replace them with another project's complete preset file.

### Web

1. Choose Compatibility; disable threads and Web GDExtension support. Export `index.html` into its own directory.
2. Append the bridge to HTML → Head Include, retaining existing content:

   ```html
   <script type="module" src="gec/bridge.js"></script>
   ```

3. Retain the resource exclusion filter and append:

   ```text
   addons/godot-sqlite/*,connection.json,gec_descriptor*.json,package_web.gd,package_native.gd,gec_web/*,exports/*
   ```

4. Export the project without debug export. Open `package_web.gd` in the script editor and choose File → Run (Ctrl+Shift+X on Windows, ⌘+Shift+X on macOS). Select the exported `index.html`.
5. Use the ZIP path in the success dialog. It contains the standard package layout and SDK, not the whole project. Existing ZIPs are retained; use the newly reported path. A failed packaging operation does not provide a usable new package.

Upload to GEP, validate and preview. Preview saves locally; publish and use the participation entry to exercise actual upload.

### Native

1. Append this exclusion filter, **without excluding SQLite**:

   ```text
   connection.json,gec_descriptor*.json,package_web.gd,package_native.gd,gec_web/*,exports/*
   ```

2. For macOS, export a complete `.app` ZIP. For Windows, choose x86_64, disable Embed PCK and export EXE/PCK into a separate directory with native dependencies intact.
3. Run `package_native.gd` in the script editor and select the macOS ZIP or Windows EXE. Use its program ZIP and matching `_descriptor.json`.
4. Register the descriptor, upload and publish in GEP, then download the complete release. Extract and run it without separately replacing the EXE or `connection.json`.

Native releases do not have a Web preview: run them on the target system. This guide does not provide bypasses for unsigned or unnotarized program warnings. Arrange signing for your distribution target.

## 6. Check the real data path

Use synthetic data to create a study, configure participation and roster, upload, preview and publish. Select the current version, open recruitment and run a task with known values from the **participation entry**. Check completion, create an export and compare JSONL events and values. Preview alone is not this round trip.

The three participation modes come from frozen release configuration, not task-code changes. First admission needs a connection. After admission, an outage saves locally and a later connection can upload. A closed page/process is not guaranteed to upload in the background. Reading records into memory is not a commit. Do not clear pending data or rebind it to another study or release.

The helper supports saved-data upload without inferring task state. Recovery-code and permit controls are not exposed in 0.1.0. For task checkpoints, first define complete trial boundaries, random state and scientific order, then use the [low-level protocol](protocol.md). Do not call low-level commit/checkpoint concurrently with a nonempty helper queue.

## 7. Before publication

- Keep program version, descriptor version, event type and schema ID consistent.
- Use actual GEP configuration; native participants receive a complete frozen package without admin secrets.
- Surface save/upload failures and never claim server receipt with missing data.
- Preserve task timing, randomization, scoring and exclusion rules.
- Run and reconcile on the actual target system; cross-exporting is not runtime validation.

Server installation is in the [deployment guide](deployment_en.md); study and publication operations are in the [user guide](researcher_en.md).
