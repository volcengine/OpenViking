import assert from 'node:assert/strict';
import test from 'node:test';
import { card, click, mountHitl, submit } from './helpers/console-hitl.mjs';

const outputType = card('compile_output_type', 'Output type', {
  options: [
    { value: 'wiki', label: 'Wiki' },
    { value: 'markdown', label: 'Markdown' },
  ],
});
const schedule = card('compile_schedule', 'Schedule');

test('a supported report skill determines the output without enabling a schedule', async () => {
  const skillUri = 'viking://system/skills/daily-report/SKILL.md';
  const view = await mountHitl([
    outputType,
    card('compile_skill', 'Skill', {
      candidates: [{ uri: skillUri, label: 'Daily Report' }],
    }),
    schedule,
  ]);
  try {
    await view.start();
    assert.equal(view.props.interaction.request.kind, 'choice');
    await click(view.container.querySelector(`input[value="${skillUri}"]`));
    await submit(view);
    assert.equal(view.props.interaction.request.kind, 'schedule');
    assert.equal(
      view.container.querySelector('input[value=none]').checked,
      true,
    );
    await submit(view);
    assert.deepEqual(
      JSON.parse(view.requests[1].text).answers.map(({ value }) => value),
      ['markdown', skillUri, { frequency: 'disabled' }],
    );
  } finally {
    await view.unmount();
  }
});

test('a localized quick-input prompt without a known skill still asks for the output type', async () => {
  const view = await mountHitl([outputType, schedule], {
    prompt: '基于选中的目录生成日报，并设置定时更新',
  });
  try {
    await view.start();
    assert.equal(view.props.interaction.request.kind, 'choice');
    assert.deepEqual(
      view.props.interaction.request.options.map(({ value }) => value),
      ['wiki', 'markdown'],
    );
    assert.equal(view.requests.length, 1);
  } finally {
    await view.unmount();
  }
});

test('a sole Skill stays selectable and then appears only in the picker value', async () => {
  const skill = 'viking://system/skills/wiki/SKILL.md';
  const view = await mountHitl(
    [
      card('compile_skill', 'Skill', {
        candidates: [{ uri: skill, label: 'Wiki' }],
      }),
    ],
    { fetch: async () => Response.json({ status: 'ok', result: [] }) },
  );
  try {
    await view.start();
    const trigger = () =>
      view.container.querySelector('.ov-viking-bot__compile-skill button');
    assert.equal(
      view.container.querySelectorAll('input[type="radio"]').length,
      1,
    );
    assert.equal(
      view.container.querySelector('button[type="submit"]').disabled,
      true,
    );
    await click(view.container.querySelector(`input[value="${skill}"]`));
    assert.match(trigger().textContent, new RegExp(skill));
    assert.equal(trigger().querySelector('span[title]').title, skill);
    assert.equal(
      view.container.querySelectorAll('input[type="radio"]').length,
      0,
    );
    assert.equal(
      view.container.querySelector('button[type="submit"]').disabled,
      false,
    );
    await submit(view);
    assert.equal(JSON.parse(view.requests[1].text).answers[0].value, skill);
  } finally {
    await view.unmount();
  }
});

test('multiple Skill candidates preserve descriptions and reflect the selected URI', async () => {
  const skills = [
    'viking://system/skills/wiki/SKILL.md',
    'viking://system/skills/report/SKILL.md',
    'viking://system/skills/import/SKILL.md',
    'viking://system/skills/custom/SKILL.md',
  ];
  const descriptions = [
    'Preserve original source citations',
    undefined,
    { text: 'Invalid description' },
    '',
  ];
  const view = await mountHitl(
    [
      card('compile_skill', 'Skill', {
        candidates: skills.map((uri, index) => ({
          uri,
          label: `Skill ${index + 1}`,
          description: descriptions[index],
        })),
      }),
    ],
    { fetch: async () => Response.json({ status: 'ok', result: [] }) },
  );
  try {
    await view.start();
    assert.deepEqual(
      Array.from(
        view.container.querySelectorAll(
          '.ov-viking-bot__interaction-description',
        ),
        (node) => node.textContent,
      ),
      [descriptions[0], skills[1], skills[2]],
    );
    await click(view.container.querySelector(`input[value="${skills[0]}"]`));
    await click(view.container.querySelector(`input[value="${skills[1]}"]`));
    assert.equal(
      view.container.querySelectorAll('input[type="radio"]').length,
      4,
    );
    assert.equal(
      view.container.querySelector(`input[value="${skills[0]}"]`).checked,
      false,
    );
    assert.equal(
      view.container.querySelector(`input[value="${skills[1]}"]`).checked,
      true,
    );
    await view.rerender({ language: 'en-US' });
    assert.match(
      view.container.querySelector('.ov-viking-bot__compile-skill button')
        .textContent,
      new RegExp(skills[1]),
    );
    await submit(view);
    assert.equal(JSON.parse(view.requests[1].text).answers[0].value, skills[1]);
  } finally {
    await view.unmount();
  }
});
