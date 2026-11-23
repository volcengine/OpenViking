import {
  HOME_URI,
  isHomeResourceUri,
  normalizeResourceUri,
  needsHomeIdentity,
} from '../resource-uri.js';
import { expandChain } from './fs-path.js';

const canonicalHome = /^viking:\/\/user\/[^/]+/;

/** Build parent paths in the saved row's namespace. Restoration still requires a listed row. */
export function expansionPath(rootUri: string, uri: string): string[] {
  let root = normalizeResourceUri(rootUri);
  const target = normalizeResourceUri(uri);
  const rootHome = root.match(canonicalHome)?.[0];
  const targetHome = target.match(canonicalHome)?.[0];
  if (isHomeResourceUri(root) && targetHome) {
    root = `${targetHome}${root.slice(HOME_URI.length)}`;
  } else if (rootHome && isHomeResourceUri(target)) {
    root = `${HOME_URI}${root.slice(rootHome.length)}`;
  }
  const path = expandChain(root, target);
  return path.at(-1) === target ? path : [];
}

function homeRelativePath(uri: string) {
  return normalizeResourceUri(uri).replace(canonicalHome, HOME_URI);
}

/** Another spelling could belong to this branch. This does not establish identity. */
export function mayShareHomeBranch(ancestor: string, target: string) {
  if (!needsHomeIdentity(ancestor, target)) return false;
  const parent = homeRelativePath(ancestor);
  const path = homeRelativePath(target);
  return path === parent || path.startsWith(`${parent}/`);
}
