# Third-party dependencies

Original GEP and GEC project code is licensed under [Apache-2.0](../LICENSE). Third-party dependencies and materials retain their respective licenses.

Python dependencies are resolved from PyPI and pinned with distribution hashes in `uv.lock`. Direct dependencies: Django (BSD-3-Clause), jsonschema (MIT), Gunicorn (MIT). Development dependencies: pytest (MIT), pytest-django (BSD-3-Clause). Installed distributions retain their license metadata and license files. Redistributing bundled dependencies must retain those notices.

Godot 4.7.2 is the locally installed official engine (MIT); export templates must match the engine. Godot runtime notices must accompany exported distributions. No Godot binary is committed here.

Official references: [Django sessions](https://docs.djangoproject.com/en/5.2/topics/http/sessions/), [Godot JavaScriptBridge](https://docs.godotengine.org/en/stable/classes/class_javascriptbridge.html).

Native SQLite: [godot-sqlite v4.7](https://github.com/2shady4u/godot-sqlite/releases/tag/v4.7), SQLite 3.51.0, upstream MIT notice in `third_party/godot_sqlite_license.md`. The installer checks the official demo archive SHA-256 `26966044757cf86a223a8027f8bc88c49c289ab047dcf8138bb591d7632e580e` before selecting macOS frameworks. Native binaries are installed locally, not committed. SQLite itself is in the public domain.

Playwright 1.58.2 is a development dependency under Apache-2.0. It controls the locally installed Google Chrome for engineering tests; it does not establish independent human acceptance.
