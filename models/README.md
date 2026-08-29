# models

Whisper weights (CTranslate2, what faster-whisper loads), and the diarization
models for separating voices on single-track material.

**Not committed** — see .gitignore. Downloaded once, reused forever.

```bash
pip install -U "huggingface_hub[cli]"
hf download Systran/faster-whisper-large-v3 \
  --local-dir ~/Dev/mishne.ai/models/faster-whisper-large-v3
```

Then:

```bash
cd apps/api
.venv/bin/python run.py ../../samples/SyncDaniel.aaf --language he \
  --model-path ../../models/faster-whisper-large-v3 --target 40s
```

## Which size

| Model | Size | Hebrew |
|---|---|---|
| `Systran/faster-whisper-large-v3` | 3.1 GB | the one to ship with |
| `Systran/faster-whisper-medium` | 1.5 GB | usable; faster for a first look |
| `base`, `small` | < 0.5 GB | poor for Hebrew — do not judge output from these |

Hebrew degrades sharply below `medium`: word timestamps drift and filler gets
dropped, which matters here because removing filler is the product's job and it
cannot do it if the ASR already did.

In production the model belongs baked into the worker image. A cold start that
pulls 3 GB is not a cold start you want against a job SLA.


## Diarization

Only needed for **single-track** material. Multi-track footage needs no model at
all — the loudest microphone is whoever is talking, and that path is arithmetic.
Without these, one-track material is reported honestly as "voices were never
separated" rather than being given an invented Speaker 1.

Two ONNX files, 36 MB together, no account and no licence acceptance. This is
deliberately not pyannote-on-torch: torch is a two-gigabyte dependency and the
pyannote pipeline is gated behind a Hugging Face account, neither of which
belongs in a customer install. ONNX Runtime is already here for the VAD.

```bash
pip install sherpa-onnx
mkdir -p ~/Dev/mishne.ai/models/diarize && cd ~/Dev/mishne.ai/models/diarize
B=https://github.com/k2-fsa/sherpa-onnx/releases/download
curl -sSL $B/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2 | tar xj
mv sherpa-onnx-pyannote-segmentation-3-0/model.onnx segmentation.onnx
curl -sSLo embedding.onnx $B/speaker-recongition-models/wespeaker_en_voxceleb_CAM++.onnx
rm -rf sherpa-onnx-pyannote-segmentation-3-0
```

Then add `--diarize ../../models/diarize` to a run. The embedding model is
VoxCeleb-trained and measures voice timbre rather than words, so it is as valid
on Hebrew as on English.

**Expect it to be honest about being unsure.** On the reference segment it finds
three voices and marks the separation weak, because a four-second answer against
three minutes of narration is not enough audio to embed confidently. That
warning is the feature: the alternative is a confident wrong label on every
line. See `docs/adr/0009-diarization-per-source-region.md`.
