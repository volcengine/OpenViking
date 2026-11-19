import { createRoot } from 'react-dom/client';
import { useState } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FilePreview } from '@openviking/react/components/file-preview';
import { FileBrowser } from '@openviking/react/modules/file-browser';

const rootUri = 'viking://resources';
const directory = `${rootUri}/docs`;
const literalDirectories = ['Finance#Q4', '100%', '100%20'].map(
  (name) => `${rootUri}/${name}`,
);
const evidence = {
  opened: [] as unknown[][],
  standalone: [] as string[],
  file: [] as string[],
  reads: [] as string[],
  scrolled: [] as string[],
};
Object.assign(window, { summaryLinksEvidence: evidence });
window.open = (...args: Parameters<typeof window.open>) => {
  evidence.opened.push(args);
  return null;
};
const scrollIntoView = HTMLElement.prototype.scrollIntoView;
HTMLElement.prototype.scrollIntoView = function (options) {
  evidence.scrolled.push(this.id);
  scrollIntoView?.call(this, options);
};
const content = [
  '[Relative child](child/.overview.md)',
  '[Literal base child](guide.md)',
  '[Absolute resource](viking://resources/guide.md)',
  '[Encoded resource](viking://resources/docs/child%20folder/.overview.md)',
  '[Web reference](https://example.com/reference)',
  '[HTTP reference](http://example.com/reference)',
  '[Protocol relative](//example.com/reference)',
  '[Mail reference](mailto:reader@example.com)',
  '[Phone reference](tel:+12025550123)',
  '[Same document](#details)',
  '[Malformed fragment](#broken%)',
  '<span id="details">Details</span>',
  '[Cross document](other/.overview.md#details)',
  '[Queried resource](viking://resources/guide.md?view=preview#details)',
  '[Unsafe](javascript:alert(1))',
].join('\n\n');
const ok = (result: unknown) =>
  Promise.resolve(new Response(JSON.stringify({ status: 'ok', result })));
const fetch: typeof globalThis.fetch = (input) => {
  const url = new URL(String(input), 'https://preview.test');
  const uri = url.searchParams.get('uri') ?? '';
  if (url.pathname.endsWith('/fs/stat'))
    return ok({ uri, isDir: !uri.endsWith('.md'), access: 'manage' });
  if (url.pathname.endsWith('/fs/ls'))
    return ok(
      uri === rootUri
        ? [
            { uri: directory, isDir: true },
            { uri: `${rootUri}/guide.md`, isDir: false },
            ...literalDirectories.map((uri) => ({ uri, isDir: true })),
          ]
        : uri === directory
          ? [
              { uri: `${directory}/child`, isDir: true },
              { uri: `${directory}/child folder`, isDir: true },
            ]
          : literalDirectories.includes(uri)
            ? [{ uri: `${uri}/guide.md`, isDir: false }]
            : [],
    );
  if (/\/content\/(abstract|overview|read)$/.test(url.pathname)) {
    evidence.reads.push(uri);
    return ok(content);
  }
  if (url.pathname.endsWith('/fs/attrs')) return ok({ attrs: { tags: [] } });
  if (url.pathname.endsWith('/snapshot/log')) return ok([]);
  return ok({});
};

function FileHost() {
  const [target, setTarget] = useState('');
  return (
    <>
      <FilePreview
        tabs={[
          {
            kind: 'file',
            key: `${directory}/note.md`,
            source: { fileName: 'note.md', content },
            operations: {
              openUri: async (uri) => {
                evidence.file.push(uri);
                setTarget(uri);
              },
            },
          },
        ]}
      />
      <output aria-label="Host navigation callback">{target}</output>
    </>
  );
}

createRoot(document.getElementById('root')!).render(
  <>
    <section id="standalone">
      <FilePreview
        tabs={[
          {
            kind: 'directory',
            key: directory,
            title: 'Docs',
            levels: [
              {
                key: 'l0',
                label: 'L0',
                source: { fileName: 'l0.md', content },
              },
              {
                key: 'l1',
                label: 'L1',
                source: { fileName: 'l1.md', content },
              },
            ],
            operations: {
              openUri: async (uri) => {
                evidence.standalone.push(uri);
              },
            },
          },
        ]}
      />
    </section>
    <section id="browser">
      <OpenVikingProvider fetch={fetch}>
        <FileBrowser rootUri={rootUri} defaultUri={directory} />
      </OpenVikingProvider>
    </section>
    <section id="file">
      <FileHost />
    </section>
    <section id="anchor-only">
      <FilePreview
        tabs={[
          {
            kind: 'directory',
            key: directory,
            title: 'No host navigation',
            levels: [
              {
                key: 'l0',
                label: 'L0',
                source: { fileName: 'l0.md', content },
              },
            ],
          },
        ]}
      />
    </section>
    {literalDirectories.map((uri, index) => (
      <section key={uri} id={`literal-${index}`}>
        <OpenVikingProvider fetch={fetch}>
          <FileBrowser rootUri={rootUri} defaultUri={uri} />
        </OpenVikingProvider>
      </section>
    ))}
  </>,
);
