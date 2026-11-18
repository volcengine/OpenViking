export function hasOvpackName(name: string) {
  return /\.ovpack$/i.test(name);
}

export async function hasOvpackHeader(file: Blob) {
  const header = new Uint8Array(await file.slice(0, 4).arrayBuffer());
  return (
    header[0] === 0x50 &&
    header[1] === 0x4b &&
    header[2] === 0x03 &&
    header[3] === 0x04
  );
}

/** Exported OVPack ZIPs contain a manifest, so they have at least one entry. */
export async function hasCompleteOvpack(file: Blob) {
  const endRecordSize = 22;
  const minimumZipSize = 30 + 46 + endRecordSize;
  if (file.size < minimumZipSize || !(await hasOvpackHeader(file)))
    return false;

  // ZIP and ZIP64 both end with this record, followed by an optional comment.
  const tailStart = Math.max(0, file.size - endRecordSize - 0xffff);
  const tail = new DataView(await file.slice(tailStart).arrayBuffer());
  for (let offset = tail.byteLength - endRecordSize; offset >= 0; offset--) {
    if (
      tail.getUint32(offset, true) === 0x06054b50 &&
      tail.getUint16(offset + 10, true) > 0 &&
      offset + endRecordSize + tail.getUint16(offset + 20, true) ===
        tail.byteLength
    ) {
      const endOffset = tailStart + offset;
      const size = tail.getUint32(offset + 12, true);
      const start = tail.getUint32(offset + 16, true);
      const count = tail.getUint16(offset + 10, true);
      const needsZip64 = size === 0xffffffff || start === 0xffffffff;
      const zip64 =
        needsZip64 || count === 0xffff
          ? await readZip64Directory(file, endOffset)
          : undefined;
      const directory = zip64 ?? {
        size: BigInt(size),
        start: BigInt(start),
        count: BigInt(count),
        endOffset,
      };
      if (
        (needsZip64 && !zip64) ||
        directory.start < 30n ||
        directory.count === 0n ||
        directory.size < directory.count * 46n ||
        directory.start + directory.size > BigInt(directory.endOffset)
      )
        continue;
      if (
        await hasCentralDirectory(
          file,
          Number(directory.start),
          Number(directory.start + directory.size),
          Number(directory.count),
        )
      )
        return true;
    }
  }
  return false;
}

async function hasCentralDirectory(
  file: Blob,
  start: number,
  end: number,
  count: number,
) {
  // Only the fixed-size header is needed. Bound memory even for large ZIP64 directories.
  const recordSize = 46;
  let position = start;
  let chunkStart = start;
  let chunk = new DataView(new ArrayBuffer(0));
  for (let index = 0; index < count; index++) {
    if (position + recordSize > end) return false;
    if (position + recordSize > chunkStart + chunk.byteLength) {
      chunkStart = position;
      chunk = new DataView(
        await file
          .slice(position, Math.min(end, position + 0x10000))
          .arrayBuffer(),
      );
    }
    const offset = position - chunkStart;
    if (chunk.getUint32(offset, true) !== 0x02014b50) return false;
    position +=
      recordSize +
      chunk.getUint16(offset + 28, true) + // Name
      chunk.getUint16(offset + 30, true) + // Extra fields
      chunk.getUint16(offset + 32, true); // Comment
  }
  return position === end;
}

async function readZip64Directory(file: Blob, endOffset: number) {
  const locatorOffset = endOffset - 20;
  if (locatorOffset < 0) return;
  const locator = new DataView(
    await file.slice(locatorOffset, endOffset).arrayBuffer(),
  );
  if (locator.getUint32(0, true) !== 0x07064b50) return;
  const recordOffset = locator.getBigUint64(8, true);
  if (recordOffset + 56n > BigInt(locatorOffset)) return;
  const position = Number(recordOffset);
  const record = new DataView(
    await file.slice(position, position + 56).arrayBuffer(),
  );
  if (record.getUint32(0, true) !== 0x06064b50) return;
  return {
    count: record.getBigUint64(32, true),
    size: record.getBigUint64(40, true),
    start: record.getBigUint64(48, true),
    endOffset: position,
  };
}
