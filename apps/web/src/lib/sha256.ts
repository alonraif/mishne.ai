/**
 * Incremental SHA-256, because the API asks for a content hash and the browser
 * cannot give it one any other way.
 *
 * `crypto.subtle.digest` takes a whole buffer. A three-hour ProRes master is
 * 200 GB and there is no streaming variant of the WebCrypto digest API, so
 * hashing one that way is a tab that dies. This is the standard block function,
 * fed a chunk at a time from `File.slice()` — the same read the upload is doing
 * anyway, which is why the digest costs nothing extra.
 *
 * Why the hash matters at all: it is the asset id, and therefore the cache key
 * for stages 0-4 including transcription. Under a filename-and-size id, a
 * re-export that happens to land on the same byte count reads a cache built
 * from different audio, and the only symptom is a cut whose words do not match
 * the picture.
 *
 * Verified against Node's `crypto.createHash("sha256")` over random inputs at
 * every buffer-boundary length; see the note in the API's HANDOVER.
 */

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

const rotr = (x: number, n: number) => (x >>> n) | (x << (32 - n));

export class Sha256 {
  private h = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  private readonly block = new Uint8Array(64);
  private readonly w = new Uint32Array(64);
  private blockLength = 0;
  private bytesHashed = 0;

  update(data: Uint8Array): this {
    this.bytesHashed += data.length;
    let offset = 0;
    // Top up a partial block first, then run whole blocks straight out of the
    // input without copying them.
    if (this.blockLength > 0) {
      const take = Math.min(64 - this.blockLength, data.length);
      this.block.set(data.subarray(0, take), this.blockLength);
      this.blockLength += take;
      offset = take;
      if (this.blockLength === 64) {
        this.compress(this.block, 0);
        this.blockLength = 0;
      }
    }
    while (data.length - offset >= 64) {
      this.compress(data, offset);
      offset += 64;
    }
    if (offset < data.length) {
      this.block.set(data.subarray(offset), 0);
      this.blockLength = data.length - offset;
    }
    return this;
  }

  /** Lower-case hex, which is the form the API validates. */
  digestHex(): string {
    const total = this.bytesHashed;
    // Padding: a 1 bit, zeros, then the message length in bits as a 64-bit
    // big-endian integer.
    const tail = new Uint8Array(this.blockLength < 56 ? 64 : 128);
    tail.set(this.block.subarray(0, this.blockLength), 0);
    tail[this.blockLength] = 0x80;
    const bits = BigInt(total) * 8n;
    const view = new DataView(tail.buffer);
    view.setBigUint64(tail.length - 8, bits, false);
    for (let i = 0; i < tail.length; i += 64) this.compress(tail, i);

    let out = "";
    for (let i = 0; i < 8; i++) out += this.h[i].toString(16).padStart(8, "0");
    return out;
  }

  private compress(data: Uint8Array, offset: number): void {
    const w = this.w;
    for (let i = 0; i < 16; i++) {
      const j = offset + i * 4;
      w[i] = ((data[j] << 24) | (data[j + 1] << 16) | (data[j + 2] << 8) | data[j + 3]) >>> 0;
    }
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }

    let [a, b, c, d, e, f, g, h] = this.h;
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (h + S1 + ch + K[i] + w[i]) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) >>> 0;
      h = g; g = f; f = e;
      e = (d + t1) >>> 0;
      d = c; c = b; b = a;
      a = (t1 + t2) >>> 0;
    }
    const h0 = this.h;
    h0[0] = (h0[0] + a) >>> 0;
    h0[1] = (h0[1] + b) >>> 0;
    h0[2] = (h0[2] + c) >>> 0;
    h0[3] = (h0[3] + d) >>> 0;
    h0[4] = (h0[4] + e) >>> 0;
    h0[5] = (h0[5] + f) >>> 0;
    h0[6] = (h0[6] + g) >>> 0;
    h0[7] = (h0[7] + h) >>> 0;
  }
}

/** How much of a file is read at a time while hashing. */
export const HASH_CHUNK_BYTES = 8 * 1024 * 1024;

/**
 * Hash a file the browser is about to upload, without holding it in memory.
 *
 * `onProgress` exists because on a large file this is a visible wait, and a
 * progress bar that sits at zero while something reads 200 GB reads as a hang.
 */
export async function hashFile(
  file: Blob,
  onProgress?: (bytesRead: number, total: number) => void,
  signal?: AbortSignal
): Promise<string> {
  const hash = new Sha256();
  for (let offset = 0; offset < file.size; offset += HASH_CHUNK_BYTES) {
    if (signal?.aborted) throw new DOMException("aborted", "AbortError");
    const slice = file.slice(offset, Math.min(offset + HASH_CHUNK_BYTES, file.size));
    hash.update(new Uint8Array(await slice.arrayBuffer()));
    onProgress?.(Math.min(offset + HASH_CHUNK_BYTES, file.size), file.size);
  }
  return hash.digestHex();
}
