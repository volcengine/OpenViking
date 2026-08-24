export function stripVersionPrefix(version: string): string {
  return version.replace(/^v/i, '')
}
