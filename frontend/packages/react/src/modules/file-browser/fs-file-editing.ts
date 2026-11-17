import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { FilePreviewEditing } from '../../components/file-preview/index.js';
import { isExperienceFile } from './experience-data.js';
import { isSummaryUri, isSystemUri } from './preview-policy.js';
import { readFileContent } from './fs-read-content.js';
import { parseWikiFileConflicts } from './wiki-conflicts.js';

const EDITABLE_TEXT = /\.(?:md|json|jsonl|txt|yaml|yml)$/i;
/** Load the full file before editing; the preview can contain only its first page. */
export function createFileEditing(
  client: OvClient,
  uri: string,
  name: string,
  options: OvRequestOptions,
  onSave?: () => void,
  isWiki = false,
): FilePreviewEditing | undefined {
  if (
    !EDITABLE_TEXT.test(name) ||
    isSystemUri(uri) ||
    isSummaryUri(uri) ||
    isExperienceFile(uri)
  )
    return undefined;
  let wiki = isWiki;
  const read = async () => {
    const result = await readFileContent(client, uri, options, -1, wiki);
    wiki = result.wiki;
    return result;
  };
  return {
    loadContent: async () => (await read()).content,
    loadDocument: async () => {
      const { content, wikiSource } = await read();
      const conflicts = wikiSource && parseWikiFileConflicts(wikiSource);
      const annotations =
        conflicts && conflicts.status === 'available'
          ? conflicts.records
              .filter(({ state }) => state === 'unfixed')
              .map(({ matchText, description, reason }) => ({
                matchText,
                description,
                reason,
              }))
          : undefined;
      return { content, annotations };
    },
    save: async (content) => {
      await client.request({
        method: 'POST',
        path: OV_PATHS.contentWrite,
        body: {
          uri,
          content,
          mode: 'replace',
          // The native write contract preserves and updates Wiki conflict history.
          ...(wiki ? { resolve_conflicts: true, use_snapshot: true } : {}),
        },
        options,
      });
    },
    onSave,
  };
}
