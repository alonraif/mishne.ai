"""Naming voices, and saying that two of them are one person.

The pipeline knows which microphone a voice came down and nothing about who was
in front of it. Everything in this file is a person's judgement being recorded —
which is why none of it is ever written by the pipeline, and why
`db/transcripts.record_asset` sets `label` and `confirmed` on insert and never
on update.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from . import models as m


def rename(
    s: Session,
    org_id: str,
    members: list[tuple[str, str]],
    label: str,
) -> int:
    """Name every row a canonical voice is made of. Returns rows changed.

    `confirmed` follows the name: a person typing one has confirmed the voice,
    and clearing it puts the voice back to its default label unconfirmed rather
    than leaving it confirmed-as-nothing.
    """
    table = m.Speaker.__table__
    changed = 0
    for asset_id, speaker_id in members:
        result = s.execute(
            sa.update(table)
            .where(
                table.c.org_id == org_id,
                table.c.asset_id == asset_id,
                table.c.speaker_id == speaker_id,
            )
            .values(label=label, confirmed=bool(label))
        )
        changed += result.rowcount or 0
    return changed


def merge(
    s: Session,
    org_id: str,
    project_id: str,
    canonical_speaker_id: str,
    members: list[tuple[str, str]],
    *,
    confirmed_by: str | None = None,
) -> None:
    """Record that these voices are one person, across uploads.

    A link per member, including the canonical one: without its own row, the
    voice that gives the group its name is the one voice not in the group, and
    `_canonical` would place it outside its own merge.

    Scoped to the project rather than the job. A person is the same person in
    every cut drawn from the same footage, and making the merge per job would
    ask an editor to redo it on every re-cut.
    """
    table = m.SpeakerLink.__table__
    for asset_id, speaker_id in members:
        s.execute(
            pg_insert(table)
            .values(
                id=f"spl_{project_id}_{asset_id}_{speaker_id}",
                org_id=org_id,
                project_id=project_id,
                canonical_speaker_id=canonical_speaker_id,
                asset_id=asset_id,
                speaker_id=speaker_id,
                confirmed_by=confirmed_by,
                confirmed_at=sa.func.now(),
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "canonical_speaker_id": sa.text(
                        "excluded.canonical_speaker_id"
                    ),
                    "confirmed_by": sa.text("excluded.confirmed_by"),
                    "confirmed_at": sa.text("excluded.confirmed_at"),
                },
            )
        )


def unmerge(s: Session, org_id: str, project_id: str,
            members: list[tuple[str, str]]) -> None:
    """Undo a merge. The rows go; the speakers and their names stay."""
    table = m.SpeakerLink.__table__
    for asset_id, speaker_id in members:
        s.execute(
            sa.delete(table).where(
                table.c.org_id == org_id,
                table.c.project_id == project_id,
                table.c.asset_id == asset_id,
                table.c.speaker_id == speaker_id,
            )
        )
