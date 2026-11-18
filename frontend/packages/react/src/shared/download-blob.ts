export function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  try {
    link.click();
  } finally {
    // Let the browser acquire the URL before releasing it.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
