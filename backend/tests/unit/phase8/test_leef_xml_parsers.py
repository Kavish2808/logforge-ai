"""Native LEEF / XML parsers and the secondary detector (Phase 8 Step 2):
deterministic, lossless, bounded."""
import pytest

from app.pipeline.detector.format_detector import detect_format, detect_secondary_format
from app.pipeline.parsers.base import ParserError
from app.pipeline.parsers.leef_parser import MAX_ATTRIBUTES, LEEFParser, parse_delimiter, split_unescaped
from app.pipeline.parsers.xml_parser import XMLParser
from app.schema.ocsf import FormatType

P = LEEFParser()
X = XMLParser()


# --- LEEF ------------------------------------------------------------------------------------------------


def test_leef_1_0_tab_delimited():
    r = P.parse("LEEF:1.0|IBM|QRadar|7.4|Login|src=10.1.1.1\tdst=10.2.2.2\tusrName=bob")
    assert r.fields == {"leef_version": "1.0", "device_vendor": "IBM", "device_product": "QRadar",
                        "device_version": "7.4", "event_id": "Login", "src": "10.1.1.1", "dst": "10.2.2.2",
                        "usrName": "bob"}
    assert r.warnings == [] and r.format_detected == "leef"


@pytest.mark.parametrize("spec,delim", [("^", "^"), ("x5E", "^"), ("0x5E", "^"), ("X5e", "^"), ("x09", "\t"),
                                        ("", "\t"), ("|", "|"), (";", ";")])
def test_leef_2_0_delimiter_specs(spec, delim):
    attrs = delim.join(["src=1.1.1.1", "dst=2.2.2.2", "sev=4"])
    line = f"LEEF:2.0|Acme|GW|2.1|E1|{spec}|{attrs}" if spec != "|" else f"LEEF:2.0|Acme|GW|2.1|E1|\\||{attrs}"
    r = P.parse(line)
    if spec == "|":  # an escaped pipe spec is a 2-char spec -> unrecognized, TAB assumed, recorded as a warning
        assert r.warnings
        return
    assert parse_delimiter(spec) == delim
    assert (r.fields["src"], r.fields["dst"], r.fields["sev"]) == ("1.1.1.1", "2.2.2.2", "4")
    assert r.fields["leef_delimiter"] == spec and r.warnings == []


def test_leef_escapes():
    r = P.parse("LEEF:2.0|Ac\\|me|GW|1|E|^|msg=a\\^b\\=c\\\\d^path=C:\\x")
    assert r.fields["device_vendor"] == "Ac|me"
    assert r.fields["msg"] == "a^b=c\\d"
    assert r.fields["path"] == "C:\\x"  # an unknown escape is kept verbatim


def test_leef_malformed_and_duplicate_attributes_are_preserved_with_warnings():
    r = P.parse("LEEF:2.0|A|B|1|E|^|src=1^noequals^=novalue^1bad=key^src=2^src=3")
    assert r.fields["src"] == "1" and r.fields["src__2"] == "2" and r.fields["src__3"] == "3"
    assert r.fields["leef_malformed_attributes"] == ["noequals", "=novalue", "1bad=key"]
    assert len(r.warnings) == 2


def test_leef_unrecognized_delimiter_spec_warns_and_assumes_tab():
    r = P.parse("LEEF:2.0|A|B|1|E|zz|src=1\tdst=2")
    assert r.fields["src"] == "1" and r.fields["dst"] == "2" and "delimiter" in r.warnings[0]


def test_leef_attribute_bound_preserves_the_tail():
    attrs = "\t".join(f"k{i}=v{i}" for i in range(MAX_ATTRIBUTES + 5))
    r = P.parse(f"LEEF:1.0|A|B|1|E|{attrs}")
    assert r.fields[f"k{MAX_ATTRIBUTES - 1}"] == f"v{MAX_ATTRIBUTES - 1}" and f"k{MAX_ATTRIBUTES}" not in r.fields
    assert r.fields["leef_unparsed_tail"].split("\t")[0] == f"k{MAX_ATTRIBUTES}=v{MAX_ATTRIBUTES}"


def test_leef_header_only_and_no_trailing_pipe():
    assert P.parse("LEEF:1.0|A|B|1|E").fields["event_id"] == "E"
    assert P.parse("LEEF:1.0|A|B|1|E|").fields["event_id"] == "E"


@pytest.mark.parametrize("raw", ["LEEF:1.0|A|B", "LEEF:3.0|A|B|C|D|x=1", "leef:1.0|A|B|C|D|x=1", "CEF:0|a|b|c|d|e|f|",
                                 "", "LEEF:2.0|A|B|C|D"])
def test_leef_malformed_headers_raise_parser_error(raw):
    with pytest.raises(ParserError):
        P.parse(raw)


def test_leef_parsing_is_deterministic():
    line = "LEEF:2.0|A|B|1|E|^|" + "^".join(f"k{i}=v{i}" for i in range(50)) + "^k3=dup^bad"
    assert all(P.parse(line).fields == P.parse(line).fields for _ in range(5))
    assert list(P.parse(line).fields) == list(P.parse(line).fields)  # key order too


def test_split_unescaped_keeps_escapes():
    assert split_unescaped("a\\|b|c", "|") == ["a\\|b", "c"]
    assert split_unescaped("a|b|c|d", "|", maxsplit=2) == ["a", "b", "c|d"]


# --- XML ---------------------------------------------------------------------------------------------------


def test_xml_flattening_is_structural_and_deterministic():
    raw = ('<Event xmlns="urn:x"><System><Provider Name="Acme"/><EventID>7</EventID></System>'
           '<EventData><Data Name="A">1</Data><Data Name="B">2</Data></EventData><Note> mixed <b>x</b> text </Note></Event>')
    fields = X.parse(raw).fields
    assert fields == {
        "xml_root": "Event", "@xmlns": "urn:x",
        "System.Provider.@Name": "Acme", "System.Provider": "", "System.EventID": "7",
        "EventData.Data[0].@Name": "A", "EventData.Data[0]": "1",
        "EventData.Data[1].@Name": "B", "EventData.Data[1]": "2",
        "Note.#text": "mixed  text", "Note.b": "x",
    }
    assert list(X.parse(raw).fields) == list(fields)


def test_xml_root_only_and_collision():
    assert X.parse("<r>hello</r>").fields == {"xml_root": "r", "#text": "hello"}
    r = X.parse("<r><xml_root>v</xml_root></r>")
    assert r.fields == {"xml_root#": "r", "xml_root": "v"} and r.warnings


def test_xml_rejections_are_parser_errors():
    with pytest.raises(ParserError, match="XML rejected"):
        X.parse('<!DOCTYPE r><r/>')


# --- secondary detection ------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("LEEF:1.0|A|B|1|E|x=1", FormatType.LEEF), ("  LEEF:2.0|A|B|1|E|^|x=1  ", FormatType.LEEF),
    ("<r/>", FormatType.XML), ('<?xml version="1.0"?>\n<r a="1">x</r>', FormatType.XML),
    ("LEEF:1.0|A", FormatType.UNKNOWN), ("xLEEF:1.0|A|B|1|E|", FormatType.UNKNOWN),
    ("<r>", FormatType.UNKNOWN), ("<1r/>", FormatType.UNKNOWN), ("< r/>", FormatType.UNKNOWN),
    ("<r/> trailing", FormatType.UNKNOWN), ("plain", FormatType.UNKNOWN),
])
def test_secondary_detection(raw, expected):
    assert detect_secondary_format(raw) == expected


def test_primary_detector_never_returns_the_new_formats():
    for raw in ("LEEF:1.0|A|B|1|E|x=1", "<r/>", '<?xml version="1.0"?><r/>', "<13>Jan 18 11:07:53 host LEEF:1.0|A|B|1|E|"):
        assert detect_format(raw) not in (FormatType.LEEF, FormatType.XML)
