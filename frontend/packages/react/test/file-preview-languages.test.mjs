import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { EditorView } from '@codemirror/view';
import Prism from 'prismjs';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount, settle } from './helpers/module-dom.mjs';
import { waitForEditor } from './helpers/preview-editor-dom.mjs';
import { codeCases, namedCases } from './helpers/preview-code-cases.mjs';

const cases = [
  ...codeCases,
  ...namedCases,
  {
    ...codeCases.find((item) => item.language === 'python'),
    fileNames: ['MAIN.PYW'],
  },
  {
    ...codeCases.find((item) => item.language === 'markup'),
    fileNames: ['icon.svg'],
    fileType: 'xml',
  },
  {
    ...codeCases.find((item) => item.language === 'python'),
    fileNames: ['source.txt'],
    fileType: 'text/x-python; charset=utf-8',
  },
  {
    ...codeCases.find((item) => item.language === 'json'),
    fileNames: ['data.txt'],
    fileType: 'application/json',
  },
  {
    ...codeCases.find((item) => item.language === 'swift'),
    fileNames: ['source.swift'],
    fileType: 'code',
  },
  {
    fileNames: ['notes.txt', 'output.log', 'table.csv', 'unknown.custom'],
    content: '<script>alert("literal text")</script>',
  },
];

for (const { language, fileNames, fileType, content, token } of cases) {
  test(`FilePreview highlights and saves ${fileNames.join(', ')}`, async () => {
    for (const fileName of fileNames) {
      const saved = [];
      const view = await mount(
        h(FilePreview, {
          tabs: [
            {
              key: 'code',
              kind: 'file',
              source: { fileName, fileType, content: '' },
              editing: {
                loadContent: async () => content,
                save: async (value) => {
                  saved.push(value);
                },
              },
            },
          ],
        }),
      );
      try {
        await act(async () =>
          view.container.querySelector('[aria-label="Edit"]').click(),
        );
        const editor = EditorView.findFromDOM(
          await waitForEditor(view.container),
        );
        const selector = `.ov-file-preview__token--${token}`;
        for (
          let attempt = 0;
          token && attempt < 60 && !editor.contentDOM.querySelector(selector);
          attempt++
        )
          await settle();
        if (token) {
          assert.ok(
            Prism.languages[language],
            `${fileName}: grammar registered on shared Prism`,
          );
          assert.ok(
            editor.contentDOM.querySelector(selector),
            `${fileName}: ${token} token`,
          );
        } else {
          assert.equal(
            editor.contentDOM.querySelector(
              '[class*="ov-file-preview__token--"]',
            ),
            null,
          );
          assert.equal(editor.contentDOM.querySelector('script'), null);
        }
        assert.equal(editor.state.doc.toString(), content, fileName);
        const draft = `${content}\n`;
        await act(async () =>
          editor.dispatch({
            changes: { from: editor.state.doc.length, insert: '\n' },
          }),
        );
        await act(async () =>
          [...view.container.querySelectorAll('button')]
            .find((button) => button.textContent === 'Save')
            .click(),
        );
        assert.deepEqual(saved, [draft], fileName);
        assert.equal(
          view.container.querySelector('.cm-content'),
          null,
          fileName,
        );
      } finally {
        await view.unmount();
      }
    }
  });
}
