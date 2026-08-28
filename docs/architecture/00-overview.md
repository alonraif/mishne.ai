# 00 — System Overview

## The shape of the problem

mishne.ai is **a deterministic media pipeline with LLM judgment injected at three
well-defined points.** It is not an agent, and the distinction drives every other
decision in this document.

A job is a known DAG: probe → extract audio → transcribe → structure → score →
select → refine cut points → assemble → emit artifacts. It runs unattended for
10–60 minutes, must be retriable, must be billable at a predictable cost, and must
be able to answer "why was this cut chosen?" six months later. Those requirements
point at a workflow engine, not an agent loop. See
[ADR-0002](../adr/0002-workflow-engine-not-agent-framework.md).

The three places where genuine judgment is required:

1. **Brief compilation** — free-text director's notes → a structured `EditBrief`.
2. **Beat scoring** — each transcript segment scored for value against the brief.
3. **Sequence review** — does the assembled selection read as a coherent piece?

Everything else — voice activity detection, cut-point snapping, duration solving,
timecode arithmetic, AAF generation — is deterministic code. That is what makes the
system debuggable, reproducible, and cheap.

## Design principles

**1. Text is the edit surface; audio is the quality surface; video is untouched.**
The LLM reasons over transcript. Cut *placement* still needs the audio waveform —
silence boundaries, breaths, loudness — because a cut that lands mid-breath sounds
wrong no matter how good the word choice was. Video is never decoded beyond probing
metadata. This is the cost and latency win.

**2. One canonical timeline representation.** Every job converges on a single
OpenTimelineIO document. Every output format is a projection of it. No format
converts directly to another. See [ADR-0001](../adr/0001-otio-as-canonical-timeline.md).

**3. Every step is a pure, idempotent function of `(job_id, step_input) → artifact`.**
Inputs and outputs live in object storage, referenced by key. A step can be replayed
without replaying the pipeline. This is what makes the orchestrator swappable and the
system debuggable, and it costs nothing to adopt on day one.

**4. Reproducibility is a product feature, not an engineering nicety.** Model
versions, prompt versions, brief JSON, scores, and rationale are all persisted per
job. When a broadcaster asks why their best soundbite was dropped, there is an
answer. This also happens to be the trust-building mechanism with professional
editors, who start from a position of scepticism.

**5. Never hold customer footage longer than the job needs.** Pre-air and embargoed
material is the most sensitive asset a broadcaster has. Retention is short by
default and configurable. See [04 — Security](04-security.md).

**6. Multi-tenancy is enforced at the data layer, not the application layer.**
`org_id` on every row, Postgres RLS as the backstop.

## System context

```mermaid
flowchart TB
    subgraph users["Users"]
        creator["Content creator"]
        editor["Broadcast editor"]
    end

    subgraph mishne["mishne.ai"]
        web["Web app"]
        api["API"]
        pipe["Edit pipeline"]
        store[("Media store")]
        db[("Postgres")]
    end

    subgraph external["External services"]
        asr["ASR provider<br/>word-level timestamps"]
        llm["LLM provider<br/>brief / scoring / review"]
        idp["Identity provider<br/>SSO / SAML"]
    end

    subgraph nle["Editing systems"]
        avid["Avid Media Composer"]
        ppro["Premiere Pro"]
        resolve["DaVinci Resolve"]
        fcp["Final Cut Pro"]
    end

    creator --> web
    editor --> web
    web --> api
    api --> db
    api --> store
    api --> pipe
    pipe --> store
    pipe --> db
    pipe --> asr
    pipe --> llm
    web --> idp

    pipe -- "AAF" --> avid
    pipe -- "FCPXML" --> ppro
    pipe -- "FCPXML / EDL" --> resolve
    pipe -- "FCPXML" --> fcp
```

## Component map

```mermaid
flowchart LR
    subgraph edge["Edge"]
        next["Next.js app<br/>CloudFront"]
    end

    subgraph app["Application tier — stateless"]
        fastapi["FastAPI<br/>ECS Fargate"]
    end

    subgraph orch["Orchestration"]
        sfn["Step Functions<br/>state machine"]
    end

    subgraph workers["Worker tier — CPU only"]
        light["Light workers<br/>Fargate<br/>probe, structure, solve, emit"]
        heavy["Heavy workers<br/>EC2 spot + EBS<br/>ffmpeg, AAF demux"]
    end

    subgraph data["Data"]
        pg[("RDS Postgres<br/>Multi-AZ")]
        s3raw[("S3 · raw")]
        s3der[("S3 · derived")]
        s3art[("S3 · artifacts")]
        redis[("ElastiCache<br/>cache only")]
    end

    subgraph vendors["Vendors"]
        asr["ASR API"]
        llm["LLM API"]
    end

    next -->|"presigned multipart"| s3raw
    next --> fastapi
    fastapi --> pg
    fastapi --> redis
    fastapi -->|"start execution"| sfn
    sfn --> light
    sfn --> heavy
    light --> s3der
    heavy --> s3raw
    heavy --> s3der
    light --> s3art
    light --> asr
    light --> llm
    light --> pg
    heavy --> pg
```

Note what is **not** in this diagram: no GPU fleet, no Kubernetes, no message broker
of record, no microservices. The worker split is by resource profile (does this step
need 200 GB of scratch disk?) rather than by domain, which keeps deployment simple
while still letting the expensive tier scale independently.

## The two request flows

### Upload

```mermaid
sequenceDiagram
    participant U as Browser
    participant A as API
    participant S as S3 (raw)
    participant W as Worker

    U->>A: POST /assets {filename, size, checksum}
    A->>A: authorize org, enforce quota
    A->>S: create multipart upload
    A-->>U: uploadId + presigned part URLs
    loop each part, resumable
        U->>S: PUT part
    end
    U->>A: POST /assets/{id}/complete {etags}
    A->>S: complete multipart upload
    A->>W: enqueue probe
    W->>W: ffprobe / AAF parse
    W->>A: asset ready {duration, edit_rate, start_tc, drop_frame, tracks}
    A-->>U: asset ready
```

Media never transits the API. The browser talks to S3 directly with short-lived
presigned URLs. This matters more than it might appear: a 3-hour ProRes 422 master
is roughly 200 GB, and proxying that through an application tier is both an enormous
bandwidth bill and a guaranteed source of timeouts.

### Job

```mermaid
sequenceDiagram
    participant U as Browser
    participant A as API
    participant O as Step Functions
    participant W as Workers
    participant X as ASR / LLM

    U->>A: POST /jobs {asset_id, notes, target_duration}
    A->>O: StartExecution
    A-->>U: job_id (202)

    O->>W: extract audio (ffmpeg)
    O->>X: transcribe (word timestamps + diarization)
    O->>W: VAD / silence map
    O->>W: structure into beats
    O->>X: compile EditBrief
    O->>X: score beats
    O->>W: solve selection (CP-SAT)
    O->>X: review sequence
    O->>W: refine cut points
    O->>W: assemble OTIO
    O->>W: emit AAF / FCPXML / EDL
    O->>W: validate round-trip
    O->>A: job complete

    U->>A: GET /jobs/{id}/artifacts
    A-->>U: presigned download URLs + transcript page
```

Progress is streamed to the browser over SSE from the API, which reads step status
from Postgres. The frontend never talks to the orchestrator.

## Technology choices, and why

| Layer | Choice | Reasoning |
|---|---|---|
| Frontend | Next.js (App Router) | Server components for auth-gated pages, native SSE for job progress, mature upload ecosystem |
| Upload | Uppy + S3 multipart | Resumable by default. Non-negotiable for multi-GB files on unreliable connections |
| API | FastAPI (Python) | The entire media toolchain — pyaaf2, OpenTimelineIO, ffmpeg bindings — is Python. Splitting languages for an MVP buys nothing and costs a serialization boundary through the hottest part of the system |
| Orchestration | AWS Step Functions | Durable execution, declarative retries, per-execution history, nothing to operate. See [ADR-0002](../adr/0002-workflow-engine-not-agent-framework.md) |
| Compute | ECS Fargate + EC2 spot | Two profiles: light steps on Fargate, disk- and CPU-heavy ffmpeg/AAF steps on EC2 with EBS. Fargate ephemeral storage caps at 200 GB, which a large AAF will exceed |
| Database | RDS Postgres | Relational data with strict tenancy needs. RLS gives a real isolation backstop. pgvector for beat-redundancy clustering without a second datastore |
| Object storage | S3, three buckets | Separate raw / derived / artifacts for independent lifecycle rules, KMS keys, and access policies |
| Auth | WorkOS | Broadcast buyers ask for SAML SSO in the first procurement conversation. Building this is weeks of work with a long tail of edge cases |
| ASR | Provider interface, managed backend | See [ADR-0003](../adr/0003-managed-asr-behind-an-interface.md) |
| LLM | Claude, behind an interface | Already in use internally; strong structured-output support; enterprise zero-retention terms available |

## What is deliberately deferred

Named here so they don't get built by accident:

- Kubernetes. ECS is sufficient until there is a platform team.
- Microservices. One API, one worker image, two task definitions.
- Multi-region. Single region until a customer contractually requires otherwise.
- Self-hosted ASR. Interface makes it a swap; see [ADR-0003](../adr/0003-managed-asr-behind-an-interface.md).
- Real-time collaboration on the transcript page.
- Any model fine-tuning.
- Video proxy generation — only needed once there is an in-browser review player,
  and the MVP deliverable is a downloadable timeline, not a preview.
