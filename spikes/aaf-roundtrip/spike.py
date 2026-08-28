#!/usr/bin/env python3
"""Spike A — AAF round-trip.

Answers one question: can mishne.ai hand an editor a timeline their NLE opens,
relinks and plays at the right frame?

This is the highest technical risk in the project and it has nothing to do with
AI. See docs/architecture/05-roadmap-and-risks.md.

    python spike.py all                # everything, all four rates
    python spike.py all --rates 25     # one rate, faster
    python spike.py media              # just render test sources
    python spike.py build              # build timelines and export
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import checklist
import exporters
import fcpx_patch
import fcpxml_check
import timecode
import testmedia
import timeline as tl
from rates import RATES, Rate
from validate import verify

OUT = Path(__file__).parent / "out"

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
)


def tick(ok: bool) -> str:
    return f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"


def cmd_media(rates: list[Rate], codec: str) -> dict[str, Path]:
    media_dir = OUT / "media"
    paths = {}
    for rate in rates:
        print(f"  {rate.label:12} rendering… ", end="", flush=True)
        path = testmedia.generate(rate, media_dir, codec=codec)
        mb = path.stat().st_size / 1e6
        print(f"{GREEN}ok{RESET} {DIM}{path.name} ({mb:.0f} MB){RESET}")
        paths[rate.key] = path
    return paths


def cmd_build(rates: list[Rate], media: dict[str, Path]) -> int:
    failures = 0

    for rate in rates:
        print(f"\n{'=' * 66}\n {rate.label}\n{'=' * 66}")
        media_path = media[rate.key]
        timeline = tl.build(rate, media_path)
        n_clips = len(list(timeline.tracks[0].find_clips()))
        print(f"  timeline: {n_clips} clips, "
              f"{round(timeline.duration().value)} frames\n")

        out_dir = OUT / rate.key
        results = exporters.export_all(timeline, out_dir, rate.key)

        summary_rows = []
        print("  export")
        for res in results:
            if res.ok:
                print(f"    {tick(True)}  {res.fmt:8} {res.bytes:>9,} B")
                summary_rows.append(f"| {res.fmt} | written | {res.bytes:,} B |")
            else:
                failures += 1
                print(f"    {tick(False)}  {res.fmt:8} {RED}{res.error}{RESET}")
                summary_rows.append(f"| {res.fmt} | **FAILED** | {res.error} |")

        print("\n  round-trip")
        by_fmt = {f[0]: (f[1], f[4]) for f in exporters.FORMATS}
        for res in results:
            if not res.ok or res.path is None:
                continue
            if res.fmt == "FCPXML":
                # Not via the OTIO reader — it truncates NTSC rates to an
                # integer. See fcpxml_check for why that is the right call
                # regardless. 
                rt = fcpxml_check.verify(timeline, res.path, rate)
            else:
                adapter, flattens = by_fmt[res.fmt]
                # EDL has no embedded frame rate — see validate.verify().
                read_kwargs = {"rate": rate.fps} if res.fmt == "EDL" else {}
                rt = verify(timeline, res.path, res.fmt, adapter,
                            read_kwargs, flattens)
            if not rt.parsed:
                failures += 1
                print(f"    {tick(False)}  {res.fmt:8} "
                      f"{YELLOW}unreadable: {rt.error}{RESET}")
                continue
            print(f"    {tick(rt.ok)}  {res.fmt:8}")
            for chk in rt.checks:
                mark = f"{GREEN}·{RESET}" if chk.ok else f"{RED}x{RESET}"
                print(f"           {mark} {chk.name:16} {DIM}{chk.detail}{RESET}")
            if not rt.ok:
                failures += 1

        path = checklist.write(
            timeline, rate, media_path,
            out_dir / f"CHECKLIST-{rate.key}.md",
            "| Format | Result | Detail |\n|---|---|---|\n"
            + "\n".join(summary_rows),
        )
        print(f"\n  checklist: {DIM}{path.relative_to(OUT.parent)}{RESET}")

    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["all", "media", "build"])
    ap.add_argument("--rates", nargs="+", default=list(RATES),
                    choices=list(RATES), help="which frame rates to test")
    ap.add_argument("--codec", default="prores",
                    choices=list(testmedia.CODECS),
                    help="test media codec (h264 is fastest and smallest)")
    args = ap.parse_args()

    rates = [RATES[k] for k in args.rates]
    patched = fcpx_patch.apply()

    print(f"\n{'=' * 66}\n Spike A — AAF round-trip\n{'=' * 66}")
    if patched:
        print(f" {DIM}fcpx_xml NTSC frame-rate patch applied "
              f"(see fcpx_patch.py){RESET}")
    print(f" rates: {', '.join(r.label for r in rates)}")
    print(f" out:   {OUT}\n")

    # Timecode conversion underpins every number this spike produces. Verify it
    # before trusting any of them — a drop-frame bug here silently invalidates
    # the whole run, and it is cheap to rule out.
    print("timecode self-test")
    tc_bad = 0
    for r in rates:
        bad = timecode.self_test(r)
        print(f"  {r.label:12} {tick(not bad)}"
              + (f"  {RED}{bad[:2]}{RESET}" if bad else ""))
        tc_bad += len(bad)
    if tc_bad:
        print(f"\n {RED}Timecode conversion is broken. Nothing below is "
              f"trustworthy.{RESET}\n")
        return 1

    print("\ntest media")
    media = cmd_media(rates, args.codec)

    if args.command == "media":
        return 0

    failures = cmd_build(rates, media)

    print(f"\n{'=' * 66}")
    if failures:
        print(f" {RED}{failures} failure(s).{RESET} Automated checks did not pass.")
    else:
        print(f" {GREEN}Automated checks passed.{RESET}")
    print(f"\n {YELLOW}This proves the files are self-consistent. It does NOT{RESET}")
    print(f" {YELLOW}prove Media Composer will open them.{RESET} Work through the")
    print(" generated CHECKLIST-*.md in each NLE — that is the actual spike.")
    print(f"{'=' * 66}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
