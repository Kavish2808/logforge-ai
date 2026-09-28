# Capability matrix and boundaries

This repository was created from the supplied Phase 1–9 narrative, which describes both implemented concepts and future aspirations. The matrix distinguishes code that exists from deployment proof. Exact test results and any benchmark artifacts belong in [VALIDATION.md](VALIDATION.md).

**Implemented** means an actual code path is present. **Automated coverage** identifies the test area; it is not a claim that every production failure mode was exercised. **Not executed here** identifies deployment checks for which the required external services were unavailable. **Deferred** identifies work that is deliberately not represented as finished.

| Phase / feature | Implementation in this project | Validation / boundary |
| --- | --- | --- |
| 1: FastAPI, database, deterministic ingestion | Batch APIs, SQLAlchemy models, native process startup, transactionally preserved raw evidence | Local SQLite/API tests; PostgreSQL deployment is a separate gate |
| 1: Core formats | Syslog, JSON, CEF parsers | Golden parser and malformed-evidence tests |
| 2: Vendor normalization | Cisco ASA, Fortinet, Palo Alto; generic mappings and preserved extension fields | Format and field-accounting tests; OCSF-aligned, not complete OCSF compliance |
| 3: Unknown vendor onboarding | Bounded samples, deterministic declarative proposal, sandbox validation, reviewer approval, versioned activation | Governance tests; external Claude/LLM service is optional future integration |
| 4: Failure hardening | Batch bounds, partial/failed states, strict envelopes, exact Base64 recovery, event APIs, idempotency | Parser/API tests and native HTTP smoke verification |
| 5: Structural drift | Presence/order/count/type weighted similarity, classifications, severity, explicit human review | Deterministic drift tests; no automatic baseline replacement |
| 6: Continuous learning | Learned-adapter-only evolution, accepted drift evidence, historical validation, immutable candidate definitions, rollback and baseline history | Workflow/governance tests |
| 7: Evidence fabric | Database raw vault, SHA-256, batch Merkle roots/proofs, signed batch anchor, revision hashes | Integrity and tamper tests; database raw vault is not WORM |
| 7: Governance | Authenticated users, RBAC, maker-checker, audit hash chain, review ledger/SLA alerts | Security and workflow tests; external paging not configured |
| 7: Exports | JSON and NDJSON evidence export | API tests; receiver-specific SIEM contracts require separate integration validation |
| 8.1: Foundation/hooks | Engine/service boundaries, persistent worker steps, evidence persistence | New version-9 schema; no claim to migrate an unrelated existing Phase-8 installation |
| 8.2: LEEF/XML | Native parsers, Syslog priority, bounded secure XML parsing | Parser security tests |
| 8.3: Compact lineage | Versioned compact codec with strict decoding and detailed lineage recovery | Codec round-trip and invalid-input tests |
| 8.4: Statistical/semantic drift | Explainable PSI, categorical/null/cardinality/median/IQR signals; deterministic advisory findings | Statistical tests; insufficient sample windows are reported, not fabricated |
| 8.5: Golden/current baselines | Explicit protected baseline operations with history and freshness checks | Maker-checker tests; absence of a golden baseline does not invent a blocker |
| 8.6: Shadow/circuit breaker | Six real-event strata, coverage counts, old/new evidence comparison, critical blockers | Shadow tests; target 25/stratum is not a claim every source has that coverage |
| 8.7: Replay/revisions | Keyset cursor, high-water mark, checkpoint, rate control, pause/cancel/resume, original plus replay revisions | Workflow/integrity tests; large jobs require independent elevated approval |
| 8.8: Cross-vendor correlation | Explainable multi-source investigation findings | Deterministic correlation checks; no automatic remediation |
| 8.9: Frontend | Responsive React operational console consuming real authenticated APIs | Production TypeScript/Vite build and browser validation as recorded |
| 8.10: Benchmarks | Real HTTP batches 1/100/1,000/10,000, sustained load, latency percentiles, optional CPU/RSS and PostgreSQL snapshots | Only an actual report counts as measured performance; low sample counts are labelled |
| 8.10B: Horizontal routing | Native NGINX round-robin with active readiness companion, pool metrics, passive failure handling, shared PostgreSQL state | Configuration/controller code and unit checks included; NGINX/PostgreSQL 1/2/4 deployment not executed here |
| 8.10B: Correctness/failure injection | Native test runner starts 1/2/4 replicas, concurrent duplicate requests, byte/hash/vault/Merkle/revision checks, kill/restart/drain | Runnable with NGINX + PostgreSQL; no fabricated scale report |
| 9: Runtime/auth/security | Bounded requests, production configuration validation, process health, drain controls, mandatory authenticated replay | Local security checks; TLS/service supervision remain deployment-specific |
| 9: Integration | Generic webhook delivery queue, bounded batches, retries/status, endpoint controls | Local contract tests where recorded; external SIEM interoperability not claimed |
| 9: Tamper evidence | Keyed batch signing plus hashes and audit chain | Signing-key custody is external; versioned rotation/external immutable anchors deferred |
| 9: Database operations | Native backup helper, explicit schema bootstrap, indexes, restore procedure | Restore drill and PostgreSQL tuning need target environment; automatic archive/delete/partitioning deferred |
| 9: Enterprise finalization | Validation scripts, operational docs, CI, honest capability boundaries | Independent security review, disaster-recovery certification, compliance evidence, and production soak deferred |

## Projected architecture, not delivered infrastructure

A durable external ingestion buffer, independently scaled parsers, partitioned event storage, separately controlled raw storage, advanced indexing, retention tiers, adaptive resource-aware routing, and fleet autoscaling may become appropriate at measured scale. None is bundled solely to inflate the architecture. This release uses a single shared persistence path, and writes serialize to preserve evidence ordering.

No throughput figure implies a billion events per day. A measured local SQLite number is not PostgreSQL performance, horizontal speedup, an end-to-end availability guarantee, or an infrastructure sizing promise.

