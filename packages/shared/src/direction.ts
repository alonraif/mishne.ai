/**
 * Text direction.
 *
 * Hebrew is a first-class target, and RTL is not a CSS afterthought. Three
 * rules, learned the hard way from the transcript deliverable:
 *
 * 1. **Transcript text uses `dir="auto"`, per string.** A real transcript mixes
 *    Hebrew with Latin product names and numerals in the same sentence. Letting
 *    the browser decide per string handles that; a blanket `dir` on the page
 *    does not.
 * 2. **Timecode is forced LTR and isolated.** `10:02:14:00` dropped into an RTL
 *    paragraph without `unicode-bidi: isolate` reorders around the colons and
 *    becomes unreadable. Timecode is what an editor scans for, so it has to be
 *    right.
 * 3. **English UI strings inside an RTL container also need `dir="auto"`**, or
 *    their trailing full stop jumps to the front of the line.
 *
 * The app chrome stays LTR; only content flips.
 */

const RTL_RANGES: Array<[number, number]> = [
  [0x0590, 0x05ff], // Hebrew
  [0x0600, 0x06ff], // Arabic
  [0x0700, 0x074f], // Syriac
  [0x0750, 0x077f],
  [0x08a0, 0x08ff],
  [0xfb1d, 0xfdff],
  [0xfe70, 0xfeff],
];

const RTL_LANGUAGES = new Set(["he", "iw", "ar", "fa", "ur", "yi"]);

export function isRtlChar(ch: string): boolean {
  const cp = ch.codePointAt(0) ?? 0;
  return RTL_RANGES.some(([lo, hi]) => cp >= lo && cp <= hi);
}

/**
 * Fraction of *letters* that are RTL. Digits and punctuation are neutral and
 * deliberately excluded — a Hebrew sentence full of numbers is still Hebrew.
 */
export function rtlRatio(text: string): number {
  const letters = [...text].filter((c) => /\p{L}/u.test(c));
  if (letters.length === 0) return 0;
  return letters.filter(isRtlChar).length / letters.length;
}

/**
 * A threshold rather than a majority: a Hebrew sentence quoting an English
 * phrase is still a Hebrew sentence and should not flip because the quote ran
 * long.
 */
export function isRtlText(text: string, threshold = 0.3): boolean {
  return rtlRatio(text) >= threshold;
}

export function isRtlLanguage(code?: string | null): boolean {
  if (!code) return false;
  return RTL_LANGUAGES.has(code.split("-")[0].toLowerCase());
}

export function directionFor(language?: string | null): "rtl" | "ltr" {
  return isRtlLanguage(language) ? "rtl" : "ltr";
}
