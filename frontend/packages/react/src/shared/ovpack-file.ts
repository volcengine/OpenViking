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
  const tail = new DataView(
    await file
      .slice(Math.max(0, file.size - endRecordSize - 0xffff))
      .arrayBuffer(),
  );
  for (let offset = tail.byteLength - endRecordSize; offset >= 0; offset--) {
    if (
      tail.getUint32(offset, true) === 0x06054b50 &&
      tail.getUint16(offset + 10, true) > 0 &&
      offset + endRecordSize + tail.getUint16(offset + 20, true) ===
        tail.byteLength
    )
      return true;
  }
  return false;
}
