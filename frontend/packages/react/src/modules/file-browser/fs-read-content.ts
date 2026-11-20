import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { isMemoryUri, stripMemoryFieldsTrailer } from './fs-memory-fields.js';
import { joinLines, splitLines } from './fs-lines.js';
import { splitWikiMetadata } from './wiki-metadata.js';
import { isCompileOwnedFile } from './dashboard-data.js';
import type { readDashboardHint } from './dashboard-resource.js';
import { isOvClientError } from '../../client/errors.js';

/** Read and project text once for preview, editing and Wiki business data. */
export async function readFileContent(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
  limit = -1,
  isWiki = false,
  hint?: Awaited<ReturnType<typeof readDashboardHint>>,
  deferWiki = false,
) {
  const read = async (lineLimit: number) => {
    const value = await client.request<string>({
      method: 'GET',
      path: OV_PATHS.contentRead,
      query: { uri, limit: lineLimit, raw: false },
      options,
    });
    if (typeof value !== 'string') throw new TypeError('Invalid file content');
    return value;
  };
  const memory = isMemoryUri(uri);
  const markdown = /\.md$/i.test(uri);
  const first = await read(limit);
  const reachedCap = limit >= 0 && splitLines(first).length >= limit;
  const candidate =
    !memory && markdown && splitWikiMetadata(first).payloads.length > 0;
  const format = markdown
    ? 'markdown'
    : /\.ya?ml$/i.test(uri)
      ? 'yaml'
      : 'text';

  const project = (full: string, wiki: boolean) => {
    const body = memory ? stripMemoryFieldsTrailer(full, format) : full;
    const projection = wiki ? splitWikiMetadata(body) : undefined;
    const visible = projection?.document ?? body;
    const lines = splitLines(visible);
    const truncated =
      limit >= 0 && (memory || wiki ? lines.length > limit : reachedCap);
    return {
      content: truncated ? joinLines(lines.slice(0, limit)) : visible,
      truncated,
      wikiSource: wiki ? body : undefined,
      wiki,
      compileOutput: Boolean(
        projection?.complete && projection.payloads.length,
      ),
    };
  };
  const readWiki = async () =>
    project(reachedCap ? await read(-1) : first, true);
  if (isWiki && markdown && (!deferWiki || !reachedCap))
    return { ...(await readWiki()), resolveWiki: undefined };
  const resolveWiki = async () => {
    try {
      if (isWiki || (await isCompileOwnedFile(client, uri, options, hint)))
        return await readWiki();
    } catch (error) {
      if (!deferWiki && (!isOvClientError(error) || error.statusCode !== 404))
        throw error;
    }
  };
  const discoverWiki =
    !memory && markdown && (candidate || (deferWiki && reachedCap));
  if (discoverWiki && !deferWiki) {
    const result = await resolveWiki();
    if (result) return { ...result, resolveWiki: undefined };
  }
  return {
    ...project(
      memory && reachedCap ? await read(-1) : first,
      isWiki && markdown,
    ),
    // A capped page can only establish provenance after checking the complete tail.
    ...(reachedCap ? { compileOutput: false } : {}),
    resolveWiki: discoverWiki && deferWiki ? resolveWiki : undefined,
  };
}
