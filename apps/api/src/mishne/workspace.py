"""Where the pipeline's files are, and how a worker gets a real path to one.

## The constraint this module exists for

Two of the pipeline's dependencies cannot be given a stream:

* **ffmpeg / ffprobe** are separate processes. They take `argv`, and `argv`
  holds a path.
* **pyaaf2** opens OLE structured storage and seeks around inside it. An AAF is
  not read front to back; it is a small filesystem, and reading it means random
  access.

So "read the object from S3" is not an option at the point of use. Something has
to put a real file on a real disk first, and this module is that something.

## The decision: stage to local disk, not FUSE

A FUSE mount (mountpoint-s3 and friends) makes an S3 key look like a path, needs
almost no disk, and is very appealing right up to the first large AAF. pyaaf2's
access pattern over structured storage is thousands of small seeks, each of
which becomes a ranged GET with a network round trip; read amplification on a
30 GB AAF is pathological rather than merely slow. Worse, when it does fail it
fails as `EIO` from inside a C library, several frames below any code that knows
what an asset is.

Downloading is dumber and better. It is one sequential read at full bandwidth,
it is trivially observable, failures are ordinary Python exceptions with the key
in them, and the file behaves like a file for every subsequent stage.

**The cost, stated plainly, because B3 has to size workers against it:** a
worker's scratch disk must hold the largest single asset it may be handed, plus
that asset's extracted audio, plus — for an AAF with embedded essence — the
essence written out beside it. Budget

    disk >= largest_asset_bytes * 2 + headroom

and cap `max_upload_bytes` at something a worker class can actually hold. The
`storage.MAX_OBJECT_BYTES` ceiling is S3's, not ours; ours is
`Settings.max_upload_bytes`.

## The cache is the economics

Stages 0-4 are cached per asset and transcription is the expensive one. On one
machine that cache was a directory. Across workers it has to outlive the worker,
or every retry re-transcribes and the unit economics invert — so the per-asset
derived files are mirrored to the derived bucket and pulled back on first touch.

What is mirrored is deliberately narrow: `ingest.json`, the extracted audio, the
raw ASR response. Not the intermediate `_seg_*.wav` files, and not extracted AAF
essence, which is reproducible, enormous, and would cost more to store than to
rebuild.

## Local runs are unchanged

`run.py` on a laptop uses `LocalWorkspace`, which is a directory and nothing
else. The point of the abstraction is that the concierge path keeps working
exactly as it does today; it is not a wrapper that everything now has to
tolerate.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .config import Settings, get_settings
from .logging import get_logger
from .storage import ObjectRef, Storage, bucket_for, derived_key, get_storage

log = get_logger(__name__)

#: Files in an asset's working directory worth keeping between runs. Everything
#: else there is either an intermediate that costs nothing to rebuild
#: (`_seg_*.wav`, `_sil_*.wav`, `_concat.txt`) or is enormous and reproducible
#: (extracted AAF essence, which lives in a subdirectory and is skipped because
#: only top-level files are considered).
CACHEABLE = ("ingest.json", "*.wav", "*.asr.json", "transcript.json")

#: Never mirrored, even though they match the patterns above.
#:
#: `_track_` is the per-sound-track render a multi-track sequence is mixed from
#: (ADR-0019). Four microphones on a 46-minute podcast is four full-length WAVs
#: — 350 MB per asset — wanted only during the run that produced them, and
#: rebuildable from the source. Exactly what ADR-0013 says not to mirror.
NOT_CACHEABLE = ("_seg_", "_sil_", "_concat", "_track_")


@dataclass
class SourceFile:
    """One object to be put on disk before a stage can read it.

    `name` is the customer's filename rather than anything derived from the key,
    and that is load-bearing in one specific case: an AAF with *linked* media
    resolves its clips by looking for files with the referenced basenames beside
    it (`aaf_ingest._url_to_path`). Materialising companion media under their
    original names into the same directory is what makes that resolution work
    without teaching the AAF parser about object storage.
    """

    name: str
    ref: ObjectRef


class Workspace(Protocol):
    """Somewhere to put files, and somewhere to read them back from."""

    def asset_dir(self, asset_id: str) -> Path:
        """A real directory for one asset's working files."""

    def materialise(self, asset_id: str, source: SourceFile,
                    companions: list[SourceFile] | None = None) -> Path:
        """Put the asset's source on local disk and return its path."""

    def publish_asset(self, asset_id: str) -> None:
        """Persist the cacheable part of an asset's working directory."""

    def publish_artifact(self, local: Path, job_id: str, name: str) -> ObjectRef | None:
        """Persist one output. Returns where it went, or None locally."""

    @property
    def root(self) -> Path:
        """The directory `project.ingest` treats as `work_dir`."""


@dataclass
class LocalWorkspace:
    """A directory. What `run.py` has always done, named.

    Nothing is uploaded and nothing is fetched: the source is already a path on
    this machine, and the cache is the directory itself. This is the
    single-machine concierge path and it must stay exactly as cheap as it was.
    """

    path: Path

    @property
    def root(self) -> Path:
        return self.path

    def asset_dir(self, asset_id: str) -> Path:
        d = self.path / "assets" / asset_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def materialise(self, asset_id: str, source: SourceFile,
                    companions: list[SourceFile] | None = None) -> Path:
        # A local source is identified by its own path; `ref.key` carries it.
        return Path(source.ref.key)

    def publish_asset(self, asset_id: str) -> None:
        return None

    def publish_artifact(self, local: Path, job_id: str, name: str) -> ObjectRef | None:
        return None


@dataclass
class S3Workspace:
    """Objects in, files on disk, derived files back out.

    Scoped to one org and project because every key is, and because a workspace
    that could be handed an object from another tenant is a class of bug this
    system should not have available to it.
    """

    org_id: str
    project_id: str
    scratch: Path
    storage: Storage = field(default_factory=get_storage)
    settings: Settings = field(default_factory=get_settings)
    #: Assets whose derived files have already been pulled down this run, so a
    #: second stage touching the same asset does not re-fetch the cache.
    _hydrated: set[str] = field(default_factory=set)

    @property
    def root(self) -> Path:
        self.scratch.mkdir(parents=True, exist_ok=True)
        return self.scratch

    def asset_dir(self, asset_id: str) -> Path:
        d = self.root / "assets" / asset_id
        d.mkdir(parents=True, exist_ok=True)
        if asset_id not in self._hydrated:
            self._hydrate(asset_id, d)
            self._hydrated.add(asset_id)
        return d

    def materialise(self, asset_id: str, source: SourceFile,
                    companions: list[SourceFile] | None = None) -> Path:
        """Download the source — and anything it references — to one directory.

        Companions land beside the source under their own names, which is
        precisely the arrangement a linked AAF expects. They are downloaded
        before the AAF is parsed, because parsing is what looks for them.
        """
        src_dir = self.root / "sources" / asset_id
        src_dir.mkdir(parents=True, exist_ok=True)
        target = src_dir / _safe_name(source.name)

        for companion in companions or []:
            beside = src_dir / _safe_name(companion.name)
            if not beside.exists():
                self.storage.download(companion.ref, beside)
                log.info("companion.materialised", asset_id=asset_id,
                         bytes=beside.stat().st_size)

        if target.exists():
            return target
        self.storage.download(source.ref, target)
        log.info("source.materialised", asset_id=asset_id,
                 bytes=target.stat().st_size)
        return target

    def publish_asset(self, asset_id: str) -> None:
        """Mirror the cacheable working files so the next worker need not rebuild.

        Uploads are best-effort by design: a failure here costs a re-computation
        on the next run, and taking a job down because a cache write failed
        would trade a cheap problem for an expensive one. It is logged, not
        swallowed silently.
        """
        d = self.root / "assets" / asset_id
        if not d.exists():
            return
        for local in _cacheable_files(d):
            try:
                self.storage.upload(local, self._derived_ref(asset_id, local.name))
            except Exception as exc:  # noqa: BLE001 - see docstring
                log.warning("cache.publish_failed", asset_id=asset_id,
                            reason=type(exc).__name__)

    def publish_artifact(self, local: Path, job_id: str, name: str) -> ObjectRef:
        from .storage import artifact_key

        ref = ObjectRef(bucket_for("artifacts", self.settings),
                        artifact_key(self.org_id, job_id, name))
        self.storage.upload(local, ref)
        return ref

    def cleanup(self) -> None:
        """Delete the scratch tree.

        Worth being explicit about rather than leaving to the container
        lifecycle: a worker that processes several jobs before it is recycled
        fills its disk with the previous ones otherwise, and "no space left on
        device" three jobs later is a hard failure to attribute.
        """
        shutil.rmtree(self.scratch, ignore_errors=True)

    # ── internals ──────────────────────────────────────────────────────────

    def _derived_ref(self, asset_id: str, name: str) -> ObjectRef:
        return ObjectRef(
            bucket_for("derived", self.settings),
            derived_key(self.org_id, self.project_id, asset_id, name),
        )

    def _hydrate(self, asset_id: str, into: Path) -> None:
        """Pull this asset's previously cached derived files down.

        One LIST and then a GET per object. The alternative — trying each known
        name — is a round trip per miss on the common path where there is no
        cache at all, which is every first ingest.
        """
        bucket = bucket_for("derived", self.settings)
        prefix = derived_key(self.org_id, self.project_id, asset_id, "")
        try:
            pages = self.storage.client.get_paginator("list_objects_v2").paginate(
                Bucket=bucket, Prefix=prefix
            )
            names = [
                obj["Key"][len(prefix):]
                for page in pages
                for obj in page.get("Contents", [])
            ]
        except Exception as exc:  # noqa: BLE001
            log.warning("cache.list_failed", asset_id=asset_id,
                        reason=type(exc).__name__)
            return

        for name in names:
            if not name or "/" in name:
                continue
            dest = into / name
            if dest.exists():
                continue
            self.storage.download(ObjectRef(bucket, prefix + name), dest)
        if names:
            log.info("cache.hydrated", asset_id=asset_id, files=len(names))


def _cacheable_files(d: Path) -> list[Path]:
    from fnmatch import fnmatch

    out: list[Path] = []
    for child in sorted(d.iterdir()):
        if not child.is_file():
            continue
        if any(marker in child.name for marker in NOT_CACHEABLE):
            continue
        if any(fnmatch(child.name, pattern) for pattern in CACHEABLE):
            out.append(child)
    return out


def _safe_name(name: str) -> str:
    """A customer filename, made safe to join onto a path.

    The name comes from a browser and is therefore attacker-controlled: a
    literal `../../etc/passwd` in `assets.filename` must not become a write
    outside the scratch tree. Only the basename survives, and separators inside
    it — including the Windows one, which `Path.name` does not treat as a
    separator on Linux — are replaced rather than trusted.

    The *extension* is preserved deliberately: `project.ingest` branches on
    `.aaf`, and a name mangled into extensionlessness would silently take the
    ffprobe path on a file ffprobe cannot read.
    """
    base = name.replace("\\", "/").split("/")[-1]
    base = base.replace("\x00", "").strip()
    cleaned = "".join(c if c.isalnum() or c in "._- " else "_" for c in base)
    cleaned = cleaned.lstrip(".") or "source"
    return cleaned[:120]


def for_job(org_id: str, project_id: str, job_id: str,
            settings: Settings | None = None) -> S3Workspace:
    """The workspace one job's worker uses."""
    s = settings or get_settings()
    return S3Workspace(
        org_id=org_id,
        project_id=project_id,
        scratch=Path(s.work_root) / job_id,
        settings=s,
    )
