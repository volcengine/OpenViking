import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { FilePreviewEditing } from '../../components/file-preview/index.js';
import { isExperienceFile } from './experience-data.js';
import {
  isSummaryUri,
  isSystemUri,
  supportsFileHistory,
} from './preview-policy.js';
import { readFileContent } from './fs-read-content.js';

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
  const canSnapshot = supportsFileHistory(uri);
  return {
    get promptForVersionNote() {
      return !wiki && canSnapshot;
    },
    loadContent: async () => {
      const result = await readFileContent(client, uri, options, -1, wiki);
      wiki = result.wiki;
      return result.content;
    },
    save: async (content, note) => {
      const shouldCreateVersion = !wiki && canSnapshot && note !== undefined;
      await client.request({
        method: 'POST',
        path: OV_PATHS.contentWrite,
        body: {
          uri,
          content,
          mode: 'replace',
          edit_source: 'manual',
          // The native write contract preserves and updates Wiki conflict history.
          ...(wiki ? { resolve_conflicts: true } : {}),
        },
        options,
      });
      if (!shouldCreateVersion) return;
      try {
        const result = await client.request<{ result: 'created' | 'noop' }>({
          method: 'POST',
          path: OV_PATHS.snapshotCommit,
          body: { message: note || `Update ${name}`, paths: [uri] },
          options,
        });
        if (result?.result === 'created') return;
      } catch {
        // The body write already succeeded; report only the missing snapshot.
      }
      return { warning: 'version-not-created' };
    },
    onSave,
  };
}
