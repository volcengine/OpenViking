import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { isHomeResourceUri } from '../../shared/resource-uri.js';

/** Native stat resolves the home alias in the authenticated request scope. */
export async function readDashboardStat(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
) {
  const stat = await client.request<{ uri?: string; isDir: boolean }>({
    method: 'GET',
    path: OV_PATHS.fsStat,
    query: { uri },
    options,
  });
  const canonical = stat?.uri ?? uri;
  if (
    typeof stat?.isDir !== 'boolean' ||
    typeof canonical !== 'string' ||
    !canonical ||
    isHomeResourceUri(canonical)
  )
    throw new TypeError('Invalid canonical resource stat');
  return { uri: canonical, isDir: stat.isDir };
}

export async function resolveDashboardUri(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
) {
  return isHomeResourceUri(uri)
    ? (await readDashboardStat(client, uri, options)).uri
    : uri;
}

/** Keep native identity and tags together in the query's authenticated scope. */
export async function readDashboardHint(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
) {
  const result = await client.request<{
    uri?: string;
    attrs?: { tags?: string[] };
  }>({ method: 'GET', path: OV_PATHS.fsAttrs, query: { uri }, options });
  return {
    uri: await resolveDashboardUri(client, result?.uri ?? uri, options),
    tags: result?.attrs?.tags ?? [],
  };
}

/** Directory tags, unlike configuration lists, also cover other users' targets. */
export async function findCompileTarget(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
  hint?: Awaited<ReturnType<typeof readDashboardHint>>,
) {
  uri = hint?.uri ?? (await resolveDashboardUri(client, uri, options));
  while (uri.length > 'viking://'.length) {
    const { tags } =
      hint?.uri === uri ? hint : await readDashboardHint(client, uri, options);
    if (tags.includes('compile_target=true')) return { uri, tags };
    const separator = uri.lastIndexOf('/');
    if (separator < 'viking://'.length) break;
    uri = uri.slice(0, separator);
  }
}

export type ReadDashboardHint = () =>
  | Promise<Awaited<ReturnType<typeof readDashboardHint>> | undefined>
  | undefined;
