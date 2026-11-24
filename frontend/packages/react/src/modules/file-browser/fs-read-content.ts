import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { isMemoryUri, stripMemoryFieldsTrailer } from './fs-memory-fields.js';
import { joinLines, splitLines } from './fs-lines.js';
import { splitWikiMetadata } from './wiki-metadata.js';
import { isCompileOwnedFile } from './dashboard-data.js';
import type { ReadDashboardHint } from './dashboard-resource.js';
import { isOvClientError } from '../../client/errors.js';
import type { CachedPreviewBody } from './preview-body-cache.js';

/** Read and project text once for preview, editing and Wiki business data. */
export async function readFileContent(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
  limit = -1,
  isWiki = false,
  readHint?: ReadDashboardHint,
  deferWiki = false,
  cache?: CachedPreviewBody,
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
  const cached = cache?.read();
  const first = cached ?? (await read(limit));
  const reachedCap = limit >= 0 && splitLines(first).length >= limit;
  if (cached === undefined && !reachedCap && !options.signal?.aborted)
    cache?.write(first);
  const candidate =
    !memory && markdown && splitWikiMetadata(first).payloads.length > 0;
  const format = markdown
    ? 'markdown'
    : /\.ya?ml$/i.test(uri)
      ? 'yaml'
      : 'text';

  const project = (
    full: string,
    wiki: boolean,
    completeRead = true,
    hidePending = false,
  ) => {
    const body = memory ? stripMemoryFieldsTrailer(full, format) : full;
    const projection =
      wiki || hidePending ? splitWikiMetadata(body) : undefined;
    const visible = projection?.document ?? body;
    const lines = splitLines(visible);
    const truncated = limit >= 0 && (!completeRead || lines.length > limit);
    return {
      content: truncated ? joinLines(lines.slice(0, limit)) : visible,
      truncated,
      wikiSource: wiki ? body : undefined,
      wiki,
      hasWikiTrailer: Boolean(projection?.payloads.length),
      compileOutput: Boolean(
        wiki &&
        completeRead &&
        projection?.complete &&
        projection.payloads.length,
      ),
    };
  };
  const readWiki = async () =>
    project(reachedCap ? await read(-1) : first, true);
  if (isWiki && markdown && (!deferWiki || !reachedCap))
    return { ...(await readWiki()), resolveWiki: undefined };
  const resolveWiki = async () => {
    try {
      let knownWiki = isWiki;
      const readOwnershipHint =
        readHint &&
        (async () => {
          const hint = await readHint();
          knownWiki ||= hint?.tags.includes('compile_target=true') ?? false;
          return hint;
        });
      if (
        isWiki ||
        (await isCompileOwnedFile(client, uri, options, readOwnershipHint))
      ) {
        const result = await readWiki();
        // Directory ownership alone does not turn a manual file into a Wiki draft.
        if (!deferWiki || knownWiki || result.hasWikiTrailer) return result;
        return;
      }
    } catch (error) {
      if (!isOvClientError(error) || error.statusCode !== 404) throw error;
      return;
    }
    if (deferWiki && candidate) return project(first, false, !reachedCap);
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
      !reachedCap || memory,
      deferWiki && candidate,
    ),
    resolveWiki: discoverWiki && deferWiki ? resolveWiki : undefined,
  };
}
