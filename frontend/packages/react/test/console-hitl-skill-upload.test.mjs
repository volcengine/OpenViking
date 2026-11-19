import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import {
  card,
  click,
  input,
  mountHitl,
  settle,
} from './helpers/console-hitl.mjs';
import { notificationError, toastContent } from './helpers/feedback-dom.mjs';

const systemRoot = 'viking://system';
const systemSkill = `${systemRoot}/compile/example-skills/llm-wiki/SKILL.md`;
const relativePatterns = [
  '**/*[lL][lL][mM]-[wW][iI][kK][iI]/[sS][kK][iI][lL][lL].[mM][dD]*/SKILL.md',
  '**/*[lL][lL][mM]-[wW][iI][kK][iI]*/SKILL.md',
];
const skillSearchRoots = [
  systemRoot,
  'viking://~/skills',
  'viking://agent/skills',
];

for (const [
  keyword,
  patterns,
  roots = skillSearchRoots,
  skill = systemSkill,
] of [
  ['llm', ['**/*[lL][lL][mM]*/SKILL.md']],
  ['LLM', ['**/*[lL][lL][mM]*/SKILL.md']],
  ['SKILL.md', ['**/SKILL.md']],
  [
    'llm/SKILL.md',
    [
      '**/*[lL][lL][mM]/[sS][kK][iI][lL][lL].[mM][dD]*/SKILL.md',
      '**/*[lL][lL][mM]*/SKILL.md',
    ],
  ],
  ...[
    [systemRoot, systemSkill],
    ['viking://agent/skills', 'viking://agent/skills/llm-wiki/SKILL.md'],
    [
      'viking://user/alice/skills',
      'viking://user/alice/skills/llm-wiki/SKILL.md',
    ],
  ].map(([root, skill]) => {
    const patterns =
      root === systemRoot
        ? [
            '**/*[cC][oO][mM][pP][iI][lL][eE]/[eE][xX][aA][mM][pP][lL][eE]-[sS][kK][iI][lL][lL][sS]/' +
              relativePatterns[0].slice('**/*'.length),
            '**/*[cC][oO][mM][pP][iI][lL][eE]/[eE][xX][aA][mM][pP][lL][eE]-[sS][kK][iI][lL][lL][sS]/' +
              relativePatterns[1].slice('**/*'.length),
          ]
        : relativePatterns;
    return [skill, patterns, [root], skill];
  }),
]) {
  test(`the compile Skill picker can select the Skill when searching for ${keyword}`, async () => {
    const requests = [];
    const view = await mountHitl([card('compile_skill', 'Compile Skill')], {
      locale: 'en-US',
      fetch: async (url, options) => {
        if (new URL(url).pathname !== '/api/v1/search/glob') {
          return Response.json({ status: 'ok', result: [] });
        }
        const body = JSON.parse(options.body);
        requests.push({ ...body, method: options.method });
        if (body.uri === 'viking://~/skills') {
          return Response.json(
            {
              status: 'error',
              error: { code: 'NOT_FOUND', message: 'Missing skills' },
            },
            { status: 404 },
          );
        }
        return Response.json({
          status: 'ok',
          result: {
            matches:
              body.uri === roots[0] && body.pattern === patterns.at(-1)
                ? [skill]
                : [],
          },
        });
      },
    });
    try {
      await view.start();
      await click(view.container.querySelector('[aria-haspopup="dialog"]'));
      const picker = document.querySelector('.ov-context-picker');
      await input(picker.querySelector('input[type="search"]'), keyword);
      await act(async () => new Promise((resolve) => setTimeout(resolve, 350)));

      assert.deepEqual(
        requests,
        roots.flatMap((uri) =>
          patterns.map((pattern) => ({
            uri,
            pattern,
            node_limit: 256,
            method: 'POST',
          })),
        ),
      );
      const choice = picker.querySelector(
        '[role=treeitem][aria-label="SKILL.md"]',
      );
      assert.ok(choice);
      await click(choice);
      assert.equal(choice.getAttribute('aria-selected'), 'true');
    } finally {
      await view.unmount();
    }
  });
}

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
