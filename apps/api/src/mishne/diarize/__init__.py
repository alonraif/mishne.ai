"""Speaker diarization: who spoke when, when there is only one track."""

from .base import DiarizationProvider, DiarizationResult, Turn

__all__ = ["DiarizationProvider", "DiarizationResult", "Turn"]


def get_provider(name: str = "sherpa-onnx", **kwargs) -> DiarizationProvider:
    if name == "sherpa-onnx":
        from .sherpa_provider import SherpaDiarizer
        return SherpaDiarizer(**kwargs)
    raise ValueError(f"unknown diarization provider: {name}")
