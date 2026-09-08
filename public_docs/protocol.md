# GEP/1 protocol (implementation in progress)

The server accepts bounded JSON (256 KiB, 64 events per batch, 10,000 events per session, depth 16). JSON duplicate keys, nonfinite values, negative zero and numbers outside the JavaScript safe integer magnitude are rejected. Object order is irrelevant; arrays preserve order; booleans differ from numbers; null differs from absence. JSON Schema 2020-12 payload definitions are frozen with builds. No remote references are permitted by the build registration contract.

Records have immutable UUID event/session/segment identities and positive segment sequence numbers. Retries preserve all record fields. A batch commits atomically or fails completely. ACK accepted/duplicate lists must exactly cover the request. An HTTP status alone is never an ACK.

Completion declares explicit event and segment UUID sets. It can precede uploads and stays pending until the declared events arrive. Once declared, additional events outside the set are rejected. Completion is a data collection fact, not scientific acceptance or backup confirmation.

Admission uses an operation UUID plus a private client-generated proof, persisted before the first request. Repeating the same operation requires its proof and binding; a public operation UUID cannot retrieve the session credential. Participant codes are scoped to a study and preserved as strings. Session bearer credentials never authorize administration or RAW reads.

These limits and protocol tests are synthetic engineering evidence. Browser/native persistence and end-to-end acceptance remain separate gates.
