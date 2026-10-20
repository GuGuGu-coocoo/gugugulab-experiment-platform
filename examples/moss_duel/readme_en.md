# Moss Duel — standalone game

English · [中文](README.md)

A standalone 2D boss-fight game with light attacks, charged heavy attacks, dashes and parries. This project has **no GEC, server configuration or account requirement**. Graphics are drawn by game code; sound effects are Kenney CC0 assets, listed in [third-party notices](THIRD_PARTY_NOTICES.md).

## Run

Import this folder's `project.godot` in **Godot 4.7.2 Standard**, wait for import to finish, then run the project. Choose **Enter the arena** or press Enter. You can start another round after a win or defeat, without GEP or a network connection.

| Action | Input |
|---|---|
| Move | A / D or left / right arrows |
| Jump | Space, W or up arrow |
| Dash | E |
| Light attack | J or left mouse button |
| Charged heavy attack | Hold K or right mouse button, then release |
| Parry | L or Shift |
| Pause | Esc or Pause button |
| Toggle sound | Sound button |

Gold telegraphs can be parried near impact; red ground attacks cannot. Jump or move out of their range. The boss enters a faster second phase at half health. These are game rules, not a validated research timing protocol.

## Add GEC yourself

Start with this project, make a separate copy and follow the [manual integration guide](../../docs/moss_duel_gec_en.md). Download the generic GEC package separately and create the entry scene and wiring yourself. The original game scripts and assets stay unchanged.

The [prewired version](../moss_duel_gec/readme_en.md) is a separate project for comparison or trying GEP directly; it is not the starting point for a from-scratch exercise.
