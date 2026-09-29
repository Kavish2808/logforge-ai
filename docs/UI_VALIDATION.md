# Browser verification

> **Accuracy notice (defect remediation, 2026-09-29).** Parts of this document describe capabilities the
> application in this repository does not have. Verified against the code: the backend supports **PostgreSQL
> only** (no SQLite); there is **no evidence-signing key or keyed batch signature** (Merkle anchors are unsigned
> local append-only files); ingestion has **no idempotency key, deduplication or `raw_base64`**; there is **no
> drain/readiness endpoint** (only `GET /health`); outbound webhook evidence delivery is **not implemented**; and
> the integrity check is `GET /api/v1/integrity/verify` (all routes are under `/api/v1`). Statements below that
> depend on those features are not valid for this codebase. Authoritative references: README.md,
> [API.md](API.md), [ARCHITECTURE.md](ARCHITECTURE.md), [CAPABILITIES.md](CAPABILITIES.md).

Verified on 28 September 2026 in the Codex Chromium browser against the real local FastAPI server and Vite frontend. The UI used an isolated SQLite database and a randomly generated test account; credentials and test databases are excluded from the source archive.

| Check | Result |
| --- | --- |
| Authenticated sign-in | Real bearer session opened the empty dashboard |
| Demo ingestion | Explicit UI action persisted 29 labeled events across seven sources |
| Dashboard | Real counts, format distribution, recent events, and alerts displayed |
| Event explorer | Real event rows, statuses, vendor information, revisions, and pagination displayed |
| Failed XML forensics | Rejected entity/DTD input remained recoverable as original text; SHA-256, valid Merkle proof, and processing warning displayed |
| Source intelligence | Actual source counts, accepted baselines, and absent golden references displayed |
| Learning | Frozen shipped adapters and the proposal workflow displayed |
| Governance | All 29 events verified; audit chain reported intact |
| Sign-out | Server logout completed and the sign-in page returned |
| Browser console | No captured errors or warnings during these checks |

Responsive visual checks covered the overview at 1440 pixels, source cards at 1280, learning at 960, and governance/mobile navigation at 768. No horizontal page overflow was observed at 1280, 960, or 768 pixels. The viewport override was reset afterward. Keyboard focus and screen-reader behavior are improved by the modal focus handling, labels, and closed-mobile-navigation accessibility guard; this is not a comprehensive accessibility certification.

Full governance transitions, concurrency, replay interruption and integrity failures are exercised by the backend tests. The browser check is a targeted integration and visual check, not exhaustive automation of every button and browser version.
