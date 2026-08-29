"""Model routing: which vendor runs which stage, and what gets recorded.

None of this needs an API key. Routing is a pure function of the catalog, the
keys present, and the policy — which is the point: two runs with the same
configuration must choose the same model, or the reproducibility record means
nothing.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.llm import Router  # noqa: E402
from mishne.llm import catalog, providers, router as router_mod  # noqa: E402
from mishne.llm.base import CallRecord, Completion, LLMError  # noqa: E402

ALL_KEYS = {"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o",
            "GEMINI_API_KEY": "g", "XAI_API_KEY": "x"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Routing must not depend on what the developer happens to have exported."""
    for var in list(ALL_KEYS) + ["MISHNE_MODEL_CATALOG"]:
        monkeypatch.delenv(var, raising=False)
    for task in ("BRIEF", "SPANS", "SCORE"):
        monkeypatch.delenv(f"MISHNE_MODEL_{task}", raising=False)
        monkeypatch.delenv(f"MISHNE_POLICY_{task}", raising=False)


def keys(monkeypatch, *names):
    for n in names:
        monkeypatch.setenv(n, ALL_KEYS[n])


# --- the catalog -------------------------------------------------------------


def test_the_shipped_catalog_parses_and_is_priced():
    models = catalog.load()
    assert models, "catalog is empty"
    for m in models:
        assert m.provider in providers.PROVIDERS, m.id
        assert m.tier in catalog.TIERS, m.id
        assert m.priced, f"{m.id} has no price"
        assert m.price_out >= m.price_in, m.id


def test_the_catalog_can_be_replaced_without_a_release(tmp_path, monkeypatch):
    """The whole reason it is data: vendors ship new models constantly."""
    custom = tmp_path / "models.json"
    custom.write_text(json.dumps({"models": [
        {"id": "some-model-not-invented-yet", "provider": "openai",
         "tier": "frontier", "in": 1.0, "out": 2.0}]}))
    monkeypatch.setenv("MISHNE_MODEL_CATALOG", str(custom))
    assert [m.id for m in catalog.load()] == ["some-model-not-invented-yet"]


def test_an_uncatalogued_model_is_usable_but_unpriced():
    m = catalog.find("a-model-shipped-yesterday", "xai")
    assert m.provider == "xai"
    assert not m.priced
    assert m.cost_for(1000, 1000) is None
    # And it must never sort as the cheapest thing available.
    assert m.blended_cost(1000, 1000) == float("inf")


# --- choosing ----------------------------------------------------------------


def test_no_keys_means_no_models():
    assert Router().plan("score") == []
    assert not Router().available_for("score")


def test_only_vendors_with_a_key_are_considered(monkeypatch):
    keys(monkeypatch, "GEMINI_API_KEY")
    assert {m.provider for m in Router().plan("score")} == {"google"}


def test_cost_policy_picks_the_cheapest_for_the_task(monkeypatch):
    keys(monkeypatch, *ALL_KEYS)
    task = router_mod.TASKS["score"]
    plan = Router(policy="cost").plan("score")
    cheapest = min(plan, key=lambda m: m.blended_cost(task.est_input,
                                                      task.est_output))
    assert plan[0] is cheapest


def test_quality_policy_picks_the_top_tier(monkeypatch):
    keys(monkeypatch, *ALL_KEYS)
    assert Router(policy="quality").plan("score")[0].tier == "frontier"


def test_a_task_floor_is_never_crossed(monkeypatch):
    """`spans` needs judgement; the cheap tier must not win on price."""
    keys(monkeypatch, *ALL_KEYS)
    for policy in ("quality", "balanced", "cost"):
        for m in Router(policy=policy).plan("spans"):
            assert m.tier != "fast", f"{policy} offered {m.id} for spans"


def test_the_cheap_tier_is_allowed_for_the_brief(monkeypatch):
    """Parsing a sentence into a duration does not need a frontier model."""
    keys(monkeypatch, *ALL_KEYS)
    assert Router(policy="cost").plan("brief")[0].tier == "fast"


def test_balanced_does_not_silently_downgrade_a_demanding_task(monkeypatch):
    """A task asking for frontier should get it, not the tenth-of-the-price one."""
    keys(monkeypatch, *ALL_KEYS)
    assert Router(policy="balanced").plan("spans")[0].tier == "frontier"


def test_routing_is_deterministic(monkeypatch):
    keys(monkeypatch, *ALL_KEYS)
    a = [m.id for m in Router(policy="balanced").plan("score")]
    b = [m.id for m in Router(policy="balanced").plan("score")]
    assert a == b


def test_a_task_can_override_the_run_policy(monkeypatch):
    keys(monkeypatch, *ALL_KEYS)
    monkeypatch.setenv("MISHNE_POLICY_SPANS", "cost")
    r = Router(policy="quality")
    assert r.plan("score")[0].tier == "frontier"
    task = router_mod.TASKS["spans"]
    plan = r.plan("spans")
    assert plan[0] is min(plan, key=lambda m: m.blended_cost(task.est_input,
                                                             task.est_output))


def test_pinning_a_model_skips_routing_entirely(monkeypatch):
    keys(monkeypatch, *ALL_KEYS)
    monkeypatch.setenv("MISHNE_MODEL_SPANS", "xai/grok-4.6")
    plan = Router(policy="cost").plan("spans")
    assert len(plan) == 1
    assert (plan[0].provider, plan[0].id) == ("xai", "grok-4.6")


# --- calling, failing over, recording ----------------------------------------


class Stub:
    """Stands in for a vendor. Fails a chosen number of times, then answers."""

    def __init__(self, name, fail=0, retryable=True, text='{"ok":true}'):
        self.name = name
        self.fail = fail
        self.retryable = retryable
        self.text = text
        self.calls = 0

    def complete(self, *, model, system, user, max_tokens=4096,
                 temperature=0.0):
        self.calls += 1
        if self.fail > 0:
            self.fail -= 1
            raise LLMError(f"{self.name} is down", retryable=self.retryable)
        return Completion(text=self.text, model=model, provider=self.name,
                          input_tokens=1000, output_tokens=500, latency_ms=42)


def stub_providers(monkeypatch, **by_name):
    def get(name):
        if name not in by_name:
            raise LLMError(f"{name}: no key", retryable=False)
        return by_name[name]
    monkeypatch.setattr(providers, "get", get)
    monkeypatch.setattr(router_mod.providers, "get", get)


def test_a_successful_call_records_what_it_cost(monkeypatch):
    keys(monkeypatch, "ANTHROPIC_API_KEY")
    stub_providers(monkeypatch, anthropic=Stub("anthropic"))
    r = Router(policy="quality")
    out = r.complete("score", system="s", user="u")

    assert out.json() == {"ok": True}
    rec = r.ledger.calls[0]
    assert rec.ok and rec.provider == "anthropic"
    assert rec.input_tokens == 1000 and rec.output_tokens == 500
    assert rec.cost_usd > 0, "a priced model must record a cost"
    assert r.ledger.cost_usd == rec.cost_usd


def test_failover_crosses_vendors_and_records_both(monkeypatch):
    """An outage at one vendor should cost a slower job, not a failed one."""
    keys(monkeypatch, *ALL_KEYS)
    r = Router(policy="quality")
    first = r.plan("score")[0]
    stubs = {p: Stub(p) for p in ("anthropic", "openai", "google", "xai")}
    stubs[first.provider] = Stub(first.provider, fail=1)
    stub_providers(monkeypatch, **stubs)

    r.complete("score", system="s", user="u")

    assert [c.ok for c in r.ledger.calls] == [False, True]
    winner = r.ledger.calls[-1]
    assert winner.provider != first.provider
    assert winner.fell_back_from == f"{first.provider}/{first.id}"


def test_the_reproducibility_record_names_every_model_that_ran(monkeypatch):
    keys(monkeypatch, *ALL_KEYS)
    r = Router(policy="quality")
    first = r.plan("score")[0]
    stubs = {p: Stub(p) for p in ("anthropic", "openai", "google", "xai")}
    stubs[first.provider] = Stub(first.provider, fail=1)
    stub_providers(monkeypatch, **stubs)

    r.complete("score", system="s", user="u")
    used = r.ledger.models_used()["score"]
    assert len(used) == 1, "only models that actually produced output"
    assert "/" in used[0]


def test_a_non_retryable_failure_stops_the_chain(monkeypatch):
    """A bad model id fails identically everywhere; walking three keys to find
    that out wastes the operator's money and buries the real error."""
    keys(monkeypatch, *ALL_KEYS)
    r = Router(policy="quality")
    first = r.plan("score")[0]
    stubs = {p: Stub(p) for p in ("anthropic", "openai", "google", "xai")}
    stubs[first.provider] = Stub(first.provider, fail=1, retryable=False)
    stub_providers(monkeypatch, **stubs)

    with pytest.raises(LLMError):
        r.complete("score", system="s", user="u")
    assert len(r.ledger.calls) == 1


def test_no_key_at_all_says_how_to_run_offline(monkeypatch):
    with pytest.raises(LLMError) as exc:
        Router().complete("score", system="s", user="u")
    assert "--scorer heuristic" in str(exc.value)


def test_unparseable_json_is_an_error_worth_counting(monkeypatch):
    keys(monkeypatch, "ANTHROPIC_API_KEY")
    stub_providers(monkeypatch,
                   anthropic=Stub("anthropic", text="sorry, I cannot"))
    out = Router(policy="quality").complete("score", system="s", user="u")
    with pytest.raises(LLMError):
        out.json()


def test_json_survives_a_fenced_block_and_a_preamble():
    c = Completion(text='Here you go:\n```json\n[{"id":"a"}]\n```',
                   model="m", provider="p")
    assert c.json() == [{"id": "a"}]


def test_constraint_violations_attach_to_the_call(monkeypatch):
    """The one quality signal available without a corpus."""
    keys(monkeypatch, "ANTHROPIC_API_KEY")
    stub_providers(monkeypatch, anthropic=Stub("anthropic"))
    r = Router(policy="quality")
    r.complete("spans", system="s", user="u")
    r.note_violations("spans", violations=3, proposals=8)

    rec = r.ledger.calls[-1]
    assert (rec.violations, rec.proposals) == (3, 8)
    assert "3/8 proposals refused" in " ".join(r.ledger.summary())


def test_an_unpriced_model_records_no_cost_rather_than_zero(monkeypatch):
    keys(monkeypatch, "XAI_API_KEY")
    monkeypatch.setenv("MISHNE_MODEL_SCORE", "xai/not-in-the-catalog")
    stub_providers(monkeypatch, xai=Stub("xai"))
    r = Router()
    r.complete("score", system="s", user="u")
    rec = r.ledger.calls[0]
    assert rec.ok and rec.cost_usd == 0.0
    assert "cost_usd" not in rec.to_dict(), "unknown cost must not read as free"


# --- the wire format ---------------------------------------------------------
#
# No live call is made here. What these check is the shape of the request each
# vendor gets and the parsing of what comes back — the two places a hand-rolled
# HTTP client goes wrong, and the reason not to trust "it compiled".


def fake_http(monkeypatch, response: dict, captured: dict):
    import urllib.request

    class Resp:
        def read(self):
            return json.dumps(response).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        captured["body"] = json.loads(req.data.decode())
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


def test_anthropic_request_shape_and_parsing(monkeypatch):
    """System is a top-level field, not a message, and max_tokens is required."""
    keys(monkeypatch, "ANTHROPIC_API_KEY")
    cap = {}
    fake_http(monkeypatch, {
        "model": "claude-opus-5",
        "content": [{"type": "thinking", "thinking": "hmm"},
                    {"type": "text", "text": "[1]"}],
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }, cap)

    out = providers.get("anthropic").complete(
        model="claude-opus-5", system="SYS", user="USR", max_tokens=99)

    assert cap["url"].endswith("/v1/messages")
    assert cap["headers"]["x-api-key"] == "a"
    assert cap["headers"]["anthropic-version"] == "2023-06-01"
    assert cap["body"]["system"] == "SYS"
    assert cap["body"]["messages"] == [{"role": "user", "content": "USR"}]
    assert cap["body"]["max_tokens"] == 99
    # Reasoning blocks come before the text one; taking block zero would return
    # the thinking rather than the answer.
    assert out.text == "[1]"
    assert (out.input_tokens, out.output_tokens) == (11, 7)


@pytest.mark.parametrize("provider,env,host", [
    ("openai", "OPENAI_API_KEY", "api.openai.com"),
    ("xai", "XAI_API_KEY", "api.x.ai"),
    ("google", "GEMINI_API_KEY", "generativelanguage.googleapis.com"),
])
def test_openai_compatible_request_shape(monkeypatch, provider, env, host):
    keys(monkeypatch, env)
    cap = {}
    fake_http(monkeypatch, {
        "model": "m",
        "choices": [{"message": {"content": "[2]"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
    }, cap)

    out = providers.get(provider).complete(model="m", system="SYS", user="USR")

    assert host in cap["url"] and cap["url"].endswith("/chat/completions")
    assert cap["headers"]["authorization"].startswith("Bearer ")
    assert cap["body"]["messages"][0] == {"role": "system", "content": "SYS"}
    # Reasoning models reject an explicit temperature; the default must not be
    # sent at all rather than sent as zero.
    assert "temperature" not in cap["body"]
    assert out.text == "[2]"
    assert (out.input_tokens, out.output_tokens) == (3, 4)


def test_a_client_error_is_not_retried_elsewhere(monkeypatch):
    """A bad model id fails the same way at every vendor."""
    import urllib.error
    import urllib.request
    keys(monkeypatch, "OPENAI_API_KEY")

    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                     io.BytesIO(b"nope"))
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    with pytest.raises(LLMError) as exc:
        providers.get("openai").complete(model="m", system="s", user="u")
    assert exc.value.retryable is False


def test_rate_limiting_and_server_errors_are_retryable(monkeypatch):
    import urllib.error
    import urllib.request
    keys(monkeypatch, "OPENAI_API_KEY")

    for code in (429, 500, 529):
        def urlopen(req, timeout=None, code=code):
            raise urllib.error.HTTPError(req.full_url, code, "x", {},
                                         io.BytesIO(b"busy"))
        monkeypatch.setattr(urllib.request, "urlopen", urlopen)
        with pytest.raises(LLMError) as exc:
            providers.get("openai").complete(model="m", system="s", user="u")
        assert exc.value.retryable is True, code


def test_a_missing_key_is_not_a_retryable_failure(monkeypatch):
    with pytest.raises(LLMError) as exc:
        providers.get("openai")
    assert exc.value.retryable is False
