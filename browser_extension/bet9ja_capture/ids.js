/**
 * Deterministic, non-cryptographic ID generation for captured records.
 *
 * These IDs only need to be STABLE (same inputs -> same ID, so recapturing
 * the same fixture updates by identity instead of creating a duplicate) --
 * they are never used as a security boundary, so a fast, dependency-free
 * hash (FNV-1a, run twice with different seeds and concatenated) is enough.
 * This mirrors ledgers/ids.py's own "stable ID from a natural key" pattern
 * on the Python side, without requiring the Web Crypto API (which is async
 * and would force every caller of this module to become async too).
 */
(function (root) {
  function fnv1a(str, seed) {
    let hash = seed >>> 0;
    for (let i = 0; i < str.length; i += 1) {
      hash ^= str.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(16).padStart(8, '0');
  }

  /** Canonicalize a natural key (array of strings/numbers/null) into one
   * stable string -- '|' separated, with each part's own length prefixed
   * so that ["ab", "c"] can never collide with ["a", "bc"]. */
  function canonicalKey(parts) {
    return parts
      .map((part) => {
        const text = part === null || part === undefined ? '' : String(part);
        return `${text.length}:${text}`;
      })
      .join('|');
  }

  function stableId(prefix, parts) {
    const key = canonicalKey(parts);
    const hex = fnv1a(key, 0x811c9dc5) + fnv1a(key, 0x12345679);
    return `${prefix}_${hex}`;
  }

  function randomHex(byteLength) {
    const bytes = new Uint8Array(byteLength);
    if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
      crypto.getRandomValues(bytes);
    } else {
      for (let i = 0; i < byteLength; i += 1) {
        bytes[i] = Math.floor(Math.random() * 256);
      }
    }
    return Array.from(bytes)
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('');
  }

  /** capture_id identifies one *run* of the extension, not a fixture --
   * it deliberately mixes in randomness (unlike fixture_id) since two
   * captures of the same page one second apart are still two distinct
   * captures, not the same logical record. */
  function captureId(capturedAtUtc) {
    const compactTimestamp = capturedAtUtc.replace(/[^0-9]/g, '');
    return `cap_${compactTimestamp}_${randomHex(4)}`;
  }

  const api = { stableId, canonicalKey, captureId };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaIds = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
