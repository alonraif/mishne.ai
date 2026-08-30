"""Handles default to none, and every entry point agrees about it.

A cut is a frame-accurate editorial decision. Handles do not add trim room
here — they extend the region that plays, which `refine._merge`'s own docstring
admits when it explains that consecutive beats overlap once handles are added.
Trim room comes from the AAF relinking to the original source (ADR-0001), where
the whole rushes sit behind every clip.

Measured on SyncDaniel: six frames each side put 1.7s of unasked-for material
into a 50s cut, and the handle-induced overlaps forced two extra clips at source
joins.

The second test is the one that matters long-term. This value is declared in
six places — the CLI, the brief, the API's brief, the schema, the orchestrator's
request and the refine functions — and nothing stopped them drifting apart. A
job submitted through the API and the same job run from the CLI would then cut
differently, which is the sort of difference nobody notices until an editor asks
why the web version is looser than the one you showed them.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.orchestration.graph import JobRequest  # noqa: E402
from mishne.pipeline.steps import refine  # noqa: E402
from mishne.pipeline.steps.brief import EditBrief  # noqa: E402
from mishne.schemas import EditBrief as EditBriefSchema  # noqa: E402


def _default(fn) -> int:
    return inspect.signature(fn).parameters["handle_frames"].default


def test_nothing_adds_handles_unless_asked():
    assert _default(refine.refine) == 0
    assert _default(refine.refine_multi) == 0
    assert EditBrief(target_duration_s=120).handle_frames == 0
    assert JobRequest.__dataclass_fields__["handle_frames"].default == 0
    assert EditBriefSchema.model_fields["handle_frames"].default == 0


def test_every_entry_point_agrees_on_the_default():
    """Six declarations of one number, and no mechanism keeping them in step.

    The CLI's default is read separately from the rest because it is an
    argparse default rather than a Python one, and it is the one most likely to
    be forgotten — `run.py` is the specification the orchestrator is tested
    against (`test_reference_run.py`), so a CLI that disagrees makes the
    reference itself wrong.
    """
    import ast

    source = (Path(__file__).parent.parent / "run.py").read_text()
    tree = ast.parse(source)
    cli_default = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "--handles"):
            continue
        for kw in node.keywords:
            if kw.arg == "default":
                cli_default = ast.literal_eval(kw.value)
    assert cli_default is not None, "run.py no longer declares --handles"

    defaults = {
        "run.py --handles": cli_default,
        "refine.refine": _default(refine.refine),
        "refine.refine_multi": _default(refine.refine_multi),
        "EditBrief": EditBrief(target_duration_s=120).handle_frames,
        "JobRequest": JobRequest.__dataclass_fields__["handle_frames"].default,
        "schemas.EditBrief": EditBriefSchema.model_fields["handle_frames"].default,
    }
    assert len(set(defaults.values())) == 1, defaults


def test_handles_are_still_available_when_a_delivery_wants_them():
    """Removed as a default, not as a feature."""
    assert EditBrief(target_duration_s=120, handle_frames=12).handle_frames == 12
