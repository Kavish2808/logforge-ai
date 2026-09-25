"""Suggestion providers: untrusted-output parsing, provider selection, and
the Claude provider exercised through a mock HTTP transport (no network)."""
import json

import anthropic
import httpx2
import pytest

from app.config import get_settings
from app.onboarding import providers as prov
from app.onboarding.analysis import analyze_samples
from app.onboarding.providers import (
    AnthropicProvider,
    OfflineHeuristicProvider,
    SuggestionError,
    SuggestionRequest,
    build_offline_proposal,
    get_provider,
    parse_proposal,
)
from tests.unit.onboarding.test_analysis import kv_samples


@pytest.fixture()
def request_():
    samples = kv_samples()
    return SuggestionRequest(samples=samples, analysis=analyze_samples(samples))


def test_parse_proposal_rejects_malformed_output(request_):
    good = build_offline_proposal(request_.analysis)
    for raw, fragment in (
        ("not json at all", "not valid JSON"),
        ("[1, 2, 3]", "must be a JSON object"),
        (json.dumps({**good, "vendor": ""}), "schema validation"),
        ("x" * 70_000, "exceeds"),
    ):
        with pytest.raises(SuggestionError) as err:
            parse_proposal(raw)
        assert err.value.kind == "MALFORMED" and fragment in err.value.message
    assert parse_proposal(json.dumps(good)).vendor == "ACMEFW"


def test_offline_provider_is_deterministic(request_):
    provider = OfflineHeuristicProvider()
    assert provider.suggest(request_) == provider.suggest(request_)
    proposal = parse_proposal(provider.suggest(request_))
    assert proposal.match.field == "vendor" and proposal.match.equals == "ACMEFW"
    assert {m.raw_field: m.target for m in proposal.mappings}["srcip"] == "network.src_ip"


def test_offline_provider_needs_an_identity_field():
    samples = [f"a={i} b={i * 2} c={i * 3}" for i in range(10)]
    with pytest.raises(SuggestionError) as err:
        build_offline_proposal(analyze_samples(samples))
    assert err.value.kind == "INSUFFICIENT_EVIDENCE"


def test_offline_positional_columns_are_assumptions():
    samples = [f"2026-01-18T12:00:{i:02d}Z|ACMEFW|10.0.0.{i}|8.8.8.8|allow" for i in range(10)]
    proposal = parse_proposal(json.dumps(build_offline_proposal(analyze_samples(samples))))
    mapped = {m.raw_field: (m.target, m.confidence) for m in proposal.mappings}
    assert mapped["col_3"] == ("network.src_ip", 0.5) and mapped["col_4"] == ("network.dst_ip", 0.5)
    assert any("assumed source" in a for a in proposal.assumptions)


def test_provider_selection(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "anthropic_api_key", "")
    assert isinstance(get_provider("auto"), OfflineHeuristicProvider)
    assert isinstance(get_provider("offline"), OfflineHeuristicProvider)
    with pytest.raises(SuggestionError) as err:
        get_provider("anthropic")
    assert err.value.kind == "UNAVAILABLE"
    monkeypatch.setattr(s, "anthropic_api_key", "sk-test")
    assert isinstance(get_provider("auto"), AnthropicProvider)
    monkeypatch.setattr(s, "llm_provider", "offline")
    assert isinstance(get_provider("auto"), OfflineHeuristicProvider)


# --- Claude provider through a mock transport -------------------------------------------------


def _mock_provider(handler) -> AnthropicProvider:
    client = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    # max_retries=0: the SDK would otherwise back off and retry 429/5xx
    return AnthropicProvider("sk-test", "claude-opus-5", 30, http_client=client, max_retries=0)


def _message(text: str, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5",
        "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def test_claude_request_shape_and_success(request_):
    seen = {}
    good = json.dumps(build_offline_proposal(request_.analysis))

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(req.url)
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.content)
        return httpx2.Response(200, json=_message(good))

    out = _mock_provider(handler).suggest(request_)
    assert parse_proposal(out).vendor == "ACMEFW"
    body = seen["body"]
    assert seen["url"].endswith("/v1/messages?beta=true") or seen["url"].endswith("/v1/messages")
    assert body["model"] == "claude-opus-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["headers"].get("anthropic-beta", "")
    assert "untrusted data" in body["system"]
    assert '<sample index="0">' in body["messages"][0]["content"]


@pytest.mark.parametrize(
    "status,kind",
    [(401, "UNAVAILABLE"), (429, "UNAVAILABLE"), (500, "UNAVAILABLE"), (400, "UNAVAILABLE")],
)
def test_claude_http_errors_fail_safely(request_, status, kind):
    def handler(req):
        return httpx2.Response(status, json={"type": "error", "error": {"type": "api_error", "message": "x"}})

    with pytest.raises(SuggestionError) as err:
        _mock_provider(handler).suggest(request_)
    assert err.value.kind == kind


def test_claude_connection_error(request_):
    def handler(req):
        raise httpx2.ConnectError("boom", request=req)

    with pytest.raises(SuggestionError) as err:
        _mock_provider(handler).suggest(request_)
    assert err.value.kind == "UNAVAILABLE"


@pytest.mark.parametrize("stop_reason,kind", [("refusal", "REFUSED"), ("max_tokens", "MALFORMED")])
def test_claude_stop_reasons(request_, stop_reason, kind):
    def handler(req):
        return httpx2.Response(200, json=_message("{}", stop_reason=stop_reason))

    with pytest.raises(SuggestionError) as err:
        _mock_provider(handler).suggest(request_)
    assert err.value.kind == kind


def test_claude_malformed_json_is_rejected_not_executed(request_):
    payload = '{"vendor": "x", "code": "__import__(\'os\').system(\'touch /tmp/pwned\')"}'

    def handler(req):
        return httpx2.Response(200, json=_message(payload))

    out = _mock_provider(handler).suggest(request_)
    with pytest.raises(SuggestionError) as err:
        parse_proposal(out)
    assert err.value.kind == "MALFORMED"


def test_prompt_bounds(request_):
    many = SuggestionRequest(samples=["x" * 5000] * 30, analysis={})
    text = prov._user_prompt(many)
    assert text.count("<sample ") == prov.MAX_PROMPT_SAMPLES
    assert 'omitted="15"' in text and "[truncated]" in text
