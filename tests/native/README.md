# Native diagnostic harnesses

These scripts run with the pinned Godot editor executable and the synthetic project's real scene or SQLite backend. Release templates do not execute arbitrary `--script` overrides.

`input_probe.gd` observes the recovery fields without reporting their contents. Supply a private JSON fixture containing `session_id` and `permit` through `GEP_INPUT_PROBE_FIXTURE`, a report path through `GEP_INPUT_PROBE_REPORT`, and a dedicated synthetic queue through `GEP_SYNTHETIC_STORAGE`. Start Godot with `--path examples/synthetic_experiment --script <absolute path to input_probe.gd> -- --config=<absolute synthetic connection.json path>`.

Use real UI input; the probe never fills a field. It reports lengths, mismatch positions and equality flags, including whether both fields matched before submission cleared the permit. Keep fixtures and reports outside Git. Obtain a fresh permit through the study's authorized recovery workflow. Debug-scene evidence must be followed by the same GUI workflow in the exported executable.

On macOS, the tested automation tool's text injection dropped an underscore. Its paste call reported a timeout despite successfully delivering the complete text. Verify field equality or the application's resulting state before retrying: a tool timeout alone is not proof of failed input. Do not relax authentication or replace GUI acceptance with command-line recovery arguments.
