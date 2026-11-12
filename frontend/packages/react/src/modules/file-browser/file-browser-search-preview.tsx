import { useEffect, useMemo, useState } from 'react';
import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { isBinaryFile } from './fs-file-kind.js';
import { isMemoryUri, stripMemoryFieldsTrailer } from './fs-memory-fields.js';
import type { ResolvedLabels } from './file-browser-labels.js';
import type { FsEntry } from './fs-model.js';
import { splitSearchUri } from './file-browser-search-results.js';

const PREVIEW_DELAY = 120;
const LINE_LIMIT = 20;
const CHARACTER_LIMIT = 2400;

export function FileBrowserSearchPreview({
  client,
  entry,
  requestOptions,
  text,
}: {
  client: OvClient | null;
  entry: FsEntry;
  requestOptions?: OvRequestOptions;
  text: ResolvedLabels;
}) {
  const { name, parent } = splitSearchUri(entry.uri);
  const request = useMemo(
    () =>
      client && !entry.isDir && !isBinaryFile(name)
        ? { client, uri: entry.uri, options: requestOptions }
        : null,
    [client, entry.uri, entry.isDir, name, requestOptions],
  );
  const [response, setResponse] = useState<{
    request: NonNullable<typeof request>;
    content?: string;
    failed?: boolean;
  }>();

  // Hover and keyboard highlight drive the external content request. A short
  // delay skips rows the pointer passes over; switching rows or closing cancels
  // the timer and read, and stale responses cannot replace the active preview.
  useEffect(() => {
    if (!request) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void request.client
        .request<string>({
          method: 'GET',
          path: OV_PATHS.contentRead,
          query: { uri: request.uri, limit: LINE_LIMIT },
          options: { ...request.options, signal: controller.signal },
        })
        .then(
          (value) => {
            if (controller.signal.aborted) return;
            const content = typeof value === 'string' ? value : '';
            setResponse({
              request,
              content: (isMemoryUri(request.uri)
                ? stripMemoryFieldsTrailer(content)
                : content
              )
                .slice(0, CHARACTER_LIMIT)
                .trim(),
            });
          },
          () => {
            if (!controller.signal.aborted)
              setResponse({ request, failed: true });
          },
        );
    }, PREVIEW_DELAY);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [request]);

  const current = request && response?.request === request ? response : null;
  const loading = !!request && !current;
  const content = current?.content || entry.abstract?.slice(0, CHARACTER_LIMIT);
  return (
    <aside
      className="ov-file-browser-search__preview"
      aria-label={text.searchPreview}
    >
      <div className="ov-file-browser-search__preview-title" title={entry.uri}>
        {parent ? <span>{parent}</span> : null}
        <strong>{name}</strong>
      </div>
      <div
        key={entry.uri}
        className="ov-file-browser-search__preview-body"
        aria-busy={loading}
      >
        {loading ? (
          <p role="status">{text.loading}</p>
        ) : content ? (
          <p className="ov-file-browser-search__excerpt">{content}</p>
        ) : (
          <p>
            {current?.failed
              ? text.searchPreviewFailed
              : text.searchPreviewEmpty}
          </p>
        )}
      </div>
    </aside>
  );
}
