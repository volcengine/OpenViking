/** Decode one URI segment for display only; never use the result as a resource key. */
export function decodePathLabel(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}
