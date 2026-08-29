#!/usr/bin/env bash
# Create a native virtualenv for the pipeline.
#
# Two things this exists to prevent:
#
# 1. A virtualenv is not portable. .venv/bin/python symlinks to the interpreter
#    that created it, so one built inside the Cowork Linux VM is a dead link on
#    macOS — and macOS reports that by asking you to install Xcode command line
#    tools, which is a confusing way to be told "wrong platform".
#
# 2. OpenTimelineIO is a C++ extension and ships wheels for cp39-cp313 only.
#    On Python 3.14 there is nothing to install, the bindings end up mismatched,
#    and every adapter fails at load with "RuntimeError: bad any cast" — which
#    reads like a corrupt file rather than a version problem.
set -euo pipefail
cd "$(dirname "$0")"

MIN_MINOR=10
MAX_MINOR=13

echo "==> finding a supported Python (3.${MIN_MINOR}-3.${MAX_MINOR})"
PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  minor=$("$candidate" -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo 0)
  major=$("$candidate" -c 'import sys; print(sys.version_info[0])' 2>/dev/null || echo 0)
  if [ "$major" = "3" ] && [ "$minor" -ge "$MIN_MINOR" ] && [ "$minor" -le "$MAX_MINOR" ]; then
    PY="$candidate"
    echo "    using $candidate (3.$minor)"
    break
  fi
done

if [ -z "$PY" ]; then
  echo
  echo "No supported Python found."
  found=$(python3 -V 2>&1 || echo "none")
  echo "  your default python3 is: $found"
  echo
  echo "OpenTimelineIO ships wheels for 3.10-3.13. Install one:"
  echo
  echo "  brew install python@3.12"
  echo
  echo "then run this script again."
  exit 1
fi

for tool in ffmpeg ffprobe; do
  command -v "$tool" >/dev/null || {
    echo "$tool not found — every stage that touches audio shells out to it."
    echo "  macOS: brew install ffmpeg"
    echo "  Debian/Ubuntu: sudo apt install ffmpeg"
    exit 1
  }
done
echo "    ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f3)"

echo "==> creating .venv"
rm -rf .venv
"$PY" -m venv .venv
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
        missing.append(f"{m}: {type(e).__name__}: {e}")

# Importing OTIO is not enough — the adapters load lazily, and a version
# mismatch only surfaces when the manifest is read.
try:
    import opentimelineio as otio
    names = {a.name for a in otio.plugins.ActiveManifest().adapters}
    for want in ("AAF", "cmx_3600", "fcpx_xml"):
        if want not in names:
            missing.append(f"adapter {want} did not register")
except Exception as e:
    missing.append(f"OTIO adapter manifest: {type(e).__name__}: {e}")

if missing:
    print("    PROBLEMS:\n      " + "\n      ".join(missing))
    sys.exit(1)
print("    all imports and adapters ok")
PY

./.venv/bin/python -m pytest tests/ -q 2>&1 | tail -2

cat <<'MSG'

==> ready. Run it with:

  .venv/bin/python run.py ../../samples/SyncDaniel.aaf --language he --model-path ../../models/faster-whisper-large-v3 --target 40s --notes "your notes"

Transcription is the slow part — roughly real time or worse on CPU. The other
eleven stages take about three seconds.
MSG
