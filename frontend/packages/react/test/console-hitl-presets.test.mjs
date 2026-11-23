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
