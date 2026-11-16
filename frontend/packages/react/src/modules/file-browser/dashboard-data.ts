import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { OV_PATHS } from '../../client/paths.js';
import { within } from './dashboard-model.js';
import {
  resolveDashboardUri,
  findCompileTarget,
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
      targets?: CompileTarget[];
      configs?: CompileTarget[];
      next_cursor?: string | null;
    }>({
      method: 'GET',
      path: OV_PATHS.compileConfigs,
      query: { limit: 100, cursor },
      options,
    });
    const items = result.configs ?? result.targets;
    named ||= Array.isArray(result.configs);
    if (!Array.isArray(items))
      throw new TypeError('Invalid Compile target registry');
    targets.push(
      ...items.filter(
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
