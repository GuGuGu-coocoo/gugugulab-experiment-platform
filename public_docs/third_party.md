# Third-party dependencies

Original GEP and GEC project code is licensed under [Apache-2.0](../LICENSE). Third-party dependencies and materials retain their respective licenses.

Python dependencies are resolved from PyPI and pinned with distribution hashes in `uv.lock`. Direct dependencies: Django (BSD-3-Clause), jsonschema (MIT), Gunicorn (MIT). Development dependencies: pytest (MIT), pytest-django (BSD-3-Clause). Installed distributions retain their license metadata and license files. Redistributing bundled dependencies must retain those notices.

Godot 4.7.2 is the locally installed official engine (MIT); export templates must match the engine. Godot runtime notices must accompany exported distributions. No Godot binary is committed here.

Official references: [Django sessions](https://docs.djangoproject.com/en/5.2/topics/http/sessions/), [Godot JavaScriptBridge](https://docs.godotengine.org/en/stable/classes/class_javascriptbridge.html).

Native SQLite: [godot-sqlite v4.7](https://github.com/2shady4u/godot-sqlite/releases/tag/v4.7), SQLite 3.51.0, upstream MIT notice in `third_party/godot_sqlite_license.md`. The installer checks the official demo archive SHA-256 `26966044757cf86a223a8027f8bc88c49c289ab047dcf8138bb591d7632e580e` before selecting macOS frameworks. Native binaries are installed locally, not committed. SQLite itself is in the public domain.

Playwright 1.58.2 is a development dependency under Apache-2.0. It controls the locally installed Google Chrome for engineering tests; it does not establish independent human acceptance.

Container acceptance tooling (not bundled): [Lima 2.2.0](https://github.com/lima-vm/lima/releases/tag/v2.2.0), Apache-2.0, official Darwin arm64 archive SHA-256 `bbdef91774885a0d05f7b048c4eb89ae2bcf3a0c252ae7ca7934e63df76d93c3`. Docker Engine 29.1.3 and Compose 2.40.3 were installed from Ubuntu signed repositories in an isolated VM; their projects use Apache-2.0 and retain dependency notices. The Python container and Ubuntu guest include separately licensed system packages; these images are not committed or redistributed by this repository.

Windows acceptance-only dependency: [websocket-client 1.8.0](https://pypi.org/project/websocket-client/1.8.0/), Apache-2.0. Its pure-Python wheel is pinned by SHA-256 in tools/windows_browser_requirements.txt and installed only in the selected test directory. It and Chrome are not bundled into project releases.
