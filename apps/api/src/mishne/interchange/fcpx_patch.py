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
    _patch_format_name(fcpx_xml)
    _patch_element_lookups(fcpx_xml)
    return True


def _patch_element_lookups(fcpx_xml) -> None:
    """Find elements by comparing attributes, not by interpolating XPath.

    The adapter looks up the elements it has already written like this:

        def _asset_by_path(self, path):
            return self.resource_element.find(f"./asset[@src='{path}']")

        def _asset_clip_by_name(self, name):
            return self.event_resource.find(f"./asset-clip[@name='{name}']")

    The value being interpolated is a filename. `RUSHES Tia Mowry talks 'My
    Next Act,'` closes the predicate's quote halfway through, and ElementTree
    raises `SyntaxError: invalid predicate` from inside the writer — so the
    FCPXML is the one deliverable a job produces for an apostrophe in a
    filename, which is to say it produces none.

    This never fired while the artifacts named the *staged* copy, because
    `workspace._safe_name` had already replaced the apostrophe with an
    underscore on the way to disk. Writing the customer's real filename is the
    fix for relink and it is what walks into this.

    An escaping helper would be the smaller patch and the wrong one: XPath 1.0
    has no escape for a quote inside a string literal. Comparing the attribute
    directly is both simpler and total.
    """
    if getattr(fcpx_xml.FcpxOtio._asset_by_path, "_mishne", False):
        return

    def by_attr(root, tag: str, attr: str, value):
        for element in root.findall(f"./{tag}"):
            if element.get(attr) == value:
                return element
        return None

    def _asset_by_path(self, path):
        return by_attr(self.resource_element, "asset", "src", path)

    def _asset_clip_by_name(self, name):
        return by_attr(self.event_resource, "asset-clip", "name", name)

    def _media_by_name(self, name):
        return by_attr(self.resource_element, "media", "name", name)

    _asset_by_path._mishne = True
    fcpx_xml.FcpxOtio._asset_by_path = _asset_by_path
    fcpx_xml.FcpxOtio._asset_clip_by_name = _asset_clip_by_name
    fcpx_xml.FcpxOtio._media_by_name = _media_by_name


def _patch_format_name(fcpx_xml) -> None:
    """Name the `<format>` from the probe we already ran, not from the path.

    The adapter builds `FFVideoFormat640x360p25.0` by shelling out to ffprobe
    against `media_reference.target_url`:

        path = path.replace("file://", "")
        if not os.path.exists(path):
            return ""

    That worked only for as long as the artifacts named a real local file, and
    naming a real local file is precisely the bug in `assemble.media_url` — a
    worker's copy is in a scratch directory nobody else has. With a relative URL
    the probe misses, the format loses its name, and Premiere and Resolve are
    handed a sequence with no stated raster.

    Stage 0 already probed the media properly. `_media_clips` puts the frame
    size on the media reference, so read it from there and fall back to the
    adapter's own behaviour when it is absent — an AAF-sourced clip, or audio.
    """
    original = fcpx_xml.FcpxOtio._clip_format_name
    # `apply()` is called once per emit, and this one wraps rather than
    # replaces: without the guard each call would add another layer.
    if getattr(original, "_mishne", False):
        return

    def _clip_format_name(self, clip):
        try:
            size = clip.media_reference.metadata.get("mishne", {})
            w, h = int(size.get("width", 0)), int(size.get("height", 0))
        except Exception:  # noqa: BLE001 — a Stack, a Track, no reference
            w = h = 0
        if w and h:
            return f"FFVideoFormat{w}x{h}p{round(float(clip.duration().rate), 2)}"
        return original(self, clip)

    _clip_format_name._mishne = True
    fcpx_xml.FcpxOtio._clip_format_name = _clip_format_name
