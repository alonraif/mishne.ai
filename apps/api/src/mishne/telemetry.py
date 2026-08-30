"""Traces: where a job spent its time, and why a fast one was fast.

## What this is for

`job_steps` records that a stage ran and how long it took, and answers "what
happened to job X" perfectly well. What it cannot answer is "what is happening
across all jobs right now", "is this transcription slow or is every
transcription slow", and "which stage regressed after Tuesday's release" —
those are questions about a distribution, not a row, and they are why a trace
backend exists.

The step boundaries already exist in the runner (B3), so this is instrumentation
rather than restructuring: one span per stage, correlated by `job_id`, with the
per-asset phase nested under the job.

## No vendor here

Anything OTel-compatible. `otel_exporter` chooses `otlp` (an endpoint from
settings), `console` (a developer's terminal), or `none`. Nothing in this file
names a product, and the decision stays a deployment one — which is the point of
instrumenting before choosing, since the cost of not instrumenting compounds and
the cost of switching backends does not.

## Two things that are load-bearing

**The SDK is optional.** If `opentelemetry` is not installed, every function
here is a no-op and the pipeline runs exactly as before. A worker that will not
start because a telemetry package is missing is a worse outcome than a worker
with no telemetry, and the test suite must not need the dependency.

**Span attributes go through `scrub`.** A trace is a log that a vendor holds.
`logging.scrub` is the enforcement point for the content rule and it only ever
saw log events; attributes set directly on a span would have walked around it.
They do not. Nor does this module ever call `record_exception`, which attaches
an exception's message and stack trace — and a step's exception can quote a
filename, which is exactly why `runner.py` logs the type and not the message.
"""

from __future__ import annotations

from contextlib import contextmanager

from .logging import blocked, clean, get_logger

log = get_logger(__name__)

try:  # pragma: no cover - exercised by whether the package is installed
    from opentelemetry import trace as _otel_trace

    _AVAILABLE = True
except ImportError:  # pragma: no cover
    _otel_trace = None
    _AVAILABLE = False

#: Set by `configure`. Until then every span here is a no-op, which is also the
#: permanent state for `run.py` on a laptop.
_TRACER = None


def available() -> bool:
    return _AVAILABLE


def configured() -> bool:
    return _TRACER is not None


def configure(settings=None, *, service: str = "mishne-worker") -> bool:
    """Install a tracer provider. Returns whether tracing is now on.

    Safe to call more than once and safe to call when the SDK is absent; both
    are no-ops that return False rather than raising, because the caller is a
    worker whose job is to cut video.
    """
    global _TRACER

    from .config import get_settings

    settings = settings or get_settings()
    exporter_name = getattr(settings, "otel_exporter", "none")
    if exporter_name == "none" or not _AVAILABLE:
        _TRACER = None
        return False

    try:  # pragma: no cover - needs the optional dependency
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

        if exporter_name == "otlp":
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=settings.otel_endpoint)
        else:
            exporter = ConsoleSpanExporter()

        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": service,
                    "deployment.environment": settings.environment,
                }
            ),
            # 100% is affordable at today's volume and will not stay that way.
            # The ratio is a setting so that the day it stops being affordable
            # is a config change rather than a release.
            sampler=TraceIdRatioBased(settings.otel_sample_ratio),
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        _otel_trace.set_tracer_provider(provider)
        _TRACER = _otel_trace.get_tracer("mishne")
        return True
    except Exception as exc:  # noqa: BLE001 - telemetry never fails a job
        log.warning("telemetry.configure_failed", reason=type(exc).__name__)
        _TRACER = None
        return False


class _NoSpan:
    """What callers get when tracing is off. Accepts everything, records nothing."""

    def set(self, **attributes) -> None:
        return None

    def failed(self, exc: BaseException) -> None:
        return None


class _Span:
    def __init__(self, span) -> None:
        self._span = span

    def set(self, **attributes) -> None:
        for key, value in attributes.items():
            if value is None:
                continue
            # The same rule as logs, and for the same reason. A key that would
            # be redacted in a log line is not recorded at all here: a span
            # attribute of "<redacted>" is a field carrying no information and
            # an invitation to relax the rule to make it useful.
            if blocked(key):
                continue
            if isinstance(value, (dict, list, tuple)):
                value = str(clean(value))
            self._span.set_attribute(key, value)

    def failed(self, exc: BaseException) -> None:
        """Mark the span failed by exception TYPE.

        Deliberately not `record_exception`: that attaches the message and the
        stack trace, and a step's exception can quote a filename — which is the
        whole reason `step.failed` logs a type. A trace held by a vendor is not
        a safer place for it than a log.
        """
        from opentelemetry.trace import Status, StatusCode

        self._span.set_attribute("error.type", type(exc).__name__)
        self._span.set_status(Status(StatusCode.ERROR, type(exc).__name__))


@contextmanager
def span(name: str, **attributes):
    """One span, or nothing at all if tracing is off."""
    if _TRACER is None:
        yield _NoSpan()
        return
    with _TRACER.start_as_current_span(name) as raw:
        wrapped = _Span(raw)
        wrapped.set(**attributes)
        try:
            yield wrapped
        except BaseException as exc:
            wrapped.failed(exc)
            raise


def reset() -> None:
    """Forget the provider. For tests, which must not leak one into the next."""
    global _TRACER
    _TRACER = None
