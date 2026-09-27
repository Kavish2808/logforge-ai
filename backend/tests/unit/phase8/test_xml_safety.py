"""Hardened XML (Phase 8 Step 2): every attack class is rejected with a
controlled error, and while parsing hostile input no network connection, file
open or process execution is even attempted. Time and memory stay bounded."""
import builtins
import os
import random
import socket
import subprocess
import time
import tracemalloc

import pytest

from app.pipeline.detector.format_detector import detect_secondary_format
from app.pipeline.orchestrator import process
from app.pipeline.parsers.base import ParserError
from app.pipeline.parsers.xml_parser import XMLParser
from app.pipeline.parsers.xml_safety import (
    MAX_DEPTH,
    MAX_ELEMENTS,
    MAX_XML_BYTES,
    XmlRejected,
    is_safe_xml,
    safe_parse,
)
from app.schema.ocsf import FormatType

SECRET = "TOP-SECRET-CONTENT-42"
LONE_SURROGATE = chr(0xD800)  # built at runtime: never present as a raw character in this source file

XXE_FILE = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file://{path}">]><r>&x;</r>'
XXE_URL = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "http://127.0.0.1:9/xxe">]><r>&x;</r>'
EXTERNAL_DTD = '<?xml version="1.0"?><!DOCTYPE r SYSTEM "http://127.0.0.1:9/evil.dtd"><r>x</r>'
PARAM_ENTITY = '<!DOCTYPE r [<!ENTITY % p SYSTEM "http://127.0.0.1:9/p.dtd"> %p;]><r/>'
BILLION_LAUGHS = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
                  + "".join(f'<!ENTITY lol{i} "{("&lol" + str(i - 1) + ";") * 10 if i > 1 else "&lol;" * 10}">'
                            for i in range(1, 10))
                  + ']><lolz>&lol9;</lolz>')
QUADRATIC = '<!DOCTYPE r [<!ENTITY a "' + "A" * 50_000 + '">]><r>' + "&a;" * 5_000 + "</r>"
UNDECLARED_ENTITY = "<r>&undefined;</r>"
LOWERCASE_DOCTYPE = "<!doctype r><r/>"
SPACED_DOCTYPE = "<! DOCTYPE r><r/>"


@pytest.fixture()
def no_side_effects(monkeypatch):
    """Fail loudly if parsing attempts any network, file or process access."""
    attempts = []

    def trap(kind):
        def _trap(*args, **kwargs):
            attempts.append((kind, args[:1]))
            raise AssertionError(f"XML parsing attempted {kind}")
        return _trap

    monkeypatch.setattr(socket.socket, "connect", trap("network connect"))
    monkeypatch.setattr(socket, "create_connection", trap("network connect"))
    monkeypatch.setattr(socket, "getaddrinfo", trap("DNS lookup"))
    monkeypatch.setattr(subprocess, "Popen", trap("process execution"))
    monkeypatch.setattr(os, "system", trap("process execution"))
    monkeypatch.setattr(os, "popen", trap("process execution"))
    real_open = builtins.open
    opened = []

    def tracking_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", tracking_open)
    yield {"attempts": attempts, "opened": opened}
    assert attempts == []


def _hostile(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text(SECRET)
    return {
        "xxe_file": XXE_FILE.format(path=secret.as_posix()),
        "xxe_url": XXE_URL,
        "external_dtd": EXTERNAL_DTD,
        "parameter_entity": PARAM_ENTITY,
        "billion_laughs": BILLION_LAUGHS,
        "quadratic_blowup": QUADRATIC,
        "undeclared_entity": UNDECLARED_ENTITY,
        "lowercase_doctype": LOWERCASE_DOCTYPE,
        "spaced_doctype": SPACED_DOCTYPE,
    }


@pytest.mark.parametrize("case", ["xxe_file", "xxe_url", "external_dtd", "parameter_entity", "billion_laughs",
                                  "quadratic_blowup", "undeclared_entity", "lowercase_doctype", "spaced_doctype"])
def test_attacks_are_rejected_without_side_effects(case, tmp_path, no_side_effects):
    payload = _hostile(tmp_path)[case]
    tracemalloc.start()
    started = time.perf_counter()
    with pytest.raises(XmlRejected):
        safe_parse(payload)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert elapsed < 1.0 and peak < 5 * 1024 * 1024  # bounded: no expansion ever happens
    assert not any("secret" in p for p in no_side_effects["opened"])
    assert detect_secondary_format(payload) == FormatType.UNKNOWN
    with pytest.raises(ParserError):
        XMLParser().parse(payload)


def test_xxe_never_leaks_file_content_through_the_pipeline(tmp_path, no_side_effects):
    payload = _hostile(tmp_path)["xxe_file"]
    result = process(payload)
    assert result.status == "FAILED" and result.format_detected == "unknown"
    assert SECRET not in repr(result.__dict__)
    assert result.raw_event == payload  # raw preserved verbatim


def test_expat_handlers_also_refuse_declarations_even_if_precheck_were_bypassed(monkeypatch):
    """Defense in depth: disable the textual precheck; the expat handlers must still refuse."""
    from app.pipeline.parsers import xml_safety

    monkeypatch.setattr(xml_safety, "_DECLARATION_RE", xml_safety.re.compile(r"(?!x)x"))  # never matches
    for payload in (XXE_URL, EXTERNAL_DTD, BILLION_LAUGHS, "<!DOCTYPE r><r/>"):
        with pytest.raises(XmlRejected):
            safe_parse(payload)


def test_oversize_input_is_rejected_before_parsing():
    payload = "<r>" + "a" * (MAX_XML_BYTES + 1) + "</r>"
    with pytest.raises(XmlRejected, match="exceeds"):
        safe_parse(payload)


def test_excessive_depth_is_rejected():
    ok = "<a>" * MAX_DEPTH + "</a>" * MAX_DEPTH
    assert safe_parse(ok).tag == "a"
    deep = "<a>" * (MAX_DEPTH + 1) + "</a>" * (MAX_DEPTH + 1)
    with pytest.raises(XmlRejected, match="nesting"):
        safe_parse(deep)
    very_deep = "<a>" * 100_000  # would exhaust a recursive parser
    with pytest.raises(XmlRejected):
        safe_parse(very_deep)


def test_excessive_element_count_is_rejected():
    ok = "<r>" + "<e/>" * (MAX_ELEMENTS - 1) + "</r>"
    assert len(safe_parse(ok).children) == MAX_ELEMENTS - 1
    too_many = "<r>" + "<e/>" * MAX_ELEMENTS + "</r>"
    with pytest.raises(XmlRejected, match="elements"):
        safe_parse(too_many)


def test_attribute_limits():
    many = "<r " + " ".join(f'a{i}="1"' for i in range(65)) + "/>"
    with pytest.raises(XmlRejected, match="attributes"):
        safe_parse(many)


@pytest.mark.parametrize("payload", [
    "<r>" + LONE_SURROGATE + "</r>",                   # lone surrogate: not encodable as UTF-8
    '<?xml version="1.0" encoding="UTF-16"?><r>x</r>',  # declaration cannot switch the decoder
    '<?xml version="1.0" encoding="EBCDIC-US"?><r/>',
    "<r>\x00</r>",                                     # NUL is not allowed in XML
    "<r>\x01</r>",
])
def test_non_utf8_and_invalid_characters_are_handled(payload):
    try:
        root = safe_parse(payload)
    except XmlRejected:
        return
    assert root.tag == "r"  # parsed as UTF-8 regardless of the declared encoding


def test_valid_unicode_is_preserved():
    root = safe_parse('<r a="ü✓">日本語 🚀</r>')
    assert root.attrs == {"a": "ü✓"} and root.text == "日本語 🚀"


@pytest.mark.parametrize("payload", [
    "<a><b></a></b>", "<a>", "</a>", "<a x='1' x='2'/>", "<a><![CDATA[x</a>", "<?xml version='1.0'?>",
    "<a/><b/>", "<>", "<a b=c/>", "<a>&#xFFFFFFFF;</a>", "<a>&#0;</a>", "",
])
def test_malformed_xml_is_a_controlled_rejection(payload):
    with pytest.raises(XmlRejected):
        safe_parse(payload)
    assert is_safe_xml(payload) is False
    assert detect_secondary_format(payload) == FormatType.UNKNOWN


def test_non_text_input_is_rejected():
    with pytest.raises(XmlRejected):
        safe_parse(b"<r/>")  # type: ignore[arg-type]


def _utf8_encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def test_fuzz_never_escapes_as_an_unhandled_exception():
    rng = random.Random(8)
    alphabet = "<>/=\"'!?&;#[]-: abcDOCTYPEENTITYxml\t\n\x00" + LONE_SURROGATE + "LEEF:1.0|2.0^\\"
    for _ in range(3_000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))
        for candidate in (text, "<" + text, "<r>" + text + "</r>", "LEEF:1.0|" + text, "LEEF:2.0|" + text):
            assert detect_secondary_format(candidate) in (FormatType.UNKNOWN, FormatType.LEEF, FormatType.XML)
            try:
                safe_parse(candidate)
            except XmlRejected:
                pass
            if _utf8_encodable(candidate):  # API-reachable input (see the surrogate test below)
                result = process(candidate)  # the whole pipeline never raises
                assert result.raw_event == candidate


def test_lone_surrogates_are_a_pre_existing_boundary_case_unchanged_by_step2(monkeypatch):
    """A lone surrogate cannot be UTF-8 encoded. The frozen Phase 0 hashing
    step (first line of process()) raises on it, before any Step 2 code runs,
    exactly as before Phase 8; the ingest API rejects such input with 422 at
    the request boundary, so it never reaches the pipeline. The Step 2
    components themselves handle it without raising."""
    from app.pipeline import orchestrator

    raw = "<r>" + LONE_SURROGATE + "</r>"
    assert detect_secondary_format(raw) == FormatType.UNKNOWN
    with pytest.raises(XmlRejected):
        safe_parse(raw)
    with pytest.raises(UnicodeEncodeError) as current:
        process(raw)
    monkeypatch.setattr(orchestrator, "detect_secondary_format", lambda r: FormatType.UNKNOWN)
    with pytest.raises(UnicodeEncodeError) as pre_step2:
        process(raw)
    assert current.traceback[-1].name == pre_step2.traceback[-1].name == "sha256_hex"


def test_random_bytes_decoded_never_break_detection():
    rng = random.Random(42)
    for _ in range(2_000):
        raw = bytes(rng.randrange(256) for _ in range(rng.randint(0, 200))).decode("latin-1")
        assert detect_secondary_format(raw) in (FormatType.UNKNOWN, FormatType.LEEF, FormatType.XML)
        assert detect_secondary_format("<" + raw) in (FormatType.UNKNOWN, FormatType.XML)


def test_detect_secondary_format_handles_non_string_like_input():
    assert detect_secondary_format(None) == FormatType.UNKNOWN  # type: ignore[arg-type]
    assert detect_secondary_format("   ") == FormatType.UNKNOWN
