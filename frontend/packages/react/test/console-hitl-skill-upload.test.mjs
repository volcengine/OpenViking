import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { card, click, mountHitl, settle } from './helpers/console-hitl.mjs';
import { notificationError, toastContent } from './helpers/feedback-dom.mjs';

test('Skill upload shows local validation in a Toast and preserves the server error in a Notification', async () => {
  const uploads = [];
  const view = await mountHitl([card('compile_skill', 'Compile Skill')], {
    locale: 'en-US',
    fetch: async (input, options) => {
      if (new URL(input).pathname === '/api/v1/skills') {
        uploads.push(JSON.parse(options.body).data);
        return Response.json(
          {
            status: 'error',
            error: { code: 'INVALID_ARGUMENT', message: 'Missing skill name' },
          },
          { status: 400 },
        );
      }
      return Response.json({ status: 'ok', result: [] });
    },
  });
  try {
    await view.start();
    await click(view.container.querySelector('[aria-haspopup="dialog"]'));
    const action = (selector) =>
      [...document.querySelectorAll(`${selector} button`)].find(
        (button) => button.textContent === 'Upload skill',
      );
    await click(action('.ov-context-picker'));
    const fileInput = document.querySelector('input[type="file"]');
    const choose = (file) =>
      act(async () => {
        Object.defineProperty(fileInput, 'files', {
          configurable: true,
          value: [file],
        });
        fileInput.dispatchEvent(new window.Event('change', { bubbles: true }));
      });
    await choose({ name: 'other.md' });
    assert.match(
      toastContent()?.textContent ?? '',
      /Select one file named SKILL\.md/,
    );
    assert.equal(action('.ov-viking-bot__compile-skill-upload').disabled, true);
    assert.deepEqual(uploads, []);

    await choose({
      name: 'SKILL.md',
      text: async () => 'Skill without frontmatter',
    });
    await click(action('.ov-viking-bot__compile-skill-upload'));
    await settle();
    assert.equal(
      document.querySelectorAll('.ov-notification--error').length,
      1,
    );
    assert.match(notificationError().textContent, /Missing skill name/);
    assert.match(notificationError().textContent, /Copy error/i);
    assert.deepEqual(uploads, ['Skill without frontmatter']);
    assert.equal(
      action('.ov-viking-bot__compile-skill-upload').disabled,
      false,
    );
    assert.equal(view.requests.length, 1);
  } finally {
    await view.unmount();
  }
});
