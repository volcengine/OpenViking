import { FilePreview } from '@openviking/react/components/file-preview';
import { createRoot } from 'react-dom/client';

const marker = '<KBImage position="0"></picture>';
const close = '</div></KBImage>';
const image = (name: string, alt = name) =>
  `![${alt}](viking://resources/${name}.png)`;
const content = [
  '<div><strong>Safe HTML</strong> <a href="https://example.com/">Reference</a></div>',
  '',
  marker,
  close,
  image('with-caption'),
  'Caption after the first image.',
  marker,
  close,
  image('second-block', 'Second "quoted" & <label>'),
  marker,
  close,
  image('third-block'),
  '',
  '<div onclick="window.untrustedEvent = true">Safe text</div>',
  '<a href="javascript:alert(1)">Unsafe link</a>',
  '<script>window.untrustedScript = true</script>',
  '',
  '<div>',
  '<!--',
  close,
  image('comment'),
  '-->',
  '</div>',
  '',
  '~~~markdown',
  close,
  image('literal'),
  '~~~',
].join('\n');

createRoot(document.getElementById('root')!).render(
  <FilePreview
    tabs={[
      {
        kind: 'file',
        key: 'readme',
        source: { fileName: 'readme.md', content },
        resolveImage: async (src) =>
          `https://preview.test/resolved/${encodeURIComponent(src)}`,
      },
    ]}
  />,
);
