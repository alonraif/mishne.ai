"""Two id spaces, and the one place that converts between them.

**The pipeline names things after their content.** An asset's pipeline id is the
digest of its bytes (`project.asset_id_for`) and a beat's is that id plus its
position — `a_1f3c…_beat_0007`. That is deliberate and load-bearing: it is the
cache key for stages 0-4, so the same rushes uploaded to two projects are
transcribed once (ADR-0008), and two cuts of the same footage stay comparable.

**The database names things after rows.** `assets.id` is one upload, by one
customer, into one project; `transcripts`, `beats`, `speakers`, `selections`
and `beat_scores` all carry a foreign key to it.

The two are never the same string, and nothing converted between them. The
worker wrote the pipeline's ids straight into columns that reference
`assets.id`, every insert failed the foreign key, `worker._record_transcript`
swallowed the error exactly as it is designed to, and
`GET /v1/jobs/{id}/transcript` answered 404 for every job the product had
actually run. The symptom was a cut editor that opened empty on a job whose
progress panel said `awaiting_edit`, with nothing anywhere to say why.

Converting here, at the boundary, is the same choice `db/transcripts` already
makes for time: milliseconds become frames on the way in, and one origin lives
in the database.

## Why the beats are renamed too, and not just their asset column

A beat's pipeline id carries the *content* id, so two rows for the same bytes —
the same interview uploaded to two projects — produce identical beat ids.
`beats.id` is a primary key and the write is an upsert, so the second job would
take the first job's beat rows and move them to its own asset, emptying a
delivered job's transcript with no error. Namespacing every id on the asset row
makes that unreachable.
"""

from __future__ import annotations

from collections.abc import Mapping

#: A job's assets, as the pipeline's content id to the `assets.id` row it was
#: staged from. `worker.prepare_request` builds it; it is the only thing that
#: knows both halves.
AssetIds = Mapping[str, str]


def db_id(ident: str, assets: AssetIds) -> str:
    """A pipeline id, under the asset row it is stored against."""
    return _swap(ident, assets)


def pipeline_id(ident: str, assets: AssetIds) -> str:
    """A database id, back in the pipeline's own space.

    The direction a resumed job needs: `selections` holds the beat ids a person
    marked in the browser, and stage 8 has to find those beats among the ones
    the ingest cache just handed back.
    """
    return _swap(ident, {row: pipe for pipe, row in assets.items()})


def _swap(ident: str, prefixes: AssetIds) -> str:
    """Exchange a leading `{asset}_` for the same asset's id in the other space.

    An id carrying no asset prefix is returned unchanged. `run.py` on a single
    file passes `asset_id=""` and its beats are a bare `beat_0007`; every job
    the orchestrator runs sets it, and the orchestrator is the only path that
    reaches the database.
    """
    for old, new in prefixes.items():
        if old and ident.startswith(f"{old}_"):
            return f"{new}_{ident[len(old) + 1:]}"
    return ident
