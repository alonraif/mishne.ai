"""Routing, parsing, splitting and cost for the managed transcription engines.

Written against recorded response shapes rather than a live vendor: what these
tests protect is our reading of the contract — that Hebrew never reaches an
engine that does not publish it, that a word array missing is an error rather
than an empty transcript, that a split file's timestamps come back on the source
clock, and that an estimated price never reads as a measured one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mishne.asr import catalog, chunking, routing
from mishne.asr.base import ASRError, ASRResult, Word
from mishne.asr.gemini_provider import GeminiProvider
from mishne.asr.xai_provider import XAIProvider

BOTH = ["xai", "google"]


def wav(path: Path, seconds: float = 1.0) -> Path:
    """A real 16 kHz mono WAV — the shape `pipeline/steps/audio.py` writes.

    Real rather than a stub because the Gemini path measures the file with
    ffprobe before deciding whether it has to be split, and a test that skips
    that skips the decision it is checking.
    """
    import wave

    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16_000)
        f.writeframes(b"\0" * int(16_000 * 2 * seconds))
    return path


# ── keys reach the vendors ────────────────────────────────────────────────


def test_a_key_in_dotenv_reaches_the_process_environment(tmp_path, monkeypatch):
    """`Settings` reads .env and does not export it, and every vendor adapter
    reads os.environ. A key in the file used to reach neither the router nor
    an error message — the run just quietly did what it does without one."""
    from mishne.config import load_env_file

    env = tmp_path / ".env"
    env.write_text('XAI_API_KEY=from-file\nGEMINI_API_KEY="quoted"\n# note\nBLANK=\n')
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("BLANK", raising=False)

    loaded = load_env_file(env)

    import os

    assert set(loaded) == {"XAI_API_KEY", "GEMINI_API_KEY"}
    assert os.environ["XAI_API_KEY"] == "from-file"
    assert os.environ["GEMINI_API_KEY"] == "quoted"
    # An empty assignment is not a key, and setting it to "" would make
    # `ProviderConfig.available` and the router disagree about nothing.
    assert "BLANK" not in os.environ
    assert routing.available_providers() == ["xai", "google"]


def test_the_shell_wins_over_the_file(tmp_path, monkeypatch):
    """Exporting a key is a deliberate override — and in staging there is no
    file at all."""
    from mishne.config import load_env_file

    env = tmp_path / ".env"
    env.write_text("XAI_API_KEY=from-file\n")
    monkeypatch.setenv("XAI_API_KEY", "from-shell")

    assert load_env_file(env) == []

    import os

    assert os.environ["XAI_API_KEY"] == "from-shell"


# ── the catalog ────────────────────────────────────────────────────────────

def test_engines_json_is_loadable_and_dated():
    engines = catalog.load()
    assert {e.key for e in engines} == {
        "xai/grok-stt", "google/gemini-3.5-transcribe"}
    assert catalog.verified_on(), "prices must carry the date they were checked"


def test_hebrew_is_not_in_xais_published_languages():
    xai = catalog.find("grok-stt", "xai")
    assert not xai.speaks("he")
    assert not xai.speaks("he-IL")
    assert xai.speaks("en") and xai.speaks("pt-BR")


def test_an_unidentified_language_is_not_english():
    """The failure this prevents is silent: a fluent transcript of the wrong
    words, from an engine that never claimed to speak what it was given."""
    assert not catalog.find("grok-stt", "xai").speaks(None)
    assert catalog.find("gemini-3.5-transcribe", "google").speaks(None)


def test_a_flat_hourly_rate_is_measured_and_a_token_guess_is_not():
    xai = catalog.find("grok-stt", "xai")
    gemini = catalog.find("gemini-3.5-transcribe", "google")

    measured = xai.cost_for(3600.0)
    assert measured.usd == pytest.approx(0.10) and not measured.estimated

    reported = gemini.cost_for(3600.0, audio_tokens=90_000, text_tokens=10_500)
    assert not reported.estimated

    assumed = gemini.cost_for(3600.0)
    assert assumed.estimated, "no usage counts means the number is a guess"


def test_an_unpriced_engine_sorts_last_rather_than_free():
    unknown = catalog.find("some-new-engine", "vendor")
    assert unknown.cost_for(3600.0).usd is None
    assert unknown.rank_cost() == float("inf")


# ── routing ────────────────────────────────────────────────────────────────

def test_english_takes_the_cheap_engine_and_hebrew_the_one_that_speaks_it():
    assert [e.key for e in routing.plan("en", have=BOTH)][0] == "xai/grok-stt"
    assert [e.key for e in routing.plan("he", have=BOTH)] == [
        "google/gemini-3.5-transcribe"]


def test_hebrew_has_nowhere_to_go_without_a_gemini_key(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "x")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert routing.plan("he", have=["xai"]) == []

    with pytest.raises(ASRError) as exc:
        routing.RoutedASR().transcribe(Path("nope.wav"), language="he")
    # The message has to name the missing key. "no engine available" sends
    # somebody to read the router; this sends them to their .env.
    assert "GEMINI_API_KEY" in str(exc.value)


def test_xais_any_language_claim_is_opt_in(monkeypatch):
    monkeypatch.setenv("MISHNE_ASR_XAI_ANY_LANGUAGE", "1")
    assert [e.key for e in routing.plan("he", have=BOTH)][0] == "xai/grok-stt"


def test_an_engine_can_be_pinned(monkeypatch):
    monkeypatch.setenv("MISHNE_ASR_ENGINE", "google/gemini-3.5-transcribe")
    assert [e.key for e in routing.plan("en", have=BOTH)] == [
        "google/gemini-3.5-transcribe"]


# ── xAI ────────────────────────────────────────────────────────────────────

XAI_RESPONSE = {
    "text": "so um we shot it twice",
    "language": "en",
    "duration": 1800.0,
    "words": [
        {"text": "so", "start": 0.10, "end": 0.28, "speaker": 0},
        {"text": "um", "start": 0.31, "end": 0.55, "speaker": 0},
        {"text": "we", "start": 0.60, "end": 0.74, "speaker": 1},
    ],
}


def _xai(monkeypatch, response, capture=None):
    provider = XAIProvider(api_key="k")

    def fake(url, headers, fields, file_field, file_path, timeout=0):
        if capture is not None:
            capture.update({"url": url, "fields": fields})
        return response, 4321

    monkeypatch.setattr("mishne.asr.xai_provider.post_multipart", fake)
    return provider


def test_xai_asks_for_verbatim_output(monkeypatch, tmp_path):
    """Filler words on and formatting off are the two fields the product
    depends on, and both default the wrong way at the vendor."""
    seen: dict = {}
    provider = _xai(monkeypatch, XAI_RESPONSE, seen)
    audio = wav(tmp_path / "a.wav")

    provider.transcribe(audio, language="en-GB")

    assert seen["fields"]["filler_words"] is True
    assert seen["fields"]["format"] is False
    assert seen["fields"]["diarize"] is True
    assert seen["fields"]["language"] == "en"


def test_xai_words_carry_millisecond_times_speakers_and_a_measured_cost(
        monkeypatch, tmp_path):
    provider = _xai(monkeypatch, XAI_RESPONSE)
    audio = wav(tmp_path / "a.wav")

    result = provider.transcribe(audio, language="en")

    assert [w.text for w in result.words] == ["so", "um", "we"]
    assert result.words[0].start_ms == 100 and result.words[0].end_ms == 280
    assert result.words[2].speaker == "spk_1"
    assert result.audio_seconds == 1800.0
    assert result.cost_usd == pytest.approx(0.05)      # half an hour at $0.10/h
    assert result.priced and not result.cost_estimated
    assert result.usd_per_source_hour == pytest.approx(0.10)


def test_a_response_without_word_timestamps_is_an_error_not_a_transcript(
        monkeypatch, tmp_path):
    """An empty word list reads downstream as silent audio, which is a
    plausible and completely wrong thing for the pipeline to believe."""
    provider = _xai(monkeypatch, {"text": "hello", "duration": 12.0})
    audio = wav(tmp_path / "a.wav")

    with pytest.raises(ASRError):
        provider.transcribe(audio, language="en")


# ── Gemini ─────────────────────────────────────────────────────────────────

GEMINI_RESPONSE = {
    "id": "interactions/abc",
    "status": "completed",
    "steps": [{"content": [{
        "type": "text",
        "text": "אז צילמנו",
        "annotations": [
            {"type": "word_info", "text": "אז", "speaker": "spk_1",
             "start_offset": "0.100s", "end_offset": "0.450s"},
            {"type": "word_info", "text": "צילמנו", "speaker": "spk_1",
             "start_offset": "0.470s", "end_offset": "1.020s"},
            {"type": "other", "text": "ignored"},
        ],
    }]}],
    # The shape a real response carries, from a recorded run: input broken down
    # by modality, output explicitly zero, and a cached count.
    "usage": {
        "total_tokens": 90_001,
        "total_input_tokens": 90_001,
        "input_tokens_by_modality": [
            {"modality": "audio", "tokens": 90_000},
            {"modality": "text", "tokens": 1},
        ],
        "total_cached_tokens": 0,
        "cached_tokens_by_modality": [{"modality": "audio", "tokens": 0}],
        "total_output_tokens": 0,
        "total_tool_use_tokens": 0,
        "total_thought_tokens": 0,
        "audio_seconds": 3600.0,
    },
}


def _gemini(monkeypatch, response, capture=None):
    provider = GeminiProvider(api_key="k")
    monkeypatch.setattr(
        "mishne.asr.gemini_provider.post_capture_headers",
        lambda url, headers, body, timeout=0: (
            {"file": {"uri": "files/x", "name": "files/x"}},
            {"x-goog-upload-url": "https://upload"}, 10))
    deleted: list = []
    monkeypatch.setattr("mishne.asr.gemini_provider.delete",
                        lambda url, headers: deleted.append(url))

    def fake_post(url, headers, body, timeout=0):
        if capture is not None:
            capture.update({"body": body, "deleted": deleted})
        return response, 9999

    monkeypatch.setattr("mishne.asr.gemini_provider.post_json", fake_post)
    provider._deleted = deleted
    return provider


def test_gemini_requests_verbatim_word_timestamps_and_diarization(
        monkeypatch, tmp_path):
    seen: dict = {}
    provider = _gemini(monkeypatch, GEMINI_RESPONSE, seen)
    audio = wav(tmp_path / "he.wav")

    provider.transcribe(audio, language="he-IL")

    mode = seen["body"]["generation_config"]["transcription_config"]["mode"]
    assert mode["type"] == "verbatim"
    assert mode["timestamp_granularities"] == ["word"]
    assert mode["diarization_mode"] == "speaker"


def test_gemini_parses_protobuf_durations_and_reports_a_measured_cost(
        monkeypatch, tmp_path):
    provider = _gemini(monkeypatch, GEMINI_RESPONSE)
    audio = wav(tmp_path / "he.wav")

    result = provider.transcribe(audio, language="he")

    assert [w.text for w in result.words] == ["אז", "צילמנו"]
    assert result.words[0].start_ms == 100 and result.words[0].end_ms == 450
    assert result.words[1].speaker == "spk_1"
    assert not result.cost_estimated, "the vendor reported usage"
    # 90,000 audio tokens at $2/M, and nothing for the transcript: the vendor
    # reports total_output_tokens as zero on a transcription.
    assert result.cost_usd == pytest.approx(0.180)


@pytest.mark.parametrize("shape", [
    {"usage": {"total_input_tokens": 90_000, "total_output_tokens": 0}},
    {"usageMetadata": {"audioTokenCount": 90_000, "outputTokenCount": 0}},
    {"usage_metadata": {"promptTokenCount": 90_000, "output_tokens": 0}},
])
def test_usage_is_read_whatever_the_vendor_calls_it(monkeypatch, tmp_path, shape):
    """The endpoint's documentation describes none of this, and a model days
    old may rename it. Picking one spelling means a real cost arrives under
    another name and quietly reads as an estimate — the exact confusion
    migration 0006 added a column to keep out of the billing data."""
    response = {k: v for k, v in GEMINI_RESPONSE.items() if k != "usage"}
    response.update(shape)
    provider = _gemini(monkeypatch, response)

    result = provider.transcribe(wav(tmp_path / "he.wav"), language="he")

    assert not result.cost_estimated
    assert result.cost_usd == pytest.approx(0.180)


def test_the_prompt_token_is_not_priced_as_audio(monkeypatch, tmp_path):
    """Input arrives split by modality — audio, and a single text token for the
    prompt. Summing them prices text at the audio rate, which is wrong in a way
    no total would reveal."""
    provider = _gemini(monkeypatch, GEMINI_RESPONSE)

    result = provider.transcribe(wav(tmp_path / "he.wav"), language="he")

    # 90,000 audio tokens, not the 90,001 the total reports.
    assert result.cost_usd == pytest.approx(90_000 * 2.00 / 1_000_000)


def test_a_transcript_billed_as_output_would_be_charged_for(monkeypatch, tmp_path):
    """Today the vendor reports zero output tokens for a transcription, which
    is why the catalog's estimate assumes none. If that ever changes, the cost
    has to follow the report rather than the assumption."""
    response = dict(GEMINI_RESPONSE)
    response["usage"] = {**GEMINI_RESPONSE["usage"], "total_output_tokens": 10_500}
    provider = _gemini(monkeypatch, response)

    result = provider.transcribe(wav(tmp_path / "he.wav"), language="he")

    assert result.cost_usd == pytest.approx(0.180 + 10_500 * 12.00 / 1_000_000)


def test_gemini_falls_back_to_its_published_rates_and_says_it_estimated(
        monkeypatch, tmp_path):
    response = {**GEMINI_RESPONSE}
    response.pop("usage")
    provider = _gemini(monkeypatch, response)
    audio = wav(tmp_path / "he.wav", seconds=1.0)

    result = provider.transcribe(audio, language="he")

    assert result.cost_estimated is True
    assert result.priced and result.cost_usd > 0


def test_the_uploaded_audio_is_deleted_from_the_vendor(monkeypatch, tmp_path):
    """Customer media on a third party's disk is the negative consequence
    ADR-0003 named. It leaves when the transcript arrives, not in 48 hours."""
    provider = _gemini(monkeypatch, GEMINI_RESPONSE)
    audio = wav(tmp_path / "he.wav")

    provider.transcribe(audio, language="he")

    assert provider._deleted, "the Files API upload was left behind"


def test_the_upload_is_deleted_even_when_transcription_fails(
        monkeypatch, tmp_path):
    provider = _gemini(monkeypatch, {})
    audio = wav(tmp_path / "he.wav")

    with pytest.raises(ASRError):
        provider.transcribe(audio, language="he")
    assert provider._deleted


# ── splitting ──────────────────────────────────────────────────────────────

def test_short_audio_is_one_chunk():
    assert chunking.plan(600.0, 1800.0, []) == [chunking.Chunk(0, 0.0, 600.0)]


def test_a_long_file_splits_in_the_middle_of_the_last_pause():
    silences = [(500.0, 502.0), (1740.0, 1746.0), (1900.0, 1901.0)]
    chunks = chunking.plan(3000.0, 1800.0, silences)
    assert len(chunks) == 2
    assert chunks[0].end_s == pytest.approx(1743.0)   # middle of the pause
    assert chunks[1].start_s == pytest.approx(1743.0)
    assert chunks[1].end_s == 3000.0


def test_continuous_speech_still_splits_rather_than_refusing():
    chunks = chunking.plan(4000.0, 1800.0, [])
    assert [round(c.end_s) for c in chunks] == [1800, 3600, 4000]


def test_a_pause_too_early_to_help_is_ignored():
    """A silence at 0:30 is not a sensible place to cut a 30-minute limit —
    it would halve every chunk and double the number of requests."""
    chunks = chunking.plan(3000.0, 1800.0, [(30.0, 34.0)])
    assert chunks[0].end_s == pytest.approx(1800.0)


def _result(words, seconds, cost, provider="google", estimated=False):
    return ASRResult(words=words, language="he", provider=provider,
                     model="m", audio_seconds=seconds, cost_usd=cost,
                     cost_estimated=estimated)


def test_merging_puts_timestamps_back_on_the_source_clock():
    chunks = [chunking.Chunk(0, 0.0, 1743.0), chunking.Chunk(1, 1743.0, 3000.0)]
    merged = chunking.merge([
        _result([Word("a", 100, 400, speaker="spk_1")], 1743.0, 0.15),
        _result([Word("b", 200, 500, speaker="spk_1")], 1257.0, 0.10),
    ], chunks)

    assert [w.start_ms for w in merged.words] == [100, 1743_200]
    assert merged.audio_seconds == pytest.approx(3000.0)
    assert merged.cost_usd == pytest.approx(0.25)
    assert merged.chunks == 2


def test_speakers_are_not_merged_across_a_seam():
    """`spk_1` in chunk two is a different person's label, not the same
    person: the diarizer never heard the two halves together."""
    chunks = [chunking.Chunk(0, 0.0, 1800.0), chunking.Chunk(1, 1800.0, 3000.0)]
    merged = chunking.merge([
        _result([Word("a", 0, 100, speaker="spk_1")], 1800.0, 0.15),
        _result([Word("b", 0, 100, speaker="spk_1")], 1200.0, 0.10),
    ], chunks)

    assert merged.words[0].speaker != merged.words[1].speaker


def test_one_estimated_chunk_makes_the_whole_transcript_estimated():
    chunks = [chunking.Chunk(0, 0.0, 1800.0), chunking.Chunk(1, 1800.0, 3000.0)]
    merged = chunking.merge([
        _result([Word("a", 0, 100)], 1800.0, 0.15),
        _result([Word("b", 0, 100)], 1200.0, 0.10, estimated=True),
    ], chunks)
    assert merged.cost_estimated


# ── the router's ledger and failover ───────────────────────────────────────

class _Fake:
    """A provider that fails, or succeeds, on command."""

    def __init__(self, name, error=None, seconds=60.0, cost=0.01):
        self.name = name
        self.error = error
        self.seconds = seconds
        self.cost = cost

    def transcribe(self, audio, *, language=None, diarize=True):
        if self.error:
            raise self.error
        return ASRResult(words=[Word("hi", 0, 100)], language=language or "en",
                         provider=self.name, model="m",
                         audio_seconds=self.seconds, cost_usd=self.cost,
                         latency_ms=7)


def _route(monkeypatch, providers: dict):
    monkeypatch.setattr("mishne.asr.base.get_provider",
                        lambda name, **kw: providers[name])
    from mishne.llm.base import Ledger
    return routing.RoutedASR(ledger=Ledger())


def test_a_retryable_failure_moves_to_the_other_vendor(monkeypatch, tmp_path):
    monkeypatch.setenv("XAI_API_KEY", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    router = _route(monkeypatch, {
        "xai": _Fake("xai", ASRError("503", retryable=True)),
        "google": _Fake("google"),
    })

    result = router.transcribe(tmp_path / "a.wav", language="en")

    assert result.provider == "google"
    records = router.ledger.calls
    assert [c.ok for c in records] == [False, True]
    assert records[1].fell_back_from == "xai/grok-stt"
    assert all(c.task == "transcribe" for c in records)


def test_a_bad_request_is_not_retried_at_the_other_vendor(monkeypatch,
                                                          tmp_path):
    """It fails identically there, and the audio is an hour long at both."""
    monkeypatch.setenv("XAI_API_KEY", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    called: list = []

    class Watch(_Fake):
        def transcribe(self, audio, *, language=None, diarize=True):
            called.append(self.name)
            return super().transcribe(audio, language=language)

    router = _route(monkeypatch, {
        "xai": _Fake("xai", ASRError("400", retryable=False)),
        "google": Watch("google"),
    })

    with pytest.raises(ASRError):
        router.transcribe(tmp_path / "a.wav", language="en")
    assert called == []


def test_the_engine_call_is_recorded_with_its_audio_duration(monkeypatch,
                                                              tmp_path):
    """Without the duration an ASR row says a job spent nine cents and gives
    nothing to divide it by — which is the whole point of recording it."""
    monkeypatch.setenv("XAI_API_KEY", "x")
    router = _route(monkeypatch, {"xai": _Fake("xai", seconds=1543.0,
                                               cost=0.0428)})

    router.transcribe(tmp_path / "a.wav", language="en")

    call = router.ledger.calls[0]
    assert call.audio_seconds == 1543.0
    assert call.cost_usd == pytest.approx(0.0428)
    assert call.priced and not call.cost_estimated


def test_a_cached_transcript_is_not_billed_again():
    """Reading a stored transcript back must not re-charge for the run that
    produced it — that cache is what ADR-0008 exists for."""
    stored = json.loads(json.dumps(ASRResult(
        words=[Word("a", 0, 100)], language="he", provider="google",
        model="gemini-3.5-transcribe", audio_seconds=1800.0,
        cost_usd=0.15).to_dict()))

    back = ASRResult.from_dict(stored)

    assert back.cost_usd == 0.0
    assert back.audio_seconds == 1800.0, "duration survives; the charge does not"
