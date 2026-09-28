# API and integration guide

Run the backend and open `/docs` for the generated, authoritative schema. Request bodies reject unknown fields. All event, learning, replay, governance, and integration operations require an authenticated session. Liveness, readiness, and operational metrics contain no event payloads and are public.

## Authenticate

```http
POST /api/auth/login
Content-Type: application/json

{"username":"admin","password":"your-private-password"}
```

Use the returned `access_token` as `Authorization: Bearer <token>`. Sessions are stored as token hashes, have an expiry, and can be revoked by logout. Never put credentials in URLs or commit request files containing passwords.

For command-line testing, set a private environment variable and let the benchmark script obtain the token. The React console stores its token in the current browser session. The CLI bootstrap accepts passwords through a named environment variable, and the PowerShell helper prompts without echo.

## Ingest

```http
POST /api/ingest
Authorization: Bearer <token>
Content-Type: application/json

{
  "source": "edge-firewall",
  "idempotency_key": "edge-firewall-batch-00001",
  "events": [
    {"external_id":"vendor-event-001","raw":"{\"src_ip\":\"192.0.2.10\",\"dst_ip\":\"198.51.100.20\",\"action\":\"allow\",\"policy_id\":7}"},
    {"external_id":"vendor-event-002","raw":"CEF:0|Acme|Firewall|1|100|Allowed connection|5|src=192.0.2.10 dst=198.51.100.20 act=allow"}
  ]
}
```

The response includes `accepted`, `duplicates`, `events`, `batch_id`, and `merkle_root`. Accepted events can include parser failures: acceptance means evidence was persisted, not that its format was fully understood. Each event carries its own processing status.

For binary/non-UTF8 evidence, replace `raw` with `raw_base64`, containing the standard Base64 encoding of the original bytes. Exactly one payload field is required. The event detail API returns `raw_base64` for byte-exact recovery and a readable `raw_text` representation where possible.

Reuse the same idempotency key and identical body after a transport failure. A repeated batch returns the same event identities. Reusing the key with changed data returns a conflict. The default deduplication key is `(source, raw SHA-256)`; an `external_id` allows two otherwise identical log payloads to represent distinct vendor events. Reusing an external ID with different evidence is a conflict.

Bounds: 1–10,000 events per batch, 1 byte–1 MiB of raw evidence per event, and 20 MiB for the complete request. The API rejects oversize input explicitly. Batch JSON/Base64 overhead counts against the HTTP limit. Backpressure is explicit rather than an unbounded ingestion queue.

## Inspect evidence

`GET /api/events` provides the explorer's event listing. `GET /api/events/{id}` includes normalized values, extensions, accounting, raw SHA-256, exact Base64 evidence, detailed and compact lineage, revision history, and batch Merkle proof results.

`GET /api/integrity` returns `valid`, `total`, `failures`, `audit_valid`, and `batches_valid`. An integrity failure is an investigation signal; do not overwrite hashes to make the check pass.

`GET /api/export?format=json` and `GET /api/export?format=ndjson` provide explicit exports. Use the actual API pagination/limit contract from `/docs` for larger extraction rather than assuming a single export contains the entire database.

## Review and replay

The console exposes candidate proposal, validation, shadow test, approval, activation, evolution, rollback, baseline/golden review, and replay operations. Refer to `/docs` for the exact endpoint models. The core rules also apply to direct API clients:

- A maker cannot approve their own candidate or sensitive review, even with an admin role.
- A candidate below 50% match is rejected. A 50–90% match requires review; at least 90% is eligible for normal validation, never automatic activation.
- A shadow `BLOCKED` result cannot activate. Missing coverage is reported and requires explicit human review.
- Replay requires authentication at every size. More than 10,000 selected events requires a separate administrator's approval.
- Replay adds revisions; it does not replace raw evidence or erase the original revision.

## Webhook contract

Webhook integration is an explicit delivery operation. An administrator registers an endpoint with `POST /api/integrations` and queues up to 100 event IDs with `POST /api/integrations/{id}/deliver`. If event IDs are omitted, the latest 100 are selected. The delivery captures an immutable serialized snapshot, so replay cannot change its body between retries. Its JSON envelope has `delivery_id`, `schema: "logforge.ocsf-aligned.v1"`, and `events` (event summaries containing normalized data and hashes).

The worker sends `Content-Type: application/json`, `Idempotency-Key: <delivery_id>`, and `X-LogForge-Signature`. The signature is HMAC-SHA256 using the application signing key over canonical JSON `{delivery_id, payload_sha256}`; canonical JSON uses sorted keys, UTF-8, no extra spaces, and no nonfinite numbers. Receiver-specific signing keys/provisioning are not implemented; do not expose the application signing key as an ordinary integration credential.

Any HTTP 2xx is a successful acknowledgement. Other responses/network errors retry up to five attempts with bounded exponential backoff, after which the delivery becomes failed and generates an alert. Receivers should deduplicate the delivery identity before applying side effects: a lost acknowledgement can result in another attempt. Delivery state and HTTP status are visible in the integration listing.

Private/loopback addresses are disabled by default, endpoints are validated, DNS is resolved and pinned for each connection, and redirects are not followed. `LOGFORGE_ALLOW_PRIVATE_WEBHOOKS=true` is only for intentionally configured local integration testing. `LOGFORGE_WEBHOOK_HOSTS` restricts destinations and is mandatory for forwarding in production mode.

This is a generic webhook/export contract. A deployed receiver, vendor-specific SIEM acknowledgement semantics, and end-to-end receiver verification are separate integration work. Do not treat the existence of these endpoints as a certification for a named SIEM.

