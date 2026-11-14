import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import {
  card,
  mountHitl,
  input,
  submit,
  click,
  settle,
} from './helpers/console-hitl.mjs';

test('HITL waits for completion, validates each card and sends one ordered response', async () => {
  let complete;
  const gate = new Promise((resolve) => {
    complete = resolve;
  });
  const view = await mountHitl(
    [
      card('compile_name', '名称'),
      card('compile_output_type', '产物', {
        options: [{ value: 'wiki', label: '知识库' }],
      }),
      card('compile_source', '来源'),
      card('compile_target', '目录', {
        suggested_value: 'viking://resources/output',
      }),
      card('compile_skill', 'Skill', {
        candidates: [{ uri: 'viking://skills/wiki', label: 'Wiki Skill' }],
      }),
      card('compile_instruction', '补充', { current_value: 'Keep citations' }),
      card('compile_schedule', '定时', {
        initial_value: {
          frequency: 'weekly',
          time: '12:27',
          weekday: 'wednesday',
        },
      }),
    ],
    {
      gate,
      hitl: {
        cards: {
          compile_name: {
            fields: {
              value: {
                validate: async (value) =>
                  value === 'reserved' ? '名称已使用' : undefined,
              },
            },
          },
        },
      },
    },
  );
  try {
    await view.start();
    assert.equal(view.container.querySelector('form'), null);
    await act(async () => complete());
    await settle();
    let field = view.container.querySelector('[name=answer]');
    await input(field, 'reserved');
    await submit(view);
    assert.match(view.container.textContent, /名称已使用/);
    assert.equal(field.getAttribute('aria-invalid'), 'true');
    await input(field, 'My Wiki');
    assert.equal(field.hasAttribute('aria-invalid'), false);
    await submit(view);
    await click(view.container.querySelector('input[value=wiki]'));
    await submit(view);
    field = view.container.querySelector('[name=answer]');
    await input(field, 'viking://resources/a\nviking://resources/b');
    await submit(view);
    await submit(view);
    await click(
      view.container.querySelector('input[value="viking://skills/wiki"]'),
    );
    await submit(view);
    await submit(view);
    assert.equal(view.requests.length, 1);
    await submit(view);
    await settle();
    assert.equal(view.requests.length, 2);
    const wire = JSON.parse(view.requests[1].text);
    assert.equal(wire.type, 'request_user_input_response');
    assert.equal(wire.schema_version, '1.0');
    assert.equal(wire.tool_call_id, 'call-1');
    assert.deepEqual(
      wire.answers.map((answer) => answer.value),
      [
        'My Wiki',
        'wiki',
        ['viking://resources/a', 'viking://resources/b'],
        'viking://resources/output',
        'viking://skills/wiki',
        'Keep citations',
        {
          frequency: 'weekly',
          time: '12:27',
          weekday: 'wednesday',
          timezone: 'Asia/Shanghai',
        },
      ],
    );
    const answer = view.props.messages.find((message) => message.answerSummary);
    assert.equal(answer.parts[0].text, view.requests[1].text);
    assert.deepEqual(answer.answerSummary[1], {
      label: '产物',
      value: '知识库',
    });
    assert.deepEqual(answer.answerSummary.at(-1), {
      label: '定时',
      value: '每周三 12:27',
    });
    assert.equal(
      view.saved.filter((message) => message.role === 'user').at(-1).parts[0]
        .text,
      view.requests[1].text,
    );
    assert.equal(view.props.interaction, undefined);
    await view.rerender({ language: 'en-US' });
    assert.match(
      view.props.messages
        .find((message) => message.answerSummary)
        .answerSummary.at(-1).value,
      /Wed.*12:27/,
    );
    assert.equal(view.requests.length, 2);
  } finally {
    await view.unmount();
  }
});

for (const terminal of ['interrupted', 'truncated']) {
  test(`${terminal} streams never activate HITL`, async () => {
    const view = await mountHitl([card('compile_name', 'Name')], { terminal });
    try {
      await view.start();
      assert.equal(view.props.interaction, undefined);
    } finally {
      await view.unmount();
    }
  });
}

test('failed answer send retains the draft, clears errors on change and permits retry', async () => {
  let fail = true;
  const view = await mountHitl([card('compile_name', 'Name')], {
    onAnswer: async () => {
      if (fail) throw new Error('Offline');
    },
  });
  try {
    await view.start();
    await input(view.container.querySelector('[name=answer]'), 'Draft');
    await submit(view);
    await settle();
    assert.equal(view.container.querySelector('[name=answer]').value, 'Draft');
    assert.match(view.container.textContent, /提交失败/);
    await input(view.container.querySelector('[name=answer]'), 'Updated');
    assert.doesNotMatch(view.container.textContent, /提交失败/);
    fail = false;
    await submit(view);
    await settle();
    assert.equal(view.props.interaction, undefined);
    assert.equal(
      JSON.parse(view.requests.at(-1).text).answers[0].value,
      'Updated',
    );
  } finally {
    await view.unmount();
  }
});

test('cancel closes the group without sending; disabled and host-owned conversations stay inert', async () => {
  for (const disabled of [false, true]) {
    const view = await mountHitl([card('compile_name', 'Name')], {
      hitl: disabled ? false : undefined,
    });
    try {
      await view.start();
      if (disabled) assert.equal(view.props.interaction, undefined);
      else await act(async () => view.props.interaction.onCancel());
      assert.equal(view.props.interaction, undefined);
      assert.equal(view.requests.length, 1);
      await view.rerender({ controlled: true });
      assert.deepEqual(view.props.messages, []);
    } finally {
      await view.unmount();
    }
  }
});
