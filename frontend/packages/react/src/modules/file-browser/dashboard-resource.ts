import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';

const isHomeUri = (uri: string) =>
  uri === 'viking://~' || uri.startsWith('viking://~/');

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
    isHomeUri(canonical)
  )
    throw new TypeError('Invalid canonical resource stat');
  return { uri: canonical, isDir: stat.isDir };
}

export async function resolveDashboardUri(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
) {
  return isHomeUri(uri)
    ? (await readDashboardStat(client, uri, options)).uri
    : uri;
}
