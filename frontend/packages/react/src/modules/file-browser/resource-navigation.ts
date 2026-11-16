import { normalizeResourceUri } from './fs-uri.js';

/** Resolve directory summary links before selecting, loading or revealing a resource. */
export function resolveResourceNavigation(uri: string): {
  uri: string;
  level?: 'l0' | 'l1';
} {
  const level = uri.endsWith('/.abstract.md')
    ? 'l0'
    : uri.endsWith('/.overview.md')
      ? 'l1'
      : undefined;
  return level
    ? {
        uri: normalizeResourceUri(uri.slice(0, uri.lastIndexOf('/') + 1)),
        level,
      }
    : { uri };
}
