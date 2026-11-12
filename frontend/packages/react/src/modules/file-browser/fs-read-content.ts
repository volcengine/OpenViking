import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { isMemoryUri, stripMemoryFieldsTrailer } from './fs-memory-fields.js';
import { joinLines, splitLines } from './fs-lines.js';
import { splitWikiMetadata } from './wiki-metadata.js';
import { readCompileTargets } from './dashboard-data.js';
import { isOvClientError } from '../../client/errors.js';

/** Read and project text once for preview, editing and Wiki business data. */
export async function readFileContent(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
  limit = -1,
  isWiki = false,
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
  let wiki = isWiki && markdown;
  const first = await read(limit);
  // A page end is not a file end. Read the complete trailer before filtering,
  // including a trailer whose closing delimiter lies beyond the preview cap.
  const reachedCap = limit >= 0 && splitLines(first).length >= limit;
  const candidate =
    !memory && markdown && splitWikiMetadata(first).payloads.length > 0;
  const full =
    reachedCap && (memory || wiki || candidate) ? await read(-1) : first;
  const format = markdown
    ? 'markdown'
    : /\.ya?ml$/i.test(uri)
      ? 'yaml'
      : 'text';
  const body = memory ? stripMemoryFieldsTrailer(full, format) : full;
  const projection = candidate || wiki ? splitWikiMetadata(body) : undefined;
  if (!wiki && projection?.payloads.length) {
    try {
      wiki = (await readCompileTargets(client, uri, options)).length > 0;
    } catch (error) {
      // An old server without Compile cannot establish Wiki ownership.
      // Other failures must not expose unclassified metadata as trusted body text.
      if (!isOvClientError(error) || error.statusCode !== 404) throw error;
    }
  }
  const visible = wiki ? (projection?.document ?? body) : body;
  const lines = splitLines(visible);
  const truncated =
    limit >= 0 && (memory || wiki ? lines.length > limit : reachedCap);
  return {
    content: truncated ? joinLines(lines.slice(0, limit)) : visible,
    truncated,
    // Only the business parser receives metadata; views and drafts receive content.
    wikiSource: wiki ? body : undefined,
    wiki,
  };
}
