"""Stage 0 on arrival: what the asset row knows before a job exists.

The credit estimate is a function of duration and the user approves it before
anything runs, so an asset whose length is unknown cannot be quoted. These tests
drive the whole path — object in a bucket, probe, row updated — with the two
readers of the file itself (`ffprobe` and pyaaf2) replaced, because what is
being tested is the arrival path and not their parsing, which has its own tests
and needs real media.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

pytest.importorskip("boto3")
pytest.importorskip("moto")
sa = pytest.importorskip("sqlalchemy")

from conftest import (  # noqa: E402
    ORG,
    PROJECT,
    asset_row,
    create_asset,
    digest,
    requires_schema,
    send_parts,
)
from mishne import probe as probe_module  # noqa: E402
from mishne import storage  # noqa: E402
from mishne.pipeline.steps import prepare  # noqa: E402
from mishne.timecode import Rate  # noqa: E402

pytestmark = requires_schema


def _uploaded(http, client, blob: bytes, filename: str, **extra) -> str:
    """Put a real object in the bucket the way a browser would, and return its id."""
    body = {
        "filename": filename,
        "bytes": len(blob),
        "checksum": digest(blob),
        **extra,
    }
    created = http.post(f"/v1/projects/{PROJECT}/assets", json=body)
    assert created.status_code == 201, created.text
    created = created.json()
    key = f"orgs/{ORG}/projects/{PROJECT}/assets/{created['asset_id']}/source"
    parts = send_parts(client, key, created["upload_id"], blob, created["part_size"])
    done = http.post(f"/v1/assets/{created['asset_id']}/complete", json={"parts": parts})
    assert done.status_code == 200, done.text
    return created["asset_id"]


def _media_info(path: Path, rate=Rate(25, 1), frames=1500) -> prepare.MediaInfo:
    return prepare.MediaInfo(
        path=path,
        rate=rate,
        duration_frames=frames,
        start_tc_frames=90_000,
        start_tc="01:00:00:00",
        codec="prores",
        width=1920,
        height=1080,
        audio=[prepare.AudioTrack(index=0, channels=2, sample_rate=48_000, codec="pcm")],
        has_video=True,
    )


# ──────────────────────────────────────────────────────────────────────── video


def test_probe_gives_the_asset_a_time_base_before_any_job_exists(api, owner, monkeypatch):
    http, client = api
    asset_id = _uploaded(http, client, b"v" * 4096, "A002.mov")
    assert asset_row(owner, asset_id).status == "probing"

    monkeypatch.setattr(prepare, "probe", lambda path, assume_rate=None: _media_info(path))
    status = probe_module.probe_asset(ORG, asset_id)

    assert status == "ready"
    row = asset_row(owner, asset_id)
    assert (row.edit_rate_num, row.edit_rate_den) == (25, 1)
    assert row.duration_frames == 1500
    assert row.start_tc_frames == 90_000
    assert row.probe["audio_tracks"] == 1
    assert row.probe["codec"] == "prores"
    assert row.probed_at is not None


def test_the_probed_asset_is_what_the_api_now_reports(api, owner, monkeypatch):
    http, client = api
    asset_id = _uploaded(http, client, b"v" * 4096, "A002.mov")
    monkeypatch.setattr(
        prepare, "probe",
        lambda path, assume_rate=None: _media_info(path, Rate(24000, 1001), 2402),
    )
    probe_module.probe_asset(ORG, asset_id)

    body = http.get(f"/v1/assets/{asset_id}").json()

    # Rational all the way out. 24000/1001 is not 23.976, and a float here is a
    # frame lost every 42 seconds.
    assert body["rate"] == {"num": 24000, "den": 1001}
    assert body["duration_frames"] == 2402
    assert body["status"] == "ready"


def test_an_unreadable_file_fails_with_a_code_and_no_customer_content(api, owner, monkeypatch):
    http, client = api
    asset_id = _uploaded(http, client, b"not really a movie", "A002.mov")

    def explode(path, assume_rate=None):
        raise RuntimeError(f"ffprobe failed on {path.name}: moov atom not found")

    monkeypatch.setattr(prepare, "probe", explode)
    assert probe_module.probe_asset(ORG, asset_id) == "failed"

    row = asset_row(owner, asset_id)
    assert row.status == "failed"
    assert row.error["code"] == "unreadable_media"
    # The error is read back into an API response, which is a wider audience
    # than a log line: no filename, no key, no path.
    assert "A002.mov" not in str(row.error)


def test_an_asset_still_uploading_is_not_probed(api, owner, monkeypatch):
    http, _ = api
    created = http.post(
        f"/v1/projects/{PROJECT}/assets",
        json={"filename": "A002.mov", "bytes": 4096, "checksum": digest(b"x")},
    ).json()

    def never(*_args, **_kwargs):
        raise AssertionError("probe read an object that is not there yet")

    monkeypatch.setattr(prepare, "probe", never)
    assert probe_module.probe_asset(ORG, created["asset_id"]) == "uploading"


# ──────────────────────────────────────────────────────────────────────── audio


def test_an_audio_upload_must_declare_its_rate(api):
    http, _ = api
    resp = http.post(
        f"/v1/projects/{PROJECT}/assets",
        json={"filename": "interview.wav", "bytes": 100, "checksum": digest(b"a")},
    )
    # ADR-0005: there is no frame rate in the file, and guessing one silently is
    # how a cut ends up a frame out everywhere.
    assert resp.status_code == 422
    assert "rate" in resp.json()["detail"]


def test_a_declared_rate_is_what_the_audio_is_probed_against(api, owner, monkeypatch):
    http, client = api
    asset_id = _uploaded(
        http, client, b"a" * 2048, "interview.wav", rate={"num": 24000, "den": 1001}
    )
    seen = {}

    def fake(path, assume_rate=None):
        seen["rate"] = assume_rate
        return _media_info(path, assume_rate, 720)

    monkeypatch.setattr(prepare, "probe", fake)
    probe_module.probe_asset(ORG, asset_id)

    assert (seen["rate"].num, seen["rate"].den) == (24000, 1001)
    assert asset_row(owner, asset_id).status == "ready"


# ────────────────────────────────────────────────────────────────────────── AAF


class _FakeClip:
    def __init__(self, name, url, resolved=False, embedded=None):
        self.name = name
        self.target_url = url
        self.media_path = Path("/tmp/whatever") if resolved else None
        self.embedded_mob_id = embedded
        self.mob_id = "urn:smpte:umid:1"


class _FakeSource:
    def __init__(self, clips, embedded=False, tracks=None):
        self.rate = Rate(25, 1)
        self.duration_frames = 5550
        self.start_tc_frames = 0
        self.clips = clips
        self.embedded = embedded
        self.missing = [c.name for c in clips if c.media_path is None and not c.embedded_mob_id]
        self.notes = []
        # What the probe records as the sequence's audio track count. Derived
        # from the clips by default, the way the real `AAFSource.tracks` is.
        self.tracks = (tracks if tracks is not None
                       else sorted({getattr(c, "track_index", 0) for c in clips}))


def _fake_aaf(monkeypatch, source):
    aaf_ingest = pytest.importorskip("mishne.pipeline.steps.aaf_ingest")
    # `parse` takes `search_dirs` now; the stub has to accept what the caller
    # passes or the probe fails with an AttributeError it reports as a bad AAF.
    monkeypatch.setattr(aaf_ingest, "parse", lambda path, **_: source)


def test_an_embedded_aaf_is_ready_on_its_own(api, owner, monkeypatch):
    http, client = api
    asset_id = _uploaded(http, client, b"AAF" * 500, "SyncDaniel.aaf")
    _fake_aaf(monkeypatch, _FakeSource(
        [_FakeClip("shot", "file:///san/A001.mxf", embedded="urn:mob:1")], embedded=True
    ))

    assert probe_module.probe_asset(ORG, asset_id) == "ready"
    row = asset_row(owner, asset_id)
    assert row.ingest_mode == "aaf_embedded"
    assert row.duration_frames == 5550


def test_a_linked_aaf_waits_for_the_media_it_references(api, owner, monkeypatch):
    http, client = api
    asset_id = _uploaded(http, client, b"AAF" * 500, "Sequence.aaf")
    _fake_aaf(monkeypatch, _FakeSource([
        _FakeClip("one", "file:///san/A001.mxf"),
        _FakeClip("two", "file:///san/A001.mxf"),
        _FakeClip("three", "file:///san/B002.mxf"),
    ]))

    status = probe_module.probe_asset(ORG, asset_id)

    # Not `failed`: nothing is wrong. Not `ready` either — a job started against
    # it would transcribe silence.
    assert status == "awaiting_media"
    row = asset_row(owner, asset_id)
    assert row.status == "awaiting_media"
    assert row.ingest_mode == "aaf_linked"

    body = http.get(f"/v1/assets/{asset_id}/requirements").json()
    assert body["outstanding"] == 2
    assert [r["basename"] for r in body["requirements"]] == ["A001.mxf", "B002.mxf"]
    assert body["requirements"][0]["clip_count"] == 2


def test_uploading_the_missing_media_releases_the_sequence(api, owner, monkeypatch):
    http, client = api
    aaf_id = _uploaded(http, client, b"AAF" * 500, "Sequence.aaf")
    _fake_aaf(monkeypatch, _FakeSource([
        _FakeClip("one", "file:///san/A001.mxf"),
        _FakeClip("two", "file:///SAN/OTHER/b002.MXF"),
    ]))
    probe_module.probe_asset(ORG, aaf_id)

    _uploaded(http, client, b"first" * 100, "A001.mxf")
    assert asset_row(owner, aaf_id).status == "awaiting_media"

    # Case and directory differ from the locator; the editor's filesystem does
    # not care and neither does the match.
    _uploaded(http, client, b"second" * 100, "B002.mxf")

    assert asset_row(owner, aaf_id).status == "ready"
    body = http.get(f"/v1/assets/{aaf_id}/requirements").json()
    assert body["outstanding"] == 0
    assert all(r["satisfied"] for r in body["requirements"])


def _listed(http, asset_id: str) -> dict:
    """The asset as the project screen reads it."""
    rows = http.get(f"/v1/projects/{PROJECT}/assets").json()
    return next(a for a in rows if a["id"] == asset_id)


def test_a_sequence_reports_the_size_of_its_media_and_not_of_itself(
    api, owner, monkeypatch
):
    """1500 bytes of pointers over a 46-minute podcast is true and useless.

    What an editor checks before believing an upload is that the essence is
    there, and a linked AAF's own size cannot tell them: it is the same few
    hundred kilobytes whether the media is forty gigabytes or absent. The total
    is a running one while the files arrive, which is why it is reported for a
    sequence with nothing yet — zero is an answer, and no answer is what an
    embedded AAF gives.
    """
    http, client = api
    aaf_id = _uploaded(http, client, b"AAF" * 500, "Sequence.aaf")
    _fake_aaf(monkeypatch, _FakeSource([
        _FakeClip("one", "file:///san/A001.mxf"),
        _FakeClip("two", "file:///san/B002.mxf"),
    ]))
    probe_module.probe_asset(ORG, aaf_id)

    assert _listed(http, aaf_id)["media_bytes"] == 0

    _uploaded(http, client, b"first" * 100, "A001.mxf")
    _uploaded(http, client, b"second" * 100, "B002.mxf")

    listed = _listed(http, aaf_id)
    assert listed["bytes"] == 1500
    assert listed["media_bytes"] == 500 + 600


def test_a_file_that_carries_its_own_essence_reports_no_media_total(
    api, owner, monkeypatch
):
    """`None`, not zero: `bytes` is already the whole answer for these."""
    http, client = api
    aaf_id = _uploaded(http, client, b"AAF" * 500, "SyncDaniel.aaf")
    _fake_aaf(monkeypatch, _FakeSource(
        [_FakeClip("shot", "file:///san/A001.mxf", embedded="urn:mob:1")], embedded=True
    ))
    probe_module.probe_asset(ORG, aaf_id)

    assert _listed(http, aaf_id)["media_bytes"] is None


def test_a_re_probe_drops_a_requirement_that_has_gone_away(api, owner, monkeypatch):
    # The customer re-exported the sequence with embedded essence. The old
    # requirement must not linger and block the job forever.
    http, client = api
    aaf_id = _uploaded(http, client, b"AAF" * 500, "Sequence.aaf")
    _fake_aaf(monkeypatch, _FakeSource([_FakeClip("one", "file:///san/A001.mxf")]))
    probe_module.probe_asset(ORG, aaf_id)
    assert asset_row(owner, aaf_id).status == "awaiting_media"

    _fake_aaf(monkeypatch, _FakeSource(
        [_FakeClip("one", "file:///san/A001.mxf", embedded="urn:mob:1")], embedded=True
    ))
    assert probe_module.probe_asset(ORG, aaf_id) == "ready"
    assert http.get(f"/v1/assets/{aaf_id}/requirements").json()["requirements"] == []


def test_an_unreadable_aaf_says_so(api, owner, monkeypatch):
    http, client = api
    asset_id = _uploaded(http, client, b"not an aaf", "Sequence.aaf")
    aaf_ingest = pytest.importorskip("mishne.pipeline.steps.aaf_ingest")

    def explode(path):
        raise OSError("not a structured storage file")

    monkeypatch.setattr(aaf_ingest, "parse", explode)
    assert probe_module.probe_asset(ORG, asset_id) == "failed"
    assert asset_row(owner, asset_id).error["code"] == "unreadable_aaf"


# ──────────────────────────────────────────────────────────────── the S3 event


def _event(key: str) -> dict:
    return {"Records": [{"s3": {"bucket": {"name": "test-raw"}, "object": {"key": key}}}]}


def test_the_event_handler_probes_the_asset_the_key_names(api, owner, monkeypatch):
    http, client = api
    asset_id = _uploaded(http, client, b"v" * 4096, "A002.mov")
    monkeypatch.setattr(prepare, "probe", lambda path, assume_rate=None: _media_info(path))

    probed = probe_module.handle_s3_event(
        _event(f"orgs/{ORG}/projects/{PROJECT}/assets/{asset_id}/source")
    )

    assert probed == [asset_id]
    assert asset_row(owner, asset_id).status == "ready"


def test_a_percent_encoded_key_is_the_same_key(api, owner, monkeypatch):
    http, client = api
    asset_id = _uploaded(http, client, b"v" * 4096, "A002.mov")
    monkeypatch.setattr(prepare, "probe", lambda path, assume_rate=None: _media_info(path))

    encoded = f"orgs/{ORG}/projects/{PROJECT}/assets/{asset_id}/source".replace("/", "%2F")
    assert probe_module.handle_s3_event(_event(encoded)) == [asset_id]


@pytest.mark.parametrize(
    "key",
    [
        "orgs/o/projects/p/assets/a/derived/audio.wav",
        "orgs/o/jobs/j/artifacts/cut.aaf",
        "something/else/entirely",
        "orgs/o/projects/p/assets/a/source/extra",
        "",
    ],
)
def test_anything_that_is_not_a_source_object_is_ignored(key):
    # The handler acts on the parse by writing to that org's rows. A best guess
    # is not available to it.
    assert storage.parse_source_key(key) is None
    assert probe_module.handle_s3_event(_event(key)) == []
