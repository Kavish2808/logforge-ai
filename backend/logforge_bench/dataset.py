"""Deterministic benchmark dataset.

Event `i` is a pure function of (seed, i, formats, sizes, malformed_rate): the
same arguments always produce byte-identical raw logs, on any machine. Every
event carries a unique sequence value, so no two raw logs are identical (the
content-addressed raw vault would otherwise skip the write of a duplicate and
flatter the Phase 7 numbers).

Malformed events are produced by corrupting a well-formed event in a way a
real sender could (truncated header, broken JSON, mismatched XML tag, invalid
typed value, ...). They are sent to the real ingestion code unchanged; the
benchmark records the status the system actually returned and never assumes
what it "should" be.
"""
from __future__ import annotations

import random
import re
import statistics
from dataclasses import dataclass
from itertools import product
from typing import Iterator

FORMATS = ("fortinet", "cef", "leef", "xml", "json", "syslog")
SIZE_BANDS = {"small": 150, "medium": 1024, "large": 4096, "xlarge": 8192}
DEFAULT_MALFORMED_RATE = 0.05

# Padding attributes have fixed-width values, so every event of one
# (format, size band) gets the same number of padding fields — a stable
# structure per band instead of random field sets (which would inflate Phase 5
# drift reviews). Differences between bands are real structural variation.
_PAD_VALUE_LEN = 16

MALFORMED_KINDS = {
    "fortinet": ("truncated_syslog_header", "invalid_port_value"),
    "cef": ("truncated_cef_header", "invalid_port_value"),
    "leef": ("incomplete_leef_header", "malformed_leef_attribute"),
    "xml": ("mismatched_closing_tag", "truncated_document"),
    "json": ("truncated_object", "trailing_comma"),
    "syslog": ("garbage_prefix", "truncated_syslog_header"),
}


@dataclass(frozen=True)
class BenchEvent:
    index: int
    format: str
    size_band: str
    malformed: bool
    malformed_kind: str | None
    raw: str

    @property
    def size_bytes(self) -> int:
        return len(self.raw.encode("utf-8"))


def _ip(rng: random.Random, private: bool) -> str:
    if private:
        return f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
    return f"{rng.choice((8, 23, 45, 93, 104, 151, 172, 198))}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"


def _clock(i: int) -> tuple[str, str, str]:
    """Deterministic timestamps (no wall clock): RFC3164, ISO date, ISO time."""
    sec = i % 86_400
    hh, mm, ss = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"Jan 18 {hh:02d}:{mm:02d}:{ss:02d}", "2026-01-18", f"{hh:02d}:{mm:02d}:{ss:02d}"


def _hex(rng: random.Random, n: int = _PAD_VALUE_LEN) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


class _Values:
    def __init__(self, rng: random.Random, i: int):
        self.i = i
        self.src = _ip(rng, True)
        self.dst = _ip(rng, False)
        self.sport = rng.randint(1024, 65535)
        self.dport = rng.choice((22, 53, 80, 123, 443, 3389, 8080))
        self.user = rng.choice(("jdoe", "asmith", "svc_backup", "mlopez", "kchen", "admin"))
        self.action = rng.choice(("accept", "deny", "close", "timeout"))
        self.host = f"host{rng.randint(1, 40):02d}"
        self.rfc3164, self.date, self.time = _clock(i)
        self.seq = f"{i:010d}"


# --- well-formed builders: (base, pad_fn(k, value)) per format -------------------------------------------------


def _fortinet(v: _Values, small: bool) -> tuple[str, str, callable, str]:
    head = f"<189>{v.rfc3164} FGT100E FORTIGATE: "
    if small:
        body = (f'srcip={v.src} dstip={v.dst} dstport={v.dport} action="{v.action}" '
                f'level="notice" sessionid={v.seq}')
    else:
        body = (f'date={v.date} time={v.time} devname="FGT100E" devid="FG100E4Q17000000" logid="0000000013" '
                f'type="traffic" subtype="forward" level="notice" srcip={v.src} srcport={v.sport} dstip={v.dst} '
                f'dstport={v.dport} proto=6 action="{v.action}" policyid=1 srcintf="internal" dstintf="wan1" '
                f'sentbyte={v.sport * 3} rcvdbyte={v.sport * 5} duration={v.dport % 60} user="{v.user}" '
                f'sessionid={v.seq} msg="traffic log"')
    return head, body, (lambda k, val: f' cfgattr{k:03d}="{val}"'), ""


def _cef(v: _Values, small: bool) -> tuple[str, str, callable, str]:
    head = "CEF:0|Palo Alto Networks|PAN-OS|10.2.0|traffic|TRAFFIC|3|"
    if small:
        body = f"src={v.src} dst={v.dst} dpt={v.dport} act={v.action} cn1={v.seq}"
    else:
        body = (f"rt=Jan 18 2026 {v.time} src={v.src} dst={v.dst} spt={v.sport} dpt={v.dport} proto=tcp "
                f"act={v.action} suser={v.user} deviceExternalId=PA-VM-01 cs1Label=Rule cs1=allow-web "
                f"cs2Label=App cs2=ssl cn1Label=SessionID cn1={v.seq} in={v.sport * 3} out={v.sport * 5} "
                f"msg=traffic log")
    return head, body, (lambda k, val: f" flexString{k:03d}={val}"), ""


def _leef(v: _Values, small: bool) -> tuple[str, str, callable, str]:
    head = "LEEF:1.0|IBM|QRadar|7.5|Authentication|"
    fields = [f"src={v.src}", f"dst={v.dst}", f"usrName={v.user}", f"sev=3", f"seq={v.seq}"]
    if not small:
        fields += [f"srcPort={v.sport}", f"dstPort={v.dport}", f"proto=TCP", f"action={v.action}",
                   f"devTime=Jan 18 2026 {v.time}", "devTimeFormat=MMM dd yyyy HH:mm:ss", f"identHostName={v.host}",
                   "cat=Logon", "policy=default", f"bytesIn={v.sport * 3}", f"bytesOut={v.sport * 5}"]
    return head, "\t".join(fields), (lambda k, val: f"\tcustomAttr{k:03d}={val}"), ""


def _xml(v: _Values, small: bool) -> tuple[str, str, callable, str]:
    if small:
        head = f"<Event><EventID>4624</EventID><Computer>{v.host}</Computer><User>{v.user}</User><Seq>{v.seq}</Seq>"
    else:
        head = ('<Event><System><Provider Name="Microsoft-Windows-Security-Auditing"/><EventID>4624</EventID>'
                f'<Level>0</Level><TimeCreated SystemTime="{v.date}T{v.time}Z"/><Computer>{v.host}</Computer>'
                f'<Seq>{v.seq}</Seq></System><EventData><TargetUserName>{v.user}</TargetUserName>'
                f'<IpAddress>{v.src}</IpAddress><IpPort>{v.sport}</IpPort><LogonType>3</LogonType>'
                f'<AuthenticationPackageName>NTLM</AuthenticationPackageName></EventData>')
    return head, "", (lambda k, val: f"<Attr{k:03d}>{val}</Attr{k:03d}>"), "</Event>"


def _json(v: _Values, small: bool) -> tuple[str, str, callable, str]:
    if small:
        body = f'{{"user":"{v.user}","action":"login","src_ip":"{v.src}","seq":"{v.seq}"'
    else:
        body = (f'{{"timestamp":"{v.date}T{v.time}Z","user":"{v.user}","action":"login","src_ip":"{v.src}",'
                f'"dst_ip":"{v.dst}","src_port":{v.sport},"dst_port":{v.dport},"status":"success",'
                f'"host":"{v.host}","app":"sso-gateway","seq":"{v.seq}","message":"user authentication"')
    return "", body, (lambda k, val: f',"ext_attr_{k:03d}":"{val}"'), "}"


def _syslog(v: _Values, small: bool) -> tuple[str, str, callable, str]:
    if small:
        head = f"<34>{v.rfc3164} {v.host} sshd[{int(v.seq) % 65536}]: "
        body = f"Accepted publickey for {v.user} from {v.src} port {v.sport} seq {v.seq}"
    else:
        head = f"<165>1 {v.date}T{v.time}.000Z {v.host} appsvc {int(v.seq) % 65536} ID47 - "
        body = (f"user={v.user} src={v.src} dst={v.dst} sport={v.sport} dport={v.dport} action={v.action} "
                f"seq={v.seq} outcome=ok")
    return head, body, (lambda k, val: f" pad{k:03d}={val}"), ""


_BUILDERS = {"fortinet": _fortinet, "cef": _cef, "leef": _leef, "xml": _xml, "json": _json, "syslog": _syslog}


def _assemble(fmt: str, v: _Values, band: str, pad_count: int, rng: random.Random) -> str:
    head, body, pad, tail = _BUILDERS[fmt](v, band == "small")
    return head + body + "".join(pad(k, _hex(rng)) for k in range(pad_count)) + tail


_PAD_CACHE: dict[tuple[str, str], int] = {}


def pad_count(fmt: str, band: str) -> int:
    """Padding fields for (format, band), fixed from a prototype so the
    structure is constant within a band."""
    key = (fmt, band)
    if key not in _PAD_CACHE:
        proto = _Values(random.Random(0), 0)
        base = len(_assemble(fmt, proto, band, 0, random.Random(0)).encode())
        one = len(_BUILDERS[fmt](proto, False)[2](999, "x" * _PAD_VALUE_LEN).encode())
        _PAD_CACHE[key] = max(0, round((SIZE_BANDS[band] - base) / one))
    return _PAD_CACHE[key]


def _corrupt(fmt: str, raw: str, kind: str) -> str:
    if kind == "truncated_syslog_header":
        return raw[: raw.index(">") + 8]  # PRI + a partial timestamp, nothing else
    if kind == "invalid_port_value":  # a typed field with a non-integer value (Fortinet sends 'dstport=-')
        if fmt == "fortinet":
            return re.sub(r"dstport=\d+", "dstport=-", raw, count=1)
        return re.sub(r"dpt=\d+", "dpt=not-a-port", raw, count=1)
    if kind == "truncated_cef_header":
        return "CEF:0|Palo Alto Networks|PAN-OS"
    if kind == "incomplete_leef_header":
        return "LEEF:1.0|IBM|QRadar"
    if kind == "malformed_leef_attribute":
        return raw + "\tthis token has no equals sign\t=novalue"
    if kind == "mismatched_closing_tag":
        return raw[: -len("</Event>")] + "</Evnt>"
    if kind == "truncated_document":
        return raw[: max(20, len(raw) // 2)] + ">"
    if kind == "truncated_object":
        return raw[: max(10, len(raw) // 2)] + "}"
    if kind == "trailing_comma":
        return raw[:-1] + ",}"
    if kind == "garbage_prefix":
        return "### corrupted-sender ### " + raw
    raise ValueError(kind)


def make_event(i: int, *, seed: int, formats: tuple[str, ...] = FORMATS,
               sizes: tuple[str, ...] = tuple(SIZE_BANDS), malformed_rate: float = DEFAULT_MALFORMED_RATE) -> BenchEvent:
    combos = list(product(formats, sizes))
    fmt, band = combos[i % len(combos)]
    rng = random.Random(seed * 1_000_003 + i)
    v = _Values(rng, i)
    raw = _assemble(fmt, v, band, pad_count(fmt, band), rng)
    malformed = rng.random() < malformed_rate
    kind = rng.choice(MALFORMED_KINDS[fmt]) if malformed else None
    if kind:
        raw = _corrupt(fmt, raw, kind)
    return BenchEvent(index=i, format=fmt, size_band=band, malformed=malformed, malformed_kind=kind, raw=raw)


def generate(n: int, *, start: int = 0, **kwargs) -> list[BenchEvent]:
    return [make_event(i, **kwargs) for i in range(start, start + n)]


def stream(*, start: int = 0, **kwargs) -> Iterator[BenchEvent]:
    i = start
    while True:
        yield make_event(i, **kwargs)
        i += 1


def _pct(values: list[int], p: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def size_stats(values: list[int]) -> dict:
    if not values:
        return {"n": 0}
    return {"n": len(values), "min": min(values), "median": statistics.median(values),
            "p95": round(_pct(values, 0.95), 1), "max": max(values), "mean": round(statistics.fmean(values), 1)}


def describe(events: list[BenchEvent]) -> dict:
    """Actual dataset characteristics (measured from the generated bytes)."""
    by_band: dict[str, list[int]] = {}
    by_format: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for e in events:
        by_band.setdefault(e.size_band, []).append(e.size_bytes)
        by_format[e.format] = by_format.get(e.format, 0) + 1
        if e.malformed_kind:
            by_kind[e.malformed_kind] = by_kind.get(e.malformed_kind, 0) + 1
    malformed = sum(e.malformed for e in events)
    return {
        "events": len(events),
        "total_bytes": sum(e.size_bytes for e in events),
        "sizes_all": size_stats([e.size_bytes for e in events]),
        "sizes_by_band": {b: {"target_bytes": SIZE_BANDS[b], **size_stats(v)} for b, v in sorted(by_band.items())},
        "by_format": dict(sorted(by_format.items())),
        "malformed": malformed,
        "malformed_rate_actual": round(malformed / len(events), 4) if events else None,
        "malformed_by_kind": dict(sorted(by_kind.items())),
        "pad_fields_per_band": {f"{f}/{b}": pad_count(f, b) for f in sorted(by_format) for b in sorted(by_band)},
    }
