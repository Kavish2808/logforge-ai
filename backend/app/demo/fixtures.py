"""Deterministic Demo Mode fixtures: one fictional firewall ("LogForgeDemo
EdgeFirewall") whose logs evolve from a v1 to a v2 structure.

Everything here is a pure function of constants — no clock, no randomness —
so every run produces byte-identical raw logs and therefore identical
SHA-256 hashes. Those hashes are how demo-owned events are recognised for
cleanup; every raw log also carries the `device=lfdemo-edge-fw01` marker.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

# Onboarding session name that marks a session as demo-owned.
DEMO_ID = "logforge-demo-firewall-v1"
# Adapter id / Phase 5 source key of the demo source (adapter ids allow [a-z0-9_] only).
DEMO_SOURCE_KEY = "logforge_demo_firewall"
DEMO_MARKER = "lfdemo-edge-fw01"
DEMO_OPERATOR = "demo-operator"

_BASE_TIME = (2026, 1, 20, 9)  # 2026-01-20T09:MM:SSZ, minute/second derived from the fixture index

# (src_ip, dst_ip, src_port, dst_port, protocol, action, severity, message)
_TRAFFIC = [
    ("10.20.1.15", "172.217.14.206", 51514, 443, "tcp", "allow", "low", "outbound https to cloud service"),
    ("10.20.1.22", "10.30.0.5", 49822, 22, "tcp", "deny", "medium", "ssh to server segment blocked by policy"),
    ("10.20.2.40", "8.8.8.8", 53011, 53, "udp", "allow", "low", "dns query to public resolver"),
    ("10.20.2.41", "203.0.113.50", 50433, 3389, "tcp", "deny", "high", "outbound rdp to internet blocked"),
    ("10.20.3.10", "10.30.0.12", 55120, 445, "tcp", "deny", "high", "smb lateral movement attempt blocked"),
    ("10.20.3.18", "151.101.1.69", 50999, 443, "tcp", "allow", "low", "outbound https to cdn"),
    ("10.20.1.30", "10.30.0.8", 60122, 1433, "tcp", "allow", "medium", "database connection from app tier"),
    ("10.20.4.2", "198.51.100.23", 40112, 25, "tcp", "deny", "medium", "direct smtp egress blocked"),
    ("10.20.4.9", "10.30.0.20", 44871, 8443, "tcp", "allow", "low", "internal admin portal"),
    ("10.20.2.77", "192.0.2.144", 62001, 123, "udp", "allow", "low", "ntp synchronisation"),
    ("10.20.5.3", "45.33.32.156", 52876, 4444, "tcp", "deny", "critical", "connection to known c2 port blocked"),
    ("10.20.5.14", "10.30.0.31", 58123, 5432, "tcp", "allow", "medium", "postgres connection from reporting"),
]
_POLICIES = [("1042", "dmz"), ("1043", "internal"), ("2001", "untrust"), ("1042", "dmz"), ("3007", "internal"), ("2001", "untrust")]


@dataclass(frozen=True)
class Fixture:
    key: str
    kind: str  # SAMPLE_V1 | UNKNOWN_PROBE | EVENT_V1 | MALFORMED | DRIFT_V2 | EVIDENCE_V2 | CHECK_V2 | CHECK_V1 | POST_ROLLBACK_V1
    raw: str
    purpose: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw.encode("utf-8")).hexdigest()


def _ts(index: int) -> str:
    y, mo, d, h = _BASE_TIME
    return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{index // 60 % 60:02d}:{index % 60:02d}Z"


def v1_log(index: int) -> str:
    """v1 structure: timestamp vendor product device src_ip dst_ip src_port dst_port protocol action severity message."""
    src, dst, sport, dport, proto, action, sev, msg = _TRAFFIC[index % len(_TRAFFIC)]
    return (
        f"timestamp={_ts(index)} vendor=LogForgeDemo product=EdgeFirewall device={DEMO_MARKER} "
        f"src_ip={src} dst_ip={dst} src_port={sport + index} dst_port={dport} protocol={proto} "
        f'action={action} severity={sev} message="{msg}"'
    )


def v2_log(index: int) -> str:
    """v2 structure (firmware update): dst_port renamed to dst_port_number in the same
    position, plus two new fields, policy_id and zone. Everything else is unchanged."""
    src, dst, sport, dport, proto, action, sev, msg = _TRAFFIC[index % len(_TRAFFIC)]
    policy, zone = _POLICIES[index % len(_POLICIES)]
    return (
        f"timestamp={_ts(index)} vendor=LogForgeDemo product=EdgeFirewall device={DEMO_MARKER} "
        f"src_ip={src} dst_ip={dst} src_port={sport + index} dst_port_number={dport} protocol={proto} "
        f'action={action} severity={sev} message="{msg}" policy_id={policy} zone={zone}'
    )


def _build() -> list[Fixture]:
    out: list[Fixture] = []
    for i in range(12):
        out.append(Fixture(f"sample_v1_{i + 1:02d}", "SAMPLE_V1", v1_log(i), "onboarding sample (v1 structure)"))
    out.append(Fixture("unknown_probe", "UNKNOWN_PROBE", v1_log(100),
                       "ingested before onboarding: an unknown vendor fails, raw preserved"))
    for i in range(8):
        out.append(Fixture(f"event_v1_{i + 1:02d}", "EVENT_V1", v1_log(200 + i), "live v1 log parsed by adapter v1"))
    out.append(Fixture("malformed", "MALFORMED",
                       f'timestamp={_ts(300)} vendor=LogForgeDemo device={DEMO_MARKER} message="truncated record',
                       "malformed record (unterminated quote): fails, raw preserved verbatim"))
    out.append(Fixture("drift_trigger", "DRIFT_V2", v2_log(400), "first v2 log: structural drift, held for review"))
    for i in range(6):
        out.append(Fixture(f"evidence_v2_{i + 1:02d}", "EVIDENCE_V2", v2_log(401 + i),
                           "more v2 logs after the drift was accepted (learning evidence)"))
    out.append(Fixture("check_v2", "CHECK_V2", v2_log(500), "new structure after adapter v2 is active"))
    out.append(Fixture("check_v1", "CHECK_V1", v1_log(501), "old structure after adapter v2 is active (compatibility)"))
    out.append(Fixture("post_rollback_v1", "POST_ROLLBACK_V1", v1_log(600), "v1 log after rolling back to adapter v1"))
    return out


FIXTURES: tuple[Fixture, ...] = tuple(_build())
BY_KEY: dict[str, Fixture] = {f.key: f for f in FIXTURES}


def of_kind(kind: str) -> list[Fixture]:
    return [f for f in FIXTURES if f.kind == kind]


def raw_hashes() -> set[str]:
    return {f.sha256 for f in FIXTURES}


def fixture_for_hash(sha: str) -> Fixture | None:
    return _BY_HASH.get(sha)


_BY_HASH: dict[str, Fixture] = {f.sha256: f for f in FIXTURES}
