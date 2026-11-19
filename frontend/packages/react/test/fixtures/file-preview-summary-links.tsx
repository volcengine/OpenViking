import { createRoot } from 'react-dom/client';
import { OpenVikingProvider } from '@openviking/react/client';
import { FilePreview } from '@openviking/react/components/file-preview';
import { FileBrowser } from '@openviking/react/modules/file-browser';

const rootUri = 'viking://resources';
const directory = `${rootUri}/docs`;
const evidence = {
  opened: [] as unknown[][],
  standalone: [] as string[],
  reads: [] as string[],
  scrolled: [] as string[],
};
Object.assign(window, { summaryLinksEvidence: evidence });
window.open = (...args: Parameters<typeof window.open>) => {
  evidence.opened.push(args);
  return null;
};
HTMLElement.prototype.scrollIntoView = function () {
  evidence.scrolled.push(this.id);
};
const content = [
  '[Relative child](child/.overview.md)',
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
          ]
        : uri === directory
          ? [
              { uri: `${directory}/child`, isDir: true },
              { uri: `${directory}/child folder`, isDir: true },
            ]
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
  </>,
);
