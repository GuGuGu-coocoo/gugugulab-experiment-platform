# GEP/1 synthetic implementation protocol

The server accepts bounded JSON (256 KiB, 64 events per batch, 10,000 events per session, depth 16). JSON duplicate keys, nonfinite values, negative zero and numbers outside the JavaScript safe integer magnitude are rejected. Object order is irrelevant; arrays preserve order; booleans differ from numbers; null differs from absence. JSON Schema 2020-12 payload definitions are frozen with builds. No remote references are permitted by the build registration contract.

Records have immutable UUID event/session/segment identities and positive segment sequence numbers. Retries preserve all record fields. A batch commits atomically or fails completely. ACK accepted/duplicate lists must exactly cover the request. An HTTP status alone is never an ACK.

Completion declares explicit event and segment UUID sets. It can precede uploads and stays pending until the declared events arrive. Once declared, additional events outside the set are rejected. Completion is a data collection fact, not scientific acceptance or backup confirmation.

Admission uses an operation UUID plus a private client-generated proof, persisted before the first request. Repeating the same operation requires its proof and binding; a public operation UUID cannot retrieve the session credential. Participant codes are scoped to a study and preserved as strings. Session bearer credentials never authorize administration or RAW reads.

These limits and protocol tests are synthetic engineering evidence. Browser/native persistence and end-to-end acceptance remain separate gates.

Recovery requires a study-authorized, unexpired one-time permit plus the original private recovery proof. Public participant/session identifiers are insufficient. Successful reauthentication atomically persists renewed credentials and resets the paused upload retry budget; a rejected permit cannot unpause it. The response includes `task_finished`. A local or server completion declaration forces data-only recovery: no new trial, segment or completion-set expansion. Experiments without a declared compatible recovery strategy also recover data only.

New participation locks older active sessions out of foreground recovery; it does not erase their unacknowledged data or modify already cleaned tombstones. Cleanup requires the complete declared event set, a durably saved valid completion ACK and no pending records or recovery dependencies. Transaction/process interruption is covered separately for IndexedDB and native SQLite; physical power-loss and secure-erasure guarantees are outside this acceptance.
