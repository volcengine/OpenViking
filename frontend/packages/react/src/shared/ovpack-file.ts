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
