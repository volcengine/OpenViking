import { normalizeResourceUri } from '../resource-uri.js';
import { expandChain } from './fs-path.js';

const homeAlias = 'viking://~';
const canonicalHome = /^viking:\/\/user\/[^/]+/;
const isHomeAlias = (uri: string) =>
  uri === homeAlias || uri.startsWith(`${homeAlias}/`);

/** Build parent paths in the saved row's namespace. Restoration still requires a listed row. */
export function expansionPath(rootUri: string, uri: string): string[] {
  let root = normalizeResourceUri(rootUri);
  const target = normalizeResourceUri(uri);
  const rootHome = root.match(canonicalHome)?.[0];
  const targetHome = target.match(canonicalHome)?.[0];
  if (isHomeAlias(root) && targetHome) {
    root = `${targetHome}${root.slice(homeAlias.length)}`;
  } else if (rootHome && isHomeAlias(target)) {
    root = `${homeAlias}${root.slice(rootHome.length)}`;
  }
  const path = expandChain(root, target);
  return path.at(-1) === target ? path : [];
}
