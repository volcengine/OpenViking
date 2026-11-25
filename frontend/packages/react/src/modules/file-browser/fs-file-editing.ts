import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { FilePreviewEditing } from '../../components/file-preview/index.js';
import { isExperienceFile } from './experience-data.js';
import { isSystemUri } from '../../shared/resource-uri.js';
import { isSummaryUri } from './preview-policy.js';
import { readFileContent } from './fs-read-content.js';
import { parseWikiFileConflicts } from './wiki-conflicts.js';
import type { ReadDashboardHint } from './dashboard-resource.js';

const EDITABLE_TEXT = /\.(?:md|json|jsonl|txt|yaml|yml)$/i;

export type WikiSaveStatus = 'saving' | 'success';

/** Load the full file before editing; the preview can contain only its first page. */
export function createFileEditing(
  client: OvClient,
  uri: string,
  name: string,
  options: OvRequestOptions,
  onSave?: () => void,
  isWiki = false,
  onWikiSaveStatus?: (status?: WikiSaveStatus) => void,
  readHint?: ReadDashboardHint,
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
    const hint = !wiki ? await readHint?.()?.catch(() => undefined) : undefined;
    const tagged = hint?.tags.includes('compile_target=true') ?? false;
    const result = await readFileContent(
      client,
      uri,
      options,
      -1,
      wiki || tagged,
      hint ? () => Promise.resolve(hint) : undefined,
    );
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
      return {
        content,
        annotations,
        // The full Wiki read keeps the projected body as an exact prefix.
        readOnlySuffix: wikiSource?.slice(content.length) || undefined,
      };
    },
    save: async (content) => {
      options.signal?.throwIfAborted();
      const notify = wiki ? onWikiSaveStatus : undefined;
      notify?.('saving');
      try {
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
        options.signal?.throwIfAborted();
      } catch (error) {
        notify?.();
        throw error;
      }
      notify?.('success');
    },
    onSave,
  };
}
