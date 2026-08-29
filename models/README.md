# models

Whisper weights, in CTranslate2 format (what faster-whisper loads).

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
