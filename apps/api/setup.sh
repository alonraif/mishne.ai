#!/usr/bin/env bash
# Create a native virtualenv for the pipeline.
#
# Run this on the machine you intend to run on. A virtualenv is not portable —
# .venv/bin/python is a symlink to the interpreter that created it, so one built
# inside the Cowork Linux VM is a broken link on macOS, and macOS responds by
# trying to locate `python` and asking you to install Xcode tools.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> checking prerequisites"
command -v python3 >/dev/null || {
  echo "python3 not found."
  echo "  macOS: xcode-select --install    (or: brew install python)"
  exit 1
}
echo "    python3 $(python3 -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')"

for tool in ffmpeg ffprobe; do
  command -v "$tool" >/dev/null || {
    echo "$tool not found — the pipeline needs it for every stage that touches audio."
    echo "  macOS: brew install ffmpeg"
    echo "  Debian/Ubuntu: sudo apt install ffmpeg"
    exit 1
  }
done
echo "    ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f3)"

echo "==> creating .venv"
rm -rf .venv
python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip

echo "==> installing dependencies (a few minutes; faster-whisper is the big one)"
./.venv/bin/pip install --quiet \
  faster-whisper numpy \
  opentimelineio otio-aaf-adapter otio-cmx3600-adapter otio-fcpx-xml-adapter \
  ortools anthropic pytest

echo "==> verifying"
./.venv/bin/python - <<'PY'
import importlib, sys
missing = []
for m in ("faster_whisper", "numpy", "opentimelineio", "aaf2",
          "ortools.sat.python.cp_model", "anthropic"):
    try:
        importlib.import_module(m)
    except Exception as e:
        missing.append(f"{m}: {type(e).__name__}")
print("    all imports ok" if not missing else "    MISSING:\n      " +
      "\n      ".join(missing))
sys.exit(1 if missing else 0)
PY

./.venv/bin/python -m pytest tests/ -q 2>&1 | tail -2

cat <<'MSG'

==> ready

  cd apps/api
  .venv/bin/python run.py ../../samples/SyncDaniel.aaf \
      --language he \
      --model-path ../../models/faster-whisper-large-v3 \
      --target 40s \
      --notes "your production notes here"

Transcription is the slow part — roughly real time or worse on CPU, so a
4-minute interview through large-v3 takes a while. The other eleven stages
take about three seconds.
MSG
