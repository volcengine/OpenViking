/**
 * Incremental reader for one agent's `wire.jsonl`.
 *
 * The file is append-only JSONL written by a live process, so a read can end
 * anywhere: mid-line, mid-record, mid-turn. This reader therefore only ever
 * consumes newline-terminated lines and returns the byte offset it stopped at.
 * A half-written tail is not "no data" and not "corrupt" — it is the next
 * read's beginning.
 *
 * Four other facts about the file decide the rest of the design:
 *
 * - The first line is `metadata` carrying `protocol_version` and
 *   `min_protocol_version`. A reader older than `min_protocol_version` must
 *   stop rather than interpret records it does not know; it keeps its cursor so
 *   a newer plugin can pick the file up. A file whose first line is not
 *   metadata is legacy, and this plugin does not guess 1.5 semantics for it.
 * - The file can be replaced or truncated (a fork writes a new one, a repair
 *   rewrites one). The reader keeps the identity of the file it was reading —
 *   device, inode, size — plus a prefix fingerprint, so a swap is detected
 *   instead of silently resumed at an offset that means something else now.
 * - Each scan is bounded. A multi-megabyte file must not make one hook read it
 *   all, and stopping at the budget must not lose the tail: the returned cursor
 *   is where the next call continues.
 * - Line numbers are the branch projection's coordinates, so they are carried
 *   with each record and must be stable across reads. They are absolute line
 *   numbers in the file, not offsets in this window.
 */

import { createHash } from "node:crypto";
import { open } from "node:fs/promises";

/** Per-scan byte budget. The design's cap; a hook must not read more. */
export const DEFAULT_SCAN_LIMIT_BYTES = 32 * 1024 * 1024;

/** Bytes of the file start kept for the replacement fingerprint. */
const PREFIX_FINGERPRINT_BYTES = 4096;

/** How many bytes are read from the file per `read()` call. */
const CHUNK_BYTES = 256 * 1024;

function parseVersion(value) {
  if (typeof value !== "string") return null;
  const match = value.trim().match(/^(\d+)(?:\.(\d+))?(?:\.(\d+))?/);
  if (!match) return null;
  return [Number(match[1]), Number(match[2] || 0), Number(match[3] || 0)];
}

/** Compare two dotted versions numerically, segment by segment. */
export function compareProtocolVersions(a, b) {
  const left = typeof a === "string" ? parseVersion(a) : a;
  const right = typeof b === "string" ? parseVersion(b) : b;
  // An unparseable version is "cannot tell", not "equal". Returning 0 would let
  // a malformed `min_protocol_version` pass the compatibility gate and the
  // reader would interpret records it does not understand.
  if (!left || !right) return null;
  for (let i = 0; i < 3; i += 1) {
    if (left[i] !== right[i]) return left[i] < right[i] ? -1 : 1;
  }
  return 0;
}

/** The protocol this reader understands. */
export const READER_PROTOCOL_VERSION = "1.5";

/**
 * What the reader remembers about one file between calls.
 *
 * `line` is the number of the last consumed line, so the next line is
 * `line + 1`. `offset` is the byte position just past its newline.
 */
export function emptyReaderState({ wirePath = "", scanOffset = 0, line = 0 } = {}) {
  return {
    wirePath,
    scanOffset,
    line,
    protocolVersion: undefined,
    minProtocolVersion: undefined,
    legacy: false,
    incompatible: false,
    fileIdentity: undefined,
    prefixHash: undefined,
    prefixLength: undefined,
    fileSize: undefined,
  };
}

/**
 * Hash exactly `length` bytes from the start of the file.
 *
 * The window has to be a fixed length, not "up to 4 KB": a small wire file
 * grows on every turn, and a fingerprint over the whole of it would change with
 * every append — making every read look like a file replacement. So the stored
 * length is part of the record, and the comparison hashes exactly that many
 * bytes of the new file. If the file grew, the same bytes are still there.
 */
async function fingerprint(handle, length) {
  if (length <= 0) return createHash("sha256").update("").digest("hex").slice(0, 16);
  const buffer = Buffer.alloc(length);
  const { bytesRead } = await handle.read(buffer, 0, length, 0);
  return createHash("sha256").update(buffer.subarray(0, bytesRead)).digest("hex").slice(0, 16);
}

/**
 * Read new complete records from `wirePath`.
 *
 * Returns the records read this call, the reader state to persist, and a status
 * that the caller must act on:
 *
 *   ok            records were read (possibly none, if nothing was appended)
 *   missing       the file does not exist (yet)
 *   unreadable    an I/O error other than ENOENT
 *   incompatible  `min_protocol_version` is newer than this reader
 *   legacy        the first line is not metadata; not parsed as 1.5
 *   reset         the file was replaced or truncated; the caller restarts at
 *                 `state.scanOffset` and must rely on stable ids to dedupe
 *
 * `reset` is a status rather than an error because it is expected: a fork or a
 * repair rewrites the file, and the caller has a defined recovery (stable ids).
 */
export async function readWireRecords(wirePath, state = emptyReaderState({ wirePath }), {
  limitBytes = DEFAULT_SCAN_LIMIT_BYTES,
  // Where to start. A capture stage re-reads from the earliest turn it has not
  // yet acknowledged, because a projection needs that turn's opening records
  // (its prompt, its steps) and not just the newest line. `startLine` keeps the
  // absolute line numbering, which is the branch tree's coordinate system.
  startOffset = undefined,
  startLine = undefined,
} = {}) {
  // The line number is the branch tree's coordinate. A window that moves the
  // bytes without moving the numbering (or the reverse) mislabels every record
  // after it, and the projection then attributes turns to the wrong branch.
  const hasOffset = Number.isFinite(startOffset) && startOffset >= 0;
  const hasLine = Number.isFinite(startLine) && startLine >= 1;
  if (hasOffset !== hasLine) {
    throw new Error("readWireRecords: startOffset and startLine must be provided together");
  }
  const next = { ...state, wirePath };
  let handle;
  try {
    handle = await open(wirePath, "r");
  } catch (error) {
    if (error?.code === "ENOENT") return { status: "missing", records: [], state: next };
    return { status: "unreadable", records: [], state: next, error: error?.code || "open_failed" };
  }

  try {
    const info = await handle.stat();
    if (!info.isFile()) return { status: "unreadable", records: [], state: next, error: "not_a_file" };

    const identity = `${info.dev}:${info.ino}`;
    // The window is fixed at the first 4 KB. `prefixLength` is remembered so a
    // file that was shorter on the previous read is hashed over the same bytes
    // it had then, not over everything it has now.
    const prefixLength = Math.min(
      Number.isFinite(next.prefixLength) ? next.prefixLength : PREFIX_FINGERPRINT_BYTES,
      info.size,
    );
    const prefix = await fingerprint(handle, prefixLength);
    let resetThisRead = false;
    const identityChanged = next.fileIdentity !== undefined
      && (next.fileIdentity !== identity || next.prefixHash !== prefix);
    const truncated = info.size < (next.scanOffset || 0);

    let offset = next.scanOffset || 0;
    let line = next.line || 0;
    if (identityChanged || truncated) {
      // Replaced or shortened: everything read before belongs to a different
      // file. Start over; duplicate suppression is the caller's job.
      offset = 0;
      line = 0;
      next.protocolVersion = undefined;
      next.minProtocolVersion = undefined;
      next.legacy = false;
      next.incompatible = false;
      resetThisRead = true;
    }
    next.fileIdentity = identity;
    next.prefixHash = prefix;
    next.prefixLength = prefixLength;
    next.fileSize = info.size;

    if (next.incompatible && !resetThisRead) {
      // Keep the cursor, report the same verdict; do not parse further.
      return { status: "incompatible", records: [], state: next };
    }

    const windowStart = resetThisRead
      ? 0
      : (Number.isFinite(startOffset) && startOffset >= 0 ? startOffset : offset);
    // `startLine` is the 1-based number of the first record the window will
    // read, which is the numbering the caller persisted. The reader's own
    // counter is the number of lines already consumed, so it is one less.
    const windowLine = resetThisRead
      ? 0
      : (Number.isFinite(startLine) && startLine >= 1 ? startLine - 1 : line);
    if (windowStart < offset || windowLine < line) {
      // A window that begins before the last read re-reads lines this process
      // has already numbered. The caller's `startLine` is authoritative there,
      // so both the position and the numbering move back together.
      offset = windowStart;
      line = windowLine;
    }

    const records = [];
    let scanned = 0;
    let position = offset;
    let carry = Buffer.alloc(0);
    let carryStart = offset;
    let stoppedForBudget = false;
    // A line can be split anywhere, including inside a multi-byte character.
    // Decoding each chunk on its own would turn the halves into replacement
    // characters and corrupt the record; a streaming decoder holds the partial
    // sequence until the rest of it arrives. Bytes are tracked separately from
    // characters so a line's offset stays exact.
    const decoder = new TextDecoder("utf-8", { fatal: false });

    const takeLine = (bytes, at) => {
      const raw = decoder.decode(bytes).replace(/\r$/, "");
      const lineNumber = line + 1;
      const nextOffset = at + bytes.length + 1;
      line = lineNumber;

      if (!raw.trim()) {
        offset = nextOffset;
        // The protocol gate lives on line 1. A blank first line is not
        // metadata, so the file is legacy — skipping the gate would let the
        // reader interpret a file whose protocol it never established.
        if (lineNumber === 1) {
          next.legacy = true;
          firstLineVerdict = { status: "legacy", line: lineNumber };
        }
        return;
      }

      let record;
      try {
        record = JSON.parse(raw);
      } catch {
        if (lineNumber === 1) {
          // Same rule: an unparseable first line cannot be metadata.
          next.legacy = true;
          firstLineVerdict = { status: "legacy", line: lineNumber };
          return;
        }
        // A complete line that is not JSON is a damaged record. The host does
        // not write those, so treat the line as consumed and count it.
        offset = nextOffset;
        records.push({ line: lineNumber, offset: at, endOffset: nextOffset, record: null, malformed: true });
        return;
      }
      if (!record || typeof record !== "object" || Array.isArray(record) || typeof record.type !== "string") {
        offset = nextOffset;
        records.push({ line: lineNumber, offset: at, endOffset: nextOffset, record: null, malformed: true });
        return;
      }

      // The first line decides the protocol for the whole file.
      if (lineNumber === 1) {
        if (record.type !== "metadata") {
          next.legacy = true;
          firstLineVerdict = { status: "legacy", line: lineNumber };
          return;
        }
        const protocol = typeof record.protocol_version === "string" ? record.protocol_version : null;
        const minimum = typeof record.min_protocol_version === "string" ? record.min_protocol_version : null;
        if (protocol === null) {
          next.legacy = true;
          firstLineVerdict = { status: "legacy", line: lineNumber };
          return;
        }
        const versionOrder = minimum ? compareProtocolVersions(minimum, READER_PROTOCOL_VERSION) : 0;
        if (minimum && versionOrder === null) {
          // A version this reader cannot parse is not one it can claim to
          // understand: refuse rather than interpret records by guesswork.
          next.incompatible = true;
          next.protocolVersion = protocol;
          next.minProtocolVersion = minimum;
          firstLineVerdict = {
            status: "incompatible",
            protocolVersion: protocol,
            minProtocolVersion: minimum,
            reason: "unparseable_min_protocol_version",
          };
          return;
        }
        if (versionOrder !== null && versionOrder > 0) {
          next.incompatible = true;
          next.protocolVersion = protocol;
          next.minProtocolVersion = minimum;
          firstLineVerdict = {
            status: "incompatible",
            protocolVersion: protocol,
            minProtocolVersion: minimum,
          };
          return;
        }
        next.protocolVersion = protocol;
        next.minProtocolVersion = minimum ?? undefined;
      }

      offset = nextOffset;
      records.push({ line: lineNumber, offset: at, endOffset: nextOffset, record });
    };

    let firstLineVerdict = null;

    while (position < info.size && !firstLineVerdict) {
      if (scanned >= limitBytes) {
        stoppedForBudget = true;
        break;
      }
      const remaining = info.size - position;
      const length = Math.min(CHUNK_BYTES, remaining, limitBytes - scanned);
      if (length <= 0) break;
      const buffer = Buffer.alloc(length);
      const { bytesRead } = await handle.read(buffer, 0, length, position);
      if (bytesRead <= 0) break;
      scanned += bytesRead;
      const chunkStart = position;
      position += bytesRead;
      carry = carry.length === 0
        ? buffer.subarray(0, bytesRead)
        : Buffer.concat([carry, buffer.subarray(0, bytesRead)]);

      // `carry` always begins at `carryStart`, so a line's absolute byte offset
      // is that start plus its index within the carry.
      let newline;
      while ((newline = carry.indexOf(0x0a)) !== -1) {
        const lineBytes = carry.subarray(0, newline);
        takeLine(lineBytes, carryStart);
        carryStart += newline + 1;
        carry = carry.subarray(newline + 1);
        if (firstLineVerdict) break;
      }
      if (firstLineVerdict) break;
    }

    if (firstLineVerdict) {
      if (firstLineVerdict.status === "legacy") {
        return { status: "legacy", records: [], state: next, line: firstLineVerdict.line };
      }
      return {
        status: "incompatible",
        records: [],
        state: next,
        protocolVersion: firstLineVerdict.protocolVersion,
        minProtocolVersion: firstLineVerdict.minProtocolVersion,
      };
    }

    next.scanOffset = offset;
    next.line = line;

    return {
      status: "ok",
      records,
      state: next,
      bytesRead: scanned,
      stoppedForBudget,
      ...(resetThisRead ? { reset: true } : {}),
      windowStart,
      windowLine,
      // A caller that needs the file to be quiet (the ended sweep) compares
      // these two readings rather than trusting a single stat.
      size: info.size,
      mtimeMs: info.mtimeMs,
    };
  } catch (error) {
    return { status: "unreadable", records: [], state: next, error: error?.code || "read_failed" };
  } finally {
    await handle.close().catch(() => {});
  }
}

/**
 * Read the whole file for a one-shot projection.
 *
 * Used by doctor and by tests; a hook always uses the incremental reader so a
 * large wire never has to be held in memory. It returns the same
 * `{ line, record }` shape so both paths feed one projector.
 */
export async function readAllWireRecords(wirePath, { limitBytes = DEFAULT_SCAN_LIMIT_BYTES } = {}) {
  const result = await readWireRecords(wirePath, emptyReaderState({ wirePath }), { limitBytes });
  if (result.status !== "ok") return { ...result, records: [] };
  return result;
}
