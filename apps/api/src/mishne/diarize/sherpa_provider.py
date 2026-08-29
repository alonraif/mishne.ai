"""Local diarization on ONNX Runtime.

Deliberately not pyannote-on-torch. Torch is a two-gigabyte dependency and the
pyannote pipeline is gated behind an account and a licence acceptance, neither
of which belongs in a customer's install path. ONNX Runtime is already here for
the VAD, the two models together are 36 MB against Whisper's three gigabytes,
and nothing needs a token.

## The channel problem, and what this does about it

A speaker embedding encodes the microphone and the room along with the voice.
Cluster the embeddings of a sequence assembled from two cameras and you
partition the audio by camera. Measured on the reference material: five
"speakers" whose boundaries sat on the clip seams.

The fix is to **diarize each source region separately** and cluster the results
afterwards. Within one clip the channel is constant, so the clustering is about
voices; matching across clips then decides which of those voices are the same
person.

The obvious refinement — subtract each region's mean embedding to cancel the
channel — is wrong here and was tried. It assumes every region contains a
similar mix of speakers. In this material most regions are one person talking,
so the region mean *is* that person, and subtracting it removes the signal
rather than the noise: fourteen speakers, none above 17% of the audio. Raw
embeddings, clustered across regions, give three. The lesson is that channel
compensation needs regions with mixed speakers, which narration does not have.

Regions too short to diarize are not diarized. A two-second clip cannot support
a speaker decision, and inventing one is how a cut ends up mislabelled; those
turns are matched to existing speakers on embedding alone, or left unassigned.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .base import DiarizationResult, Turn

# Below this a region cannot support its own clustering — one voice or five
# looks the same in two seconds of audio.
MIN_REGION_MS = 8_000
# Below this a turn's embedding is too noisy to match on.
MIN_TURN_MS = 800
# Cosine distance at which two embeddings are the same person. Swept against
# the reference material: 0.35 splits one presenter into two, 0.65 merges a
# separate voice away, 0.45 is the middle of the stable band. Turn it up if a
# person comes back twice, down if two people share a label.
MERGE_DISTANCE = 0.45
# Within a region, sherpa's own clustering threshold.
CLUSTER_THRESHOLD = 0.6


@dataclass
class SherpaDiarizer:
    """Pyannote segmentation + a speaker embedding model, both as ONNX.

    `model_dir` holds `segmentation.onnx` and `embedding.onnx`. Fetch once:

        https://github.com/k2-fsa/sherpa-onnx/releases
          speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2
          speaker-recongition-models/wespeaker_en_voxceleb_CAM++.onnx

    The embedding model is VoxCeleb-trained and language-independent — it
    measures voice timbre, not words — so it is as valid on Hebrew as English.
    """

    model_dir: Path
    num_threads: int = 4
    name: str = "sherpa-onnx"

    def __post_init__(self):
        self.model_dir = Path(self.model_dir)
        self._seg = self.model_dir / "segmentation.onnx"
        self._emb = self.model_dir / "embedding.onnx"
        missing = [p.name for p in (self._seg, self._emb) if not p.exists()]
        if missing:
            raise FileNotFoundError(
                f"diarization models not found in {self.model_dir}: "
                f"{', '.join(missing)}. See this module's docstring for where "
                f"to fetch them.")

    # -- lazily built, because importing sherpa_onnx costs a second ----------

    def _diarizer(self):
        import sherpa_onnx as so
        return so.OfflineSpeakerDiarization(so.OfflineSpeakerDiarizationConfig(
            segmentation=so.OfflineSpeakerSegmentationModelConfig(
                pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(self._seg)),
                num_threads=self.num_threads),
            embedding=so.SpeakerEmbeddingExtractorConfig(
                model=str(self._emb), num_threads=self.num_threads),
            clustering=so.FastClusteringConfig(
                num_clusters=-1, threshold=CLUSTER_THRESHOLD),
            min_duration_on=0.3, min_duration_off=0.5))

    def _extractor(self):
        import sherpa_onnx as so
        return so.SpeakerEmbeddingExtractor(so.SpeakerEmbeddingExtractorConfig(
            model=str(self._emb), num_threads=self.num_threads))

    # -- the interface ------------------------------------------------------

    def diarize(self, audio_path: Path,
                regions: list[tuple[int, int]] | None = None
                ) -> DiarizationResult:
        audio, sr = _read_wav(Path(audio_path))
        total_ms = int(len(audio) / sr * 1000)
        notes: list[str] = []

        spans = _usable_regions(regions, total_ms, notes)
        sd, ex = self._diarizer(), self._extractor()

        # (region index, turn, embedding) for everything worth clustering.
        rows: list[tuple[int, Turn, np.ndarray]] = []
        for ri, (lo, hi) in enumerate(spans):
            chunk = audio[int(lo / 1000 * sr):int(hi / 1000 * sr)]
            for seg in sd.process(chunk).sort_by_start_time():
                t = Turn(start_ms=lo + int(seg.start * 1000),
                         end_ms=lo + int(seg.end * 1000),
                         speaker=f"r{ri}s{seg.speaker}")
                if t.duration_ms < MIN_TURN_MS:
                    continue
                rows.append((ri, t, _embed(ex, audio, sr, t)))

        if not rows:
            return DiarizationResult(
                turns=[], provider=self.name, model=self._emb.name,
                reliable=False, notes=notes + ["no speech found to separate"])

        labels = _match_across_regions(rows)
        turns = []
        for (_, t, _), label in zip(rows, labels):
            turns.append(Turn(t.start_ms, t.end_ms, f"S{label + 1}"))
        turns.sort(key=lambda t: t.start_ms)

        count = len({t.speaker for t in turns})
        reliable = True
        by_spk: dict[str, int] = {}
        for t in turns:
            by_spk[t.speaker] = by_spk.get(t.speaker, 0) + t.duration_ms
        total = sum(by_spk.values()) or 1
        dominant = max(by_spk.values()) / total
        if count > 1 and dominant > 0.9:
            # One voice holding almost everything with slivers around it means
            # the model heard *something* but could not hold the minor voices
            # apart. Real on this material: a four-second answer against three
            # minutes of narration is not enough audio to embed confidently.
            reliable = False
            notes.append(
                f"one voice is {dominant * 100:.0f}% of the audio and the "
                f"others are seconds long — the minor speakers are a weak "
                f"separation, not a confident one")
        if len(spans) > 1:
            notes.append(
                f"diarized per source region ({len(spans)}) and matched across "
                f"them — clustering the assembled audio whole would separate "
                f"microphones rather than people")
        if count > 6:
            reliable = False
            notes.append(
                f"{count} distinct voices is more than this kind of material "
                f"usually has — check the labels before trusting the cut")
        return DiarizationResult(turns=turns, provider=self.name,
                                 model=self._emb.name, reliable=reliable,
                                 notes=notes)


# --- helpers -----------------------------------------------------------------


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path.name}: expected 16-bit mono")
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0, sr


def _usable_regions(regions, total_ms: int, notes: list[str]
                    ) -> list[tuple[int, int]]:
    """Source regions long enough to diarize; short ones fold into a neighbour.

    Folding rather than dropping: the audio still has to be covered, and a
    two-second clip appended to its neighbour changes the channel slightly,
    which is a far smaller error than giving it a speaker of its own.
    """
    if not regions:
        return [(0, total_ms)]
    spans: list[tuple[int, int]] = []
    for lo, hi in sorted(regions):
        if spans and (hi - lo) < MIN_REGION_MS:
            spans[-1] = (spans[-1][0], hi)
        else:
            spans.append((lo, hi))
    if len(spans) < len(regions):
        notes.append(f"{len(regions) - len(spans)} source clip(s) too short to "
                     f"diarize on their own — folded into the previous clip")
    return spans


def _embed(extractor, audio: np.ndarray, sr: int, t: Turn) -> np.ndarray:
    stream = extractor.create_stream()
    stream.accept_waveform(sr, audio[int(t.start_ms / 1000 * sr):
                                     int(t.end_ms / 1000 * sr)])
    stream.input_finished()
    v = np.asarray(extractor.compute(stream), dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n else v


def _match_across_regions(rows) -> list[int]:
    """Cluster the per-region turns into people.

    Average-linkage agglomerative on cosine distance, stopped at
    MERGE_DISTANCE. Written out rather than pulled from scipy because it runs on
    a few dozen vectors and adding a dependency for twenty lines is a poor
    trade.

    Raw embeddings, not channel-compensated — see the module docstring for why
    the compensated version is worse on exactly the material that needs help.
    """
    vecs = np.stack([v for _, _, v in rows])
    clusters = [[i] for i in range(len(rows))]
    while len(clusters) > 1:
        best, pair = None, None
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                d = float(np.mean([1 - float(vecs[i] @ vecs[j])
                                   for i in clusters[a] for j in clusters[b]]))
                if best is None or d < best:
                    best, pair = d, (a, b)
        if best is None or best > MERGE_DISTANCE:
            break
        a, b = pair
        clusters[a].extend(clusters[b])
        clusters.pop(b)

    # Biggest speaker first, so S1 is the person who talks most.
    order = sorted(range(len(clusters)),
                   key=lambda c: -sum(rows[i][1].duration_ms
                                      for i in clusters[c]))
    labels = [0] * len(rows)
    for rank, c in enumerate(order):
        for i in clusters[c]:
            labels[i] = rank
    return labels
