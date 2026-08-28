"""Workaround: otio-fcpx-xml-adapter cannot write NTSC frame rates.

## The bug

`fcpx_xml.FcpxOtio._framerate_to_frame_duration` looks a rate up in a table
keyed by *rounded* values:

    FRAMERATE_FRAMEDURATION = {23.98: "1001/24000s", 24: "25/600s",
                               25: "1/25s", 29.97: "1001/30000s", ...}

    frame_duration = FRAMERATE_FRAMEDURATION.get(int(framerate), "")
    if not frame_duration:
        frame_duration = FRAMERATE_FRAMEDURATION.get(float(framerate), "")

OTIO carries the true rational rate, so it passes 23.976023976023978, not
23.98, and 29.97002997002997, not 29.97. Both lookups miss:

    int(23.976...) -> 23   not a key
    float(23.976...)       not a key (the key is 23.98)

The miss returns `""`, and the failure surfaces much later, somewhere else
entirely, as:

    ValueError: not enough values to unpack (expected 2, got 1)

...when `"".split("/")` is unpacked into two variables.

## Why this matters more than it looks

The integer rates work, so the adapter passes a casual test at 25 or 30 and
fails on exactly the rates most North American broadcast material is shot at.
FCPXML is the delivery path for Premiere, Resolve and Final Cut — three of the
four target NLEs — so without this fix the spike cannot answer its own question
for 23.976 or 29.97.

It also inverts the risk assumption in docs/architecture/02: AAF was supposed to
be the fragile format and FCPXML the safe one. AAF passed at all four rates.

## The fix

Round to two decimal places before the lookup, which is the precision the
table's own keys are written at. `23.976023976 -> 23.98`, `29.97002997 -> 29.97`,
`59.94005994 -> 59.94`, and the integer rates are unaffected because `25.0 == 25`
as a dict key.

This is a monkey-patch so the spike stays runnable against the published
package. It belongs upstream — a pull request against
https://github.com/OpenTimelineIO/otio-fcpx-xml-adapter is a better long-term
answer than carrying a patch, and if mishne.ai ships FCPXML this needs to be
resolved properly rather than shimmed.
"""

from __future__ import annotations


def apply() -> bool:
    """Patch the adapter in place. Returns True if the patch was applied.

    Note the module lookup. OTIO's plugin system loads adapter modules itself,
    by path, so the object registered as the `fcpx_xml` adapter is *not* the
    same module object you get from `import otio_fcpx_xml_adapter.fcpx_xml`.
    Patching the imported one appears to work — the attribute is set, calling it
    directly returns the right answer — and changes nothing at all when the
    adapter runs. Reach the plugin's own module through the adapter registry.
    """
    import opentimelineio as otio

    try:
        fcpx_xml = otio.adapters.from_name("fcpx_xml").module()
    except Exception:  # noqa: BLE001 — adapter not installed
        return False

    table = fcpx_xml.FRAMERATE_FRAMEDURATION

    def _framerate_to_frame_duration(framerate):
        rate = float(framerate)
        for key in (rate, round(rate, 2), round(rate)):
            hit = table.get(key)
            if hit:
                return hit
        raise ValueError(
            f"No FCPXML frameDuration for rate {rate!r}. Known rates: "
            f"{sorted(table)}. Add it to FRAMERATE_FRAMEDURATION."
        )

    fcpx_xml.FcpxOtio._framerate_to_frame_duration = staticmethod(
        _framerate_to_frame_duration
    )
    return True
