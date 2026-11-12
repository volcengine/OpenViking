import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { FilePreviewEditing } from '../../components/file-preview/index.js';
import { isExperienceFile } from './experience-data.js';
import { isEditableText, supportsFileVersions } from './preview-policy.js';
import { contentDigest } from './fs-content-digest.js';
import { readFileContent } from './fs-read-content.js';

/** Load the full file before editing; the preview can contain only its first page. */
export function createFileEditing(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
  onSave?: () => void,
  isWiki = false,
): FilePreviewEditing | undefined {
  if (!isEditableText(uri) || isExperienceFile(uri)) return undefined;
  let original: string | undefined;
  let wiki = isWiki;
  return {
    versionNote: supportsFileVersions(uri),
    loadContent: async () => {
      const result = await readFileContent(client, uri, options, -1, wiki);
      wiki = result.wiki;
      original = result.revisionText;
      return result.content;
    },
    save: async (content, versionMessage) => {
      const revision = original;
      if (versionMessage !== undefined && revision === undefined)
        throw new Error('The original content was not loaded');
      await client.request({
        method: 'POST',
        path: OV_PATHS.contentWrite,
        body: {
          uri,
          content,
          mode: 'replace',
          edit_source: 'manual',
          ...(wiki ? { resolve_conflicts: true } : {}),
          ...(versionMessage === undefined || revision === undefined
            ? {}
            : {
                version_message: versionMessage,
                expected_content_sha256: await contentDigest(revision),
              }),
        },
        options,
      });
    },
    onSave,
  };
}
