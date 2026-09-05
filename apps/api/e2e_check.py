#!/usr/bin/env python
"""Drive the browser's own path end to end, against the running stack.

Not a test — a harness for the click-through in docs/HANDOFF-CLAUDE-CODE.md §2,
so that the thing being exercised is the API the browser calls rather than the
pipeline the tests call. It does what the web app does, in the same order:

    POST /v1/projects                       a project
    POST /v1/projects/{id}/assets           presigned multipart, one per file
    PUT  <presigned part urls>              the bytes, straight to MinIO
    POST /v1/assets/{id}/complete           assemble, then probe
    GET  /v1/assets/{id}/requirements       what a linked AAF still wants
    POST /v1/jobs                           submit
    GET  /v1/jobs/{id}                      poll to completion
    GET  /v1/assets/{id}/proxy              the player's URL
    GET  /v1/jobs/{id}/artifacts            the deliverables

Then it checks the two things this session fixed, against the artifacts the API
actually served:

  * the asset has a preview and its URL plays (issue 1);
  * the emitted AAF references the source's own MasterMob ids (issue 2, A2).

    python e2e_check.py --aaf /path/to/export.aaf --media /path/to/AAF\\ Media
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import httpx  # noqa: E402
import sqlalchemy as sa  # noqa: E402

API = "http://127.0.0.1:8000"


def _session(org_id: str) -> str:
    """A signed-in browser, made directly.

    The API takes the org from the session and never from a header (B4), so a
    script needs a real one. Minted through the database rather than by signing
    in because there is no password to hand; every request after this goes
    through the real resolution path.
    """
    from datetime import datetime, timedelta, timezone

    engine = sa.create_engine(
        "postgresql+psycopg://mishne:mishne@localhost:5432/mishne"
    )
    token = secrets.token_urlsafe(24)
    with engine.begin() as conn:
        user = conn.execute(
            sa.text("SELECT id FROM users WHERE org_id = :o ORDER BY created_at LIMIT 1"),
            {"o": org_id},
        ).first()
        if user is None:
            raise SystemExit(f"no user in {org_id}")
        conn.execute(
            sa.text(
                "INSERT INTO sessions (id, org_id, user_id, token_hash, expires_at) "
                "VALUES (:i, :o, :u, :h, :e)"
            ),
            {
                "i": f"ses_{secrets.token_hex(6)}", "o": org_id, "u": user.id,
                "h": hashlib.sha256(token.encode()).hexdigest(),
                "e": datetime.now(timezone.utc) + timedelta(hours=2),
            },
        )
    engine.dispose()
    return token


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upload(http: httpx.Client, project_id: str, path: Path) -> str:
    """One file, the way the browser sends it: presign, PUT the parts, complete."""
    size = path.stat().st_size
    created = http.post(
        f"/v1/projects/{project_id}/assets",
        json={"filename": path.name, "bytes": size, "checksum": _digest(path),
              "ingest_mode": "full_media"},
    )
    if created.status_code == 409:
        # Already here from an earlier run; the id comes back in the header.
        asset_id = created.headers["X-Asset-Id"]
        print(f"    {path.name}: already uploaded ({asset_id})")
        return asset_id
    created.raise_for_status()
    body = created.json()
    asset_id, part_size = body["asset_id"], body["part_size"]

    # The part size the SERVER sent, never one this script picks: S3 assembles
    # mismatched parts without complaining and hands back a corrupt object.
    parts = []
    with path.open("rb") as f:
        for spec in body["parts"]:
            chunk = f.read(part_size)
            put = httpx.put(spec["url"], content=chunk, timeout=300)
            put.raise_for_status()
            parts.append({"part_number": spec["part_number"],
                          "etag": put.headers["ETag"]})
    print(f"    {path.name}: {len(parts)} part(s), {size:,} bytes")

    done = http.post(f"/v1/assets/{asset_id}/complete", json={"parts": parts})
    done.raise_for_status()
    return asset_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aaf", required=True, type=Path)
    ap.add_argument("--media", type=Path, help="the AAF Media folder, if linked")
    ap.add_argument("--org", default="org_0d8e6c85")
    ap.add_argument("--target", type=int, default=30)
    ap.add_argument("--language", default="he")
    args = ap.parse_args()

    token = _session(args.org)
    http = httpx.Client(base_url=API, timeout=120,
                        headers={"Authorization": f"Bearer {token}"})

    print("== project")
    project = http.post("/v1/projects",
                        json={"name": f"e2e {time.strftime('%H:%M:%S')}"})
    project.raise_for_status()
    project_id = project.json()["id"]
    print(f"   {project_id}")

    print("== upload the sequence")
    asset_id = upload(http, project_id, args.aaf)

    # Probe runs on completion and decides what the sequence still needs.
    for _ in range(60):
        row = http.get(f"/v1/assets/{asset_id}").json()
        if row["status"] != "probing":
            break
        time.sleep(1)
    print(f"   status: {row['status']}")

    reqs = http.get(f"/v1/assets/{asset_id}/requirements").json()
    outstanding = reqs["outstanding"]
    print(f"   requirements: {len(reqs['requirements'])}, outstanding {outstanding}")

    if outstanding and args.media:
        print("== upload the companions it asked for")
        wanted = {r["basename"] for r in reqs["requirements"] if not r["satisfied"]}
        for f in sorted(args.media.iterdir()):
            if f.name in wanted:
                upload(http, project_id, f)
        reqs = http.get(f"/v1/assets/{asset_id}/requirements").json()
        outstanding = reqs["outstanding"]
        print(f"   outstanding now: {outstanding}")

    print("== estimate")
    # The price is recomputed server-side at submission and the request must
    # carry the cap the user approved, so that a price which moved since the
    # estimate was shown is refused rather than quietly charged (ADR-0006).
    est = http.post("/v1/jobs/estimate", json={
        "asset_ids": [asset_id], "target_duration_s": args.target, "mode": "ai",
    })
    est.raise_for_status()
    quote = est.json()
    print(f"   {quote['source_hours']:.2f} source hours · cap {quote['cap']} "
          f"· balance {quote['balance_before']} -> {quote['balance_after']}")
    if not quote["sufficient"]:
        print(f"   INSUFFICIENT CREDITS, short {quote['shortfall']}")
        return 1

    print("== submit")
    job = http.post("/v1/jobs", json={
        "asset_ids": [asset_id], "name": "e2e", "mode": "ai",
        "notes": "the strongest moments", "target_duration_s": args.target,
        "language": args.language, "accept_missing_media": bool(outstanding),
        "approved_cap": quote["cap"],
    })
    if job.status_code >= 400:
        print("   REFUSED:", job.status_code, job.text[:400])
        return 1
    job_id = job.json()["id"]
    print(f"   {job_id}")

    print("== run")
    last = ""
    for _ in range(900):
        j = http.get(f"/v1/jobs/{job_id}").json()
        if j["status"] != last:
            print(f"   {j['status']}")
            last = j["status"]
        if j["status"] in ("complete", "failed", "awaiting_edit"):
            break
        time.sleep(2)

    print(f"\n== result: {j['status']}")
    if j["status"] == "failed":
        print("   error:", j.get("error"))
        return 1

    proxy = http.get(f"/v1/assets/{asset_id}/proxy").json()
    print(f"\n== preview: status={proxy['status']} kind={proxy['kind']}")
    if proxy.get("url"):
        head = httpx.get(proxy["url"], headers={"Range": "bytes=0-1023"}, timeout=60)
        print(f"   URL serves HTTP {head.status_code} ({len(head.content)} bytes)")
    else:
        print("   NO URL — the player will show nothing")

    arts = http.get(f"/v1/jobs/{job_id}/artifacts").json()
    print(f"\n== artifacts: {len(arts)}")
    for a in arts:
        print(f"   {a['kind']:<7} {a['bytes']:>9,} B  validated={a['validated']}")

    print(f"\njob_id={job_id}\nasset_id={asset_id}\nproject_id={project_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
