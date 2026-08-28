"""Pipeline step registry.

Each entry is (name, human label). The orchestrator state machine is generated
from this list, and the UI renders progress from it, so the order here is the
order of the pipeline.
"""

STEPS: list[tuple[str, str]] = [
    ("prepare", "Probe and normalize"),
    ("audio", "Extract audio"),
    ("transcribe", "Transcribe with word timestamps"),
    ("vad", "Build silence map"),
    ("structure", "Structure into beats"),
    ("brief", "Compile edit brief"),
    ("score", "Score beats"),
    ("select", "Solve selection"),
    ("review", "Review sequence"),
    ("refine", "Refine cut points"),
    ("assemble", "Assemble timeline"),
    ("emit", "Generate artifacts"),
    ("validate", "Validate round-trip"),
]

__all__ = ["STEPS"]
