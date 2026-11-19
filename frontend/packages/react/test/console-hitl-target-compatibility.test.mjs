import assert from 'node:assert/strict';
import test from 'node:test';
import { ok, recordingFetch } from './helpers/module-dom.mjs';
import {
  card,
  click,
  input,
  mountHitl,
  submit,
} from './helpers/console-hitl.mjs';

test('host-created targets normalize valid URI values and preserve custom text', async () => {
  for (const [value, expected] of [
    [' viking://resources/wiki/ ', 'viking://resources/wiki'],
    [' host-preset ', ' host-preset '],
    ['viking://agent/skills/example/', 'viking://agent/skills/example'],
  ]) {
    const view = await mountHitl([card('compile_target', 'Target')], {
      hitl: {
        cards: {
          compile_target: {
            createRequest: () => ({ kind: 'text', initialValue: value }),
          },
        },
      },
    });
    try {
      await view.start();
      await submit(view);
      assert.equal(
        JSON.parse(view.requests[1].text).answers[0].value,
        expected,
      );
    } finally {
      await view.unmount();
    }
  }
});

test('an explicit host target serializer owns the wire value', async () => {
  const value = ' viking://resources/wiki/ ';
  const view = await mountHitl([card('compile_target', 'Target')], {
    hitl: {
      cards: {
        compile_target: {
          createRequest: () => ({ kind: 'text', initialValue: value }),
          serialize: (answer) => ({ hostTarget: answer.value }),
        },
      },
    },
  });
  try {
    await view.start();
    await submit(view);
    assert.deepEqual(JSON.parse(view.requests[1].text).answers[0].value, {
      hostTarget: value,
    });
  } finally {
    await view.unmount();
  }
});

test('built-in targets reject Skill children and accept supported skills namespaces', async () => {
  for (const namespace of [
    'viking://agent/skills',
    'viking://user/alice/skills',
    'viking://~/skills',
  ]) {
    const fetch = recordingFetch(({ uri }) => ok({ uri, isDir: true }));
    const view = await mountHitl(
      [
        card('compile_target', 'Target', {
          suggested_value: namespace + '/example',
        }),
      ],
      { fetch, locale: 'en-US' },
    );
    try {
      await view.start();
      assert.equal(view.props.interaction.request.initialValue, undefined);
      await click(
        view.container.querySelector(
          '.ov-viking-bot__interaction-custom-choice input',
        ),
      );
      await input(
        view.container.querySelector('input[name=custom-answer]'),
        namespace + '/example',
      );
      await submit(view);
      assert.match(
        view.container.textContent,
        /Choose the skills namespace root/,
      );
      assert.equal(view.requests.length, 1);
      assert.equal(fetch.calls.length, 0);

      await input(
        view.container.querySelector('input[name=custom-answer]'),
        ' ' + namespace + '/ ',
      );
      await submit(view);
      assert.equal(fetch.calls[0].uri, namespace);
      assert.equal(
        JSON.parse(view.requests[1].text).answers[0].value,
        namespace,
      );
    } finally {
      await view.unmount();
    }
  }
});

test('built-in targets reject a canonical Skill child returned for a selected alias', async () => {
  const fetch = recordingFetch(() =>
    ok({
      uri: 'viking://user/alice/skills/example',
      isDir: true,
    }),
  );
  const view = await mountHitl(
    [
      card('compile_target', 'Target', {
        suggested_value: 'viking://~/skills',
      }),
    ],
    { fetch, locale: 'en-US' },
  );
  try {
    await view.start();
    await submit(view);
    assert.match(
      view.container.textContent,
      /Choose the skills namespace root/,
    );
    assert.equal(view.requests.length, 1);
    assert.equal(fetch.calls.length, 1);
  } finally {
    await view.unmount();
  }
});
