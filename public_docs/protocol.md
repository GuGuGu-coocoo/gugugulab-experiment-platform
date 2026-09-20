# GEP/1 synthetic implementation protocol

The server accepts bounded JSON (256 KiB, 64 events per batch, 10,000 events per session, depth 16). JSON duplicate keys, nonfinite values, negative zero and numbers outside the JavaScript safe integer magnitude are rejected. Object order is irrelevant; arrays preserve order; booleans differ from numbers; null differs from absence. JSON Schema 2020-12 payload definitions are frozen with builds. No remote references are permitted by the build registration contract.

Records have immutable UUID event/session/segment identities and positive segment sequence numbers. Retries preserve all record fields. A batch commits atomically or fails completely. ACK accepted/duplicate lists must exactly cover the request. An HTTP status alone is never an ACK.

Completion declares explicit event and segment UUID sets. It can precede uploads and stays pending until the declared events arrive. Once declared, additional events outside the set are rejected. Completion is a data collection fact, not scientific acceptance or backup confirmation.

Admission uses an operation UUID plus a private client-generated proof, persisted before the first request. Repeating the same operation requires its proof and binding; a public operation UUID cannot retrieve the session credential. Participant codes are scoped to a study and preserved as strings. Session bearer credentials never authorize administration or RAW reads.

These limits and protocol tests are synthetic engineering evidence. Browser/native persistence and end-to-end acceptance remain separate gates.

Recovery requires a study-authorized, unexpired one-time permit plus the original private recovery proof. Public participant/session identifiers are insufficient. Successful reauthentication atomically persists renewed credentials and resets the paused upload retry budget; a rejected permit cannot unpause it. The response includes `task_finished`. A local or server completion declaration forces data-only recovery: no new trial, segment or completion-set expansion. Experiments without a declared compatible recovery strategy also recover data only.

## Versioned same-device recovery (03D engineering)

The long-permit recovery above is unchanged. A separate route `POST /v1/participant/recovery` accepts exactly one versioned capability and fails closed with `unsupported_capability` (409) for anything else.

`recovery_code/v1` carries a six-digit code a study-authorized researcher issued for one session, plus the device's original private proof and the frozen `instance_id`/`study_id`/`release_id`/`build_id` binding. No public session or participant UUID is required, because the server resolves the session internally. Issuance requires current `session.recover` (with its `study.view` prerequisite) on a live account; the code lives five minutes, is bound to one session/study/release/issuer, allows at most five failed attempts, and a newer issuance invalidates the previous code. Only a server-secret HMAC of the digits is stored. The code alone discloses nothing: a wrong code, proof or binding returns the same `recovery_denied` (403) without a session credential, and a consumed code cannot be replayed. Issuance and redemption are rate-limited by bounded study, client and instance keys, never by the code or proof itself, so one client's failures do not lock other clients. A revoked session stays unrecoverable, and a successful redemption renews the session without touching its completion declaration.

`recovery_named/v1` is the explicit same-device continuation for a frozen password-mode release: it requires the original private proof, the matching roster participant code and the configured password, the same binding and an active, unexpired roster entry. A public participant code alone restores nothing, and a device that reports `front_locked` is refused (`front_locked`, 403) and must use the issued code or long permit path instead.

Both capabilities answer with the renewed session credential, `session_id` and `task_finished` only; a finished queue recovers data only under its original closed completion, never trial replay. Issuance and redemption write secret-free audit rows. This is synthetic engineering evidence: the GEC shell still uses the long-permit path until the 03D shell delivery exposes the six-digit and named inputs.

New participation locks older active sessions out of foreground recovery; it does not erase their unacknowledged data or modify already cleaned tombstones. Cleanup requires the complete declared event set, a durably saved valid completion ACK and no pending records or recovery dependencies. Transaction/process interruption is covered separately for IndexedDB and native SQLite; physical power-loss and secure-erasure guarantees are outside this acceptance.

## Study entry, public listing and the current release

The additional fields below were added in the Phase 03 03C delivery and are covered by synthetic engineering tests, not by human acceptance.

A study is listed publicly only when the researcher explicitly marks it public, recruitment is open and the study has a current release that is approved and resource-located. Private and roster studies never become public through recruitment or authentication mode, and a study without a current release does not open new participation through the portal. A closed study is only shown as an opted-in summary without any start.

The stable study entry on the experiment origin reports the current release and publication revision it observed. A new participation request can bind them instead of a direct release:

- Stable-entry admission uses `study_id`, `expected_release_id` and `expected_revision` instead of `release_id`.
- The start click carries that observation through the platform entry gate: the release application is only served while the binding is still the study's current, open, approved and resource-located release, and the validated `expected_release_id`/`expected_revision` are injected into the application context so the shipped client sends them with its create request. A page whose binding is superseded receives the entry-refresh page (409) instead of the application and creates no session.
- A create request that carries the stable-entry binding is validated as a stable entry even when it also carries `release_id`; only a request with `release_id` alone uses the frozen direct-release contract.
- If the current release or the publication revision changed after the page loaded, the request is refused with `stale_entry` (409) and no session is created; the server never silently switches materials.
- Repeating an existing create operation with its original proof and binding returns the original session even after the current release changed, before any new current-release policy is applied.
- Direct `release_id` requests keep their frozen original admission contract (approved release plus open recruitment) and are never redirected to another release. Existing sessions, uploads, recovery permits, configuration downloads, exports and resource URLs stay bound to their original release and are not rewritten.

## Public participation links

The personal site only links to the participation portal; the portal and experiment resources are served on the experiment origin, and the researcher workbench stays on its own origin with a host-only management cookie. Real domain, DNS and TLS deployment remain future work and are not part of this implementation.
