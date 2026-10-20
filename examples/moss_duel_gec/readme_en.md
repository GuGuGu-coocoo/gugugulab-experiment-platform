# Moss Duel — GEC connected

English · [中文](README.md)

This project is already connected to GEC. Use it to try uploading, publishing and collecting data, or compare your own wiring. For the from-scratch exercise, copy the [clean standalone game](../moss_duel/readme_en.md) and follow the [manual guide](../../docs/moss_duel_gec_en.md).

## What is connected

- `gec_entry.tscn` is the main scene. GEC entry appears before the game, which starts only after admission.
- `gec_entry.gd` uses the generic `GECSession`, connects existing game signals, records `game.duel` events and returns control to GEC at the end.
- `gec_descriptor.json` uses program version `moss-duel-3`. Web, macOS and Windows export presets include the required bridge and exclusions.
- `scripts/`, `assets/` and `main.tscn` match the standalone project. Combat logic has not been rewritten for GEC.

There is no fixed server, study binding, workbench password or roster credential in the project. Starting without connection configuration is refused as expected; do not hard-code an address to bypass it.

## Open and install dependencies

Use **Godot 4.7.2 Standard** and matching export templates. A complete `moss_duel_gec.zip` prepared by the maintainer includes SQLite. Repository checkouts do not version native binaries: copy `addons/godot-sqlite/bin/` from the [generic GEC package](../../docs/gec_godot_en.md#1-get-the-generic-dependency) into the same location here first. Wiring, GEC source and the remaining files are already included.

Import this folder's `project.godot`. The Web bridge and packaging scripts are configured; do not replace these presets with the standalone ones.

## Run the Web version through GEP

1. Export the **Web** preset to `exports/web/index.html`, with debug export disabled.
2. Run `package_web.gd` in the script editor and select that `index.html`. Use the ZIP path shown on success.
3. Upload the ZIP in GEP's **Upload & publish experiment**, then choose **Preview (no data upload)**. GEC appears first; starting it opens the game. Preview saves locally only.
4. Publish, select the current version in **Recruitment & portal**, and open new participation. Play through the participant entry, then check completion and exported data.

GEP selects the participation mode; the game is not rewritten for anonymous, ID or ID/password entry. For native builds, follow the [generic export guide](../../docs/gec_godot_en.md#native), publish in GEP and download the complete package. An original EXE or `.app` alone has no automatic connection configuration.

The example does not resume a fight or change old sessions and pending data. See the [standalone README](../moss_duel/readme_en.md) for controls. Events are for synthetic integration practice, not validated scientific timing.
