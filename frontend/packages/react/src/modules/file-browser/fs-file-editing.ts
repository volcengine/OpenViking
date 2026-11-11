import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { FilePreviewEditing } from '../../components/file-preview/index.js';
import { isMemoryUri, stripMemoryFieldsTrailer } from './fs-memory-fields.js';

const EDITABLE_TEXT = /\.(?:md|json|jsonl|txt|yaml|yml)$/i;

/** Load the full file before editing; the preview can contain only its first page. */
export function createFileEditing(
  client: OvClient,
  uri: string,
  name: string,
  options: OvRequestOptions,
  onSave?: () => void,
): FilePreviewEditing | undefined {
  if (
    !EDITABLE_TEXT.test(name) ||
    /(?:^viking:\/\/system(?:\/|$)|\/(?:experiences|\.abstract\.md|\.overview\.md)(?:\/|$))/i.test(
      uri,
    )
  )
    return undefined;
  return {
    loadContent: async () => {
      const content = await client.request<string>({
        method: 'GET',
        path: OV_PATHS.contentRead,
        query: { uri, limit: -1 },
        options,
      });
      if (typeof content !== 'string')
        throw new TypeError('Invalid file content');
      // The server replaces only the body of memory files and preserves their metadata.
      return isMemoryUri(uri) ? stripMemoryFieldsTrailer(content) : content;
    },
    save: async (content) => {
      await client.request({
        method: 'POST',
        path: OV_PATHS.contentWrite,
        body: { uri, content, mode: 'replace' },
        options,
      });
    },
    onSave,
  };
}
