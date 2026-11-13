import { useEffect, useMemo, useState } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { isBinaryFile } from './fs-file-kind.js';
import { readFileContent } from './fs-read-content.js';
import { nameFromUri } from './fs-model.js';
import type { SearchHit } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

const LINE_LIMIT = 20;
const CHARACTER_LIMIT = 2400;

export function FileBrowserSearchPreview({
  client,
  entry,
  requestOptions,
  text,
}: {
  client: OvClient | null;
  entry: SearchHit;
  requestOptions?: OvRequestOptions;
  text: ResolvedLabels;
}) {
  const name = nameFromUri(entry.uri);
  const parent = entry.uri.endsWith('://')
    ? ''
    : entry.uri.slice(0, entry.uri.lastIndexOf('/') + 1);
  const request = useMemo(
    () =>
      client &&
      !entry.isDir &&
      entry.resultKind !== 'grep' &&
      !isBinaryFile(name)
        ? { client, uri: entry.uri, options: requestOptions }
        : null,
    [client, entry.uri, entry.isDir, entry.resultKind, name, requestOptions],
  );
  const [response, setResponse] = useState<{
    request: NonNullable<typeof request>;
    content?: string;
    failed?: boolean;
  }>();

  // Read the active result after hover settles or keyboard selection changes.
  // Switching results or closing aborts the read and discards late responses.
  useEffect(() => {
    if (!request) return;
    const controller = new AbortController();
    void readFileContent(
      request.client,
      request.uri,
      { ...request.options, signal: controller.signal },
      LINE_LIMIT,
    ).then(
      ({ content }) => {
        if (controller.signal.aborted) return;
        setResponse({
          request,
          content: content.slice(0, CHARACTER_LIMIT).trim(),
        });
      },
      () => {
        if (!controller.signal.aborted) setResponse({ request, failed: true });
      },
    );
    return () => controller.abort();
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
        {content ? (
          <p className="ov-file-browser-search__excerpt">{content}</p>
        ) : loading ? (
          <p role="status">{text.loading}</p>
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
