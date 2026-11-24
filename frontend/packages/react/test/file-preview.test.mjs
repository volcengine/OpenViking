import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderMarkup } from './helpers/csr-render.mjs';
import { FilePreview } from '@openviking/react/components/file-preview';
import {
  toFileSource,
  isEmptyFile,
} from '../src/components/file-preview/file-preview-source.ts';
const source = (content, fileName = 'guide.md') => ({ content, fileName });
const levels = [
  { key: 'L0', label: 'L0 Abstract', source: source(''), disabled: true },
  { key: 'L1', label: 'L1 Overview', source: source('') },
  { key: 'L2', label: 'L2 Content', source: source('body') },
];
const render = (props = {}) =>
  renderMarkup(
    createElement(FilePreview, {
      tabs: [
        {
          kind: 'file',
          key: 'guide',
          source: source('fallback'),
          levels,
          ...props,
        },
      ],
    }),
  );

test('selects the first enabled level and does not leak configuration into the DOM', () => {
  const html = render();
  assert.match(html, /aria-pressed="true"[^>]*>L1 Overview/);
  assert.match(html, /disabled=""[^>]*>L0 Abstract/);
  assert.match(html, /This file is empty/);
  assert.doesNotMatch(html, /activeLevelKey=|resolveImage=|levels=/);
});
test('controlled selection uses the matching source; stale or disabled keys fall back', () => {
  assert.match(
    render({ activeLevelKey: 'L2' }),
    /aria-pressed="true"[^>]*>L2 Content/,
  );
  for (const key of ['removed', 'L0'])
    assert.match(
      render({ activeLevelKey: key }),
      /aria-pressed="true"[^>]*>L1 Overview/,
    );
});
test('legacy tabs and all-disabled levels retain the base source', () => {
  assert.doesNotMatch(render({ levels: undefined }), /ov-file-preview__levels/);
  const html = render({
    source: source(''),
    levels: levels.map((level) => ({ ...level, disabled: true })),
  });
  assert.match(html, /This file is empty/);
  assert.doesNotMatch(html, /aria-pressed="true"/);
});

for (const defaultLevelKey of ['l0', 'l1']) {
  test(`directory indicator appears after loading with default ${defaultLevelKey}`, async () => {
    const { mount } = await import('./helpers/module-dom.mjs');
    const tab = {
      kind: 'directory',
      key: 'directory',
      defaultLevelKey,
      levels: ['l0', 'l1'].map((key) => ({
        key,
        label: key,
        source: source(''),
      })),
    };
    const render = (loading) =>
      createElement(FilePreview, { tabs: [{ ...tab, loading }] });
    const view = await mount(render(true));
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        assert.equal(
          view.container.querySelector('.ov-file-preview__levels'),
          null,
        );
        await view.render(render(false));
        assert.equal(
          view.container.querySelector(
            '[role="tab"][aria-selected="true"].ov-file-preview__level',
          )?.textContent,
          defaultLevelKey,
        );
        assert.equal(
          view.container.querySelector('.ov-file-preview__level-indicator')
            ?.style.width,
          '14px',
          'the selected level must have an indicator without a click or resize',
        );
        await view.render(render(true));
      }
    } finally {
      await view.unmount();
    }
  });
}

test('Blob conversion retains MIME and binary bytes; URL and empty/whitespace precedence stay explicit', async () => {
  const blob = new Blob([new Uint8Array([0, 255, 128])], { type: 'image/png' });
  const result = toFileSource({
    file: blob,
    fileName: 'image',
    url: '/ignored',
  });
  assert.equal(result.kind, 'file');
  assert.equal(result.file.type, 'image/png');
  assert.deepEqual(
    new Uint8Array(await result.file.arrayBuffer()),
    new Uint8Array([0, 255, 128]),
  );
  assert.equal(toFileSource({ ...source(''), file: blob }).kind, 'bytes');
  assert.equal(isEmptyFile(source('')), true);
  assert.equal(isEmptyFile(source(' \n')), false);
  assert.equal(
    toFileSource({ fileName: 'image.png', url: '/image.png' }).kind,
    'url',
  );
});

test('Viking image targets survive sanitization without changing links, code, or unsafe protocols', async () => {
  const { preserveVikingImages, imagePreviewTarget, originalImageTarget } =
    await import('../src/components/file-preview/file-preview-markdown-images.ts');
  const raw = 'viking://resources/a folder/图.svg';
  const root = {
    type: 'root',
    children: [
      { type: 'image', url: raw },
      { type: 'link', url: raw },
      { type: 'code', value: `![x](${raw})` },
      { type: 'image', url: 'javascript:alert(1)' },
      { type: 'imageReference', identifier: 'figure', alt: 'Figure' },
      { type: 'definition', identifier: 'figure', url: raw, title: 'Title' },
    ],
  };
  preserveVikingImages(root);
  assert.equal(root.children[0].url, imagePreviewTarget(raw));
  assert.equal(originalImageTarget(root.children[0].url), raw);
  assert.equal(root.children[1].url, raw);
  assert.equal(root.children[2].value, `![x](${raw})`);
  assert.equal(root.children[3].url, 'javascript:alert(1)');
  assert.equal(root.children[4].type, 'image');
  assert.equal(originalImageTarget(root.children[4].url), raw);
  assert.equal(root.children[4].alt, 'Figure');
  assert.equal(root.children[5].url, raw);
  assert.equal(
    originalImageTarget('./__ov_viking_image__/%invalid'),
    './__ov_viking_image__/%invalid',
  );
});
