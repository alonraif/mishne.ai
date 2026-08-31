# The worker image: the pipeline, its binaries, and its models.
#
# Two things in here are the difference between a worker that runs and one that
# fails in a way nobody can diagnose from a log line:
#
#   ffmpeg and ffprobe. Stages 0-1 shell out to them; they are not a Python
#   dependency and pip will not bring them.
#
#   ffmpeg is also what splits audio that is longer than an engine will take in
#   one request (asr/chunking.py).
#
#   NO ASR WEIGHTS, by default. Transcription is a managed API now (ADR-0018),
#   so the 3 GB of Whisper weights this image used to carry are dead payload on
#   every pull, every cold start and every worker. `faster-whisper` itself is
#   still installed and is NOT optional — stage 3 uses the Silero VAD model
#   bundled inside it, which is a few megabytes and unrelated to transcription.
#
#   For an air-gapped customer, or to re-measure the self-hosted baseline, bake
#   the weights back in and run with `--asr faster-whisper`:
#       docker build --build-arg MODEL_DIR=models/faster-whisper-large-v3 .
#
# Python is pinned to 3.12 on purpose: OpenTimelineIO has no 3.14 wheel and
# fails at import with `RuntimeError: bad any cast`. Supported range is 3.9-3.13
# and setup.sh enforces the same thing for a laptop.
FROM python:3.12-slim-bookworm

# ffmpeg brings ffprobe. `--no-install-recommends` keeps the layer from
# collecting an X stack the worker will never use.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Not root. Media processing is the most attacker-exposed part of this system:
# uploaded files are attacker-controlled and ffmpeg has a large surface
# (docs/architecture/04-security.md).
RUN useradd --create-home --uid 10001 worker
WORKDIR /app

# Dependencies first, so a code change does not re-resolve the world.
COPY pyproject.toml README.md* ./
COPY src ./src
RUN pip install --no-cache-dir . \
 && pip install --no-cache-dir \
      faster-whisper numpy \
      opentimelineio otio-aaf-adapter otio-cmx3600-adapter otio-fcpx-xml-adapter \
      ortools

COPY run.py ./run.py

# ASR weights, only if asked for. `MODEL_DIR` is a path in the build context;
# the default is an empty directory, so the normal image carries none. A build
# that points it at real weights gets the offline path, and nothing else
# changes — the provider is a flag (ADR-0003, ADR-0018).
ARG MODEL_DIR=models
COPY ${MODEL_DIR} /opt/models
ENV MISHNE_MODEL_PATH=/opt/models

# Scratch. A worker's disk must hold the largest asset it may be handed, plus
# that asset's extracted audio, plus — for an AAF with embedded essence — the
# essence written out beside it (ADR-0013):
#
#     disk >= largest_asset_bytes * 2 + headroom
#
# and `max_upload_bytes` is the product ceiling that has to agree with it.
ENV WORK_ROOT=/scratch
RUN mkdir -p /scratch && chown worker /scratch
VOLUME ["/scratch"]

# 2 GB with managed ASR: the peak is then ffmpeg and the solver, not a model.
#
# large-v3 peaks around 4.6 GB, so a worker built with weights and run with
# `--asr faster-whisper` needs 8 GB — a 4 GB container is an OOM kill, exit 137,
# and no useful message anywhere. Two labels rather than one number, because
# sizing every worker for a model most of them will never load was a large part
# of what made self-hosted transcription expensive.
LABEL org.mishne.min-memory-gb="2"
LABEL org.mishne.min-memory-gb-self-hosted-asr="8"

USER worker
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# One job per invocation. Step Functions passes the ids; nothing else.
ENTRYPOINT ["python", "-m", "mishne.orchestration.worker"]
