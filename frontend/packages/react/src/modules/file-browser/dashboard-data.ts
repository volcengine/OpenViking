import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { OV_PATHS } from '../../client/paths.js';
import { within } from './dashboard-model.js';
import {
  resolveDashboardUri,
  findCompileTarget,
  type ReadDashboardHint,
} from './dashboard-resource.js';

export interface CompileTarget {
  compile_name?: string;
  skill?: string;
  to: string;
  conflicts_num?: number;
  compile_type?: string;
  initialized?: boolean;
}

export async function readCompileCatalog(
  client: OvClient,
  rootUri: string,
  options: OvRequestOptions,
) {
  rootUri = await resolveDashboardUri(client, rootUri, options);
  const targets: CompileTarget[] = [];
  let cursor: string | undefined;
  let named = false;
  const seen = new Set<string>();
  do {
    const result = await client.request<{
      configs?: CompileTarget[];
      targets?: CompileTarget[];
      next_cursor?: string | null;
    }>({
      method: 'GET',
      path: OV_PATHS.compileConfigs,
      query: { limit: 100, cursor },
      options,
    });
    const page = result?.configs ?? result?.targets;
    named ||= Array.isArray(result?.configs);
    if (!Array.isArray(page))
      throw new TypeError('Invalid Compile target registry');
    targets.push(
      ...page.filter(
        (item) =>
          typeof item.to === 'string' &&
          item.compile_type !== 'memory' &&
          item.skill !== 'memory' &&
          item.initialized !== false &&
          (within(item.to, rootUri) || within(rootUri, item.to)),
      ),
    );
    cursor = result.next_cursor ?? undefined;
    if (cursor && seen.has(cursor))
      throw new TypeError('Compile target cursor did not advance');
    if (cursor) seen.add(cursor);
  } while (cursor);
  return { targets, named };
}

export async function readCompileTargets(
  client: OvClient,
  rootUri: string,
  options: OvRequestOptions,
) {
  const catalog = await readCompileCatalog(client, rootUri, options);
  // Named configurations are user-scoped; confirm Wiki ownership from resource tags.
  if (!catalog.named) return catalog.targets;
  const target = await findCompileTarget(client, rootUri, options);
  return target ? [{ to: target.uri }] : [];
}

export async function isCompileOwnedFile(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
  readHint?: ReadDashboardHint,
) {
  const namespace = compileNamespaceOwnership(uri);
  if (namespace !== undefined) return namespace;
  const hint = await readHint?.();
  const scope = hint?.uri ?? (await resolveDashboardUri(client, uri, options));
  const canonicalNamespace = compileNamespaceOwnership(scope);
  if (canonicalNamespace !== undefined) return canonicalNamespace;
  if (await findCompileTarget(client, scope, options, hint)) return true;
  const catalog = await readCompileCatalog(client, scope, options);
  return (
    !catalog.named && catalog.targets.some((target) => within(scope, target.to))
  );
}

// Namespace ownership still requires a file-level Wiki trailer for the label.
function compileNamespaceOwnership(uri: string) {
  if (
    !/^viking:\/\/(?:system|user\/[^/]+|~)(?:\/peers\/[^/]+)?\/compile\/.+/i.test(
      uri,
    )
  )
    return undefined;
  return !/^viking:\/\/system\/compile\/example-skills(?:\/|$)/i.test(uri);
}
