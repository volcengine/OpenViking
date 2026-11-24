import assert from 'node:assert/strict';
import test from 'node:test';
import {
  card,
  mountHitl,
  input,
  click,
  settle,
} from './helpers/console-hitl.mjs';

for (const [scenario, payload] of [
  ['initially empty', {}],
  ['cleared prefill', { current_value: 'Keep citations' }],
]) {
  test(`optional instruction submits an empty value: ${scenario}`, async () => {
    const view = await mountHitl([
      card('compile_name', '名称'),
      card('compile_instruction', '补充要求（可选）', payload),
    ]);
    const submitButton = () =>
      view.container.querySelector('button[type=submit]');
    try {
      await view.start();
      const name = view.container.querySelector('input[name=answer]');
      assert.equal(name.required, true);
      assert.equal(submitButton().disabled, true);
      await input(name, 'wiki-config');
      await click(submitButton());
      await settle();

      const instruction = view.container.querySelector('textarea[name=answer]');
      assert.ok(instruction);
      assert.equal(instruction.required, false);
      assert.equal(instruction.value, payload.current_value ?? '');
      if (payload.current_value) await input(instruction, '');
      assert.equal(instruction.value, '');
      assert.equal(submitButton().disabled, false);
      await click(submitButton());
      await settle();

      assert.equal(view.requests.length, 2);
      assert.deepEqual(JSON.parse(view.requests[1].text).answers, [
        { card_type: 'compile_name', title: '名称', value: 'wiki-config' },
        {
          card_type: 'compile_instruction',
          title: '补充要求（可选）',
          value: '',
        },
      ]);
      assert.equal(view.props.interaction, undefined);
    } finally {
      await view.unmount();
    }
  });
}
