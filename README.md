# GEP — GuguguLab Experiment Platform

English · [中文](README_ZH.md)

GEP is a self-hosted platform for publishing experiments, managing participation and collecting data. GEC connects a Godot experiment to GEP: it handles entry, local saving, upload and the completion screen, while your project controls the task itself.

## Download and deploy

[Download GEP 0.1.0](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/releases/tag/v0.1.0). The release contains a Linux **amd64 Docker image**, Compose files and launchers for Linux and Windows. You do not need Python, Node or Godot on the server. ARM servers need a separate build; this download is not an ARM image.

- **Windows:** install Docker Desktop, start its Linux container engine, extract the release and double-click `deploy/start_windows.cmd`.
- **Linux or a VPS:** install Docker Engine and Compose, extract the release and run `sudo sh deploy/linux.sh start`.

Open `http://admin.localhost:8080/login` on the deployment computer. The first-run page asks you to choose the Owner username and password. Leave the optional domain blank for local use. A remote server is initialized through an SSH tunnel before public access is enabled.

The [deployment guide](docs/deployment_en.md) starts with a new server and covers Docker installation, first setup, domains, HTTPS, upgrades and backups. Entering a domain in the wizard saves configuration; it does not change DNS or open the server to the internet.

## What GEP provides

- A study dashboard and separate participant portal.
- Anonymous, ID-only and ID-with-password participation; spreadsheet roster imports.
- Web experiment upload, isolated preview, publication and one current version per study.
- Native experiment packages for supported macOS and Windows targets.
- Participation records, completion filters and permission-checked data exports.
- Owner, Admin and member accounts, with per-study visibility and permissions.

GEP and GEC interfaces support Chinese and English. Recovery controls are not exposed in 0.1.0; existing local data and pending uploads are retained.

## Guides

| Reader | Start here |
|---|---|
| Server operator | [Server and Docker deployment](docs/deployment_en.md) |
| Researcher | [Illustrated GEP user guide](docs/researcher_en.md) |
| Godot developer | [GEC integration, events and packaging](docs/gec_godot_en.md) · [GEC_SDK source](GEC_SDK/) |

## Game example

Moss Duel is available as a [clean standalone game](examples/moss_duel/readme_en.md) and a [GEC-connected project](examples/moss_duel_gec/readme_en.md). To practise adding GEC from scratch, copy the clean game and follow the [manual integration guide](docs/moss_duel_gec_en.md); use the connected version to compare or try the GEP workflow. Both share the same game scripts and assets. See the [example overview](examples/README.md) for the two projects.

See also the [protocol](docs/protocol.md), [security notes](docs/security.md) and [engineering verification](docs/verification.md).

## Scope

GEC targets Godot 4.7.2, Chrome for Web, macOS arm64 and Windows x64. The Docker image runs on a Linux container engine, including Docker Desktop on Windows. Validation records describe the actual builds and environments checked; they do not establish scientific timing accuracy, capacity for an arbitrary study, or approval to collect participant data. Review consent, retention, access and backup arrangements before using it for a study.

Code and documentation use [Apache License 2.0](LICENSE). Third-party components retain their own [licences](third_party/). Report issues with a public or synthetic example; do not upload participant records, passwords or recovery files.
