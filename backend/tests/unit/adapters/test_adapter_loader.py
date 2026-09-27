import pytest
import yaml

from app.adapters.loader import get_adapter_registry, load_adapters

_VALID_ADAPTER = {
    "id": "dup_adapter",
    "vendor": "V",
    "product": "P",
    "format": "json",
    "ocsf": {"class_uid": 1, "class_name": "C", "category_uid": 1, "category_name": "C"},
}


def test_loads_all_shipped_adapters():
    registry = load_adapters()
    ids = {a.id for a in registry.all()}
    assert {
        "syslog_generic",
        "json_generic",
        "cef_generic",
        "cisco_asa",
        "fortinet",
        "paloalto_cef",
    }.issubset(ids)


def test_get_adapter_registry_is_cached():
    assert get_adapter_registry() is get_adapter_registry()


def test_generic_fallback_used_when_no_vendor_matches():
    registry = load_adapters()
    adapter = registry.find_for("syslog", {"app_name": "sshd", "message": "hello"})
    assert adapter is not None
    assert adapter.id == "syslog_generic"


def test_vendor_specific_adapter_wins_over_generic_by_match_rule():
    registry = load_adapters()
    adapter = registry.find_for("syslog", {"app_name": "%ASA-6-302013"})
    assert adapter is not None
    assert adapter.id == "cisco_asa"


def test_fortinet_adapter_matches_on_exact_app_name():
    registry = load_adapters()
    adapter = registry.find_for("syslog", {"app_name": "FORTIGATE"})
    assert adapter.id == "fortinet"


def test_paloalto_adapter_matches_on_device_vendor():
    registry = load_adapters()
    adapter = registry.find_for("cef", {"device_vendor": "Palo Alto Networks"})
    assert adapter.id == "paloalto_cef"


def test_unrecognized_format_returns_none():
    registry = load_adapters()
    assert registry.find_for("evtx", {}) is None


def test_duplicate_adapter_id_is_rejected(tmp_path):
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(_VALID_ADAPTER), encoding="utf-8")
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(_VALID_ADAPTER), encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate adapter id"):
        load_adapters(tmp_path)


def test_invalid_yaml_syntax_raises_clear_error(tmp_path):
    (tmp_path / "broken.yaml").write_text("id: [unclosed", encoding="utf-8")

    with pytest.raises(ValueError, match="Malformed YAML"):
        load_adapters(tmp_path)


def test_missing_required_field_raises_clear_error(tmp_path):
    incomplete = {"id": "no_ocsf", "vendor": "V", "product": "P", "format": "json"}
    (tmp_path / "incomplete.yaml").write_text(yaml.safe_dump(incomplete), encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid adapter mapping"):
        load_adapters(tmp_path)


def test_non_mapping_yaml_top_level_raises_clear_error(tmp_path):
    (tmp_path / "list.yaml").write_text(yaml.safe_dump([1, 2, 3]), encoding="utf-8")

    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_adapters(tmp_path)
