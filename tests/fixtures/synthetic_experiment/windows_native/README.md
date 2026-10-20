# Windows x64 native acceptance material (synthetic only)

This directory holds the delivery-side material for the mandatory Windows x64
native acceptance (03D/03F). Nothing here is a pass record, and nothing here
carries a credential or participant data.

| File | Audience | Purpose |
|---|---|---|
| `WINDOWS_NATIVE_GUIDE.zh-CN.md` | original designer (autonomous experience) | Chinese self-directed WN01–WN06 guide; no command for the human to type, prepared launchers only |
| `WINDOWS_NATIVE_RESULT_TEMPLATE.md` | original designer | structured result table; `未运行/待运行` is a distinct outcome |

Machine side (repository tools, never shipped to participants):

* `tools/phase03_verify_windows_native.py` - preparation, runtime service and
  strict gate:
  * `--verify-preparation --probe-device`: pinned toolchain, the real cross-built
    program archive (PE32+ x86-64, PCK 4.7.2, Windows ZIP rules), a readiness kit
    built from the **real platform lifecycle** (isolated instance, three frozen
    studies/releases sharing one immutable build, real roster accounts, a real
    invited member, real logins and admissions, the complete package and sidecars
    downloaded through the authenticated release endpoints, platform-side WN06
    contracts), plus a read-only device probe. The readiness report keeps
    `preparation_status` separate from `runtime_status`; the latter stays
    `NOT_RUN`/`BLOCKED` until a real Windows x64 desktop runs the program.
  * `--serve-runtime --run <dir>`: serve the prepared isolated instance on the
    frozen loopback port for a real Windows run (never rewrites the frozen
    configuration).
  * `--gate --gate-run <dir> --gate-prep <dir>`: the strict integration gate. It
    loads the **explicitly selected** run, recomputes the current
    source/build/descriptor/harness digests, re-reads the bound kit and the
    isolated instance database, and independently re-validates every WN01–WN06
    case from the raw evidence files. A bare `runtime_status=PASS`, a doctor-only
    report, a stale kit, a skipped case, a mismatched raw value or a corrupted
    evidence file is refused.
* `tools/windows_native_harness.py` - runs on the Windows machine: host and kit
  checks, package member hashes, PE32+/PCK inspection, Unicode+space path
  extraction, **owned-PID** interactive launch evidence (window, executable path,
  console session, architecture), the real `--synthetic-auto` engineering flows
  for WN02–WN06 (three modes, wrong credentials/binding, offline local commit,
  reconnect, lost ACK, process kill, checkpoint recovery, short code + device
  proof, replay/expiry, shared writer, cleanup, data-only recovery, secret-free
  failure export, authorized JSONL reconciliation, old-release compatibility) and
  a scoped loopback fault proxy. It fails when a prerequisite is missing instead
  of skipping, and it records `NOT_RUN`/`BLOCKED` with the exact condition.
* `tools/phase03_windows_kit.py` - the real-lifecycle kit builder used by the
  preparation gate (isolated instance + authenticated researcher/participant
  HTTP flow + authenticated downloads + platform-side WN06 contracts).
* `tools/phase03_verify_windows_package.py --verify` - real cross-build and
  frozen-package engineering closure (already part of 03D).

The strict final integration gate refuses Phase completion while real-device WN
evidence is missing; designer autonomous QA and independent T17 remain `NOT_RUN`
until they actually happen. Automated engineering checks never substitute for the
designer's own experience or for independent T17.
