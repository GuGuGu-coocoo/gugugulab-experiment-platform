# Moss Duel examples

English · [中文](readme_zh.md)

The same game is provided in two forms:

On GitHub, choose **Code → Download ZIP**, extract it and open the project folder listed below. Maintainers can also produce separate complete ZIPs using `tools/moss_duel_examples.py`; see the [packaging notes](../docs/maintainer.md).

| Project | What it contains | Use it to |
|---|---|---|
| [moss_duel](moss_duel/readme_en.md) | A standalone game with no GEC, server settings or account requirement | Play the game, then practise adding GEC yourself |
| [moss_duel_gec](moss_duel_gec/readme_en.md) | The same game with GEC entry, event recording, completion UI and export presets already connected | Try the GEP workflow or compare your own integration |

For the from-scratch exercise, start with **moss_duel**, make a copy, then follow the [manual integration guide](../docs/moss_duel_gec_en.md). Do not start with the prewired project and treat it as a clean game. The generic GEC package remains independent of both games.

Both use Godot 4.7.2. The prewired project has no fixed server, study or account credentials; Web configuration comes from GEP. See each project's README for dependencies and packaging. Older synthetic hosts and the old adapter are regression fixtures under `tests/fixtures/`, not additional reader examples.
