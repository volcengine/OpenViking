import assert from 'node:assert/strict';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement as h } from 'react';
import { createPortal } from 'react-dom';
import { mount, ok, settle } from './helpers/module-dom.mjs';

const { Console, OpenVikingProvider, createOvClient, presentCard } =
  await import(
    pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'console-overlays.js'))
  );
const fetch = async () => ok([]);
const interaction = {
  requestKey: 'name',
  request: { kind: 'text', question: 'Wiki name', multiline: false },
};
const frame = (mode, conversation) =>
  h(
    OpenVikingProvider,
    { fetch },
    h(Console, {
      mode,
      conversation: { messages: [], composer: false, ...conversation },
    }),
  );
const click = async (node) =>
  act(async () => {
    assert.ok(node);
    node.focus();
    node.click();
  });

function BodyPortal() {
  return createPortal(
    h('input', { 'aria-label': 'Portaled clarification' }),
    document.body,
  );
}

test('a controlled Console removes a host body portal while conversation is inactive', async () => {
  const conversation = { interaction, interactionSlot: h(BodyPortal) };
  const host = await mount(frame('conversation', conversation));
  try {
    assert.ok(document.querySelector('[aria-label="Portaled clarification"]'));
    await host.render(frame('command', conversation));
    assert.equal(
      document.querySelector('[aria-label="Portaled clarification"]'),
      null,
    );
    const terminal = host.container.querySelector('textarea');
    await act(async () => terminal.focus());
    await settle();
    assert.equal(document.activeElement === terminal, true);
    await host.render(frame('conversation', conversation));
    assert.ok(document.querySelector('[aria-label="Portaled clarification"]'));
  } finally {
    await host.unmount();
  }
});

test('inactive Skill upload releases its focus and body lock while its inline answer is retained', async () => {
  const initialOverflow = document.body.style.overflowY;
  const client = createOvClient({ fetch });
  const skill = {
    ...presentCard(
      {
        id: 'skill',
        cardType: 'compile_skill',
        title: 'Skill',
        payload: {},
        request: {
          kind: 'choice',
          options: [
            {
              value: 'viking://agent/skills/wiki/SKILL.md',
              label: 'Wiki Skill',
            },
          ],
          initialValue: 'viking://agent/skills/wiki/SKILL.md',
        },
      },
      'en-US',
      { client },
      [],
      [],
      [],
    ),
    requestKey: 'skill',
  };
  const conversation = { interaction: skill };
  const host = await mount(frame('conversation', conversation));
  const named = (scope, text) =>
    [...document.querySelectorAll(`${scope} button`)].find(
      (button) => button.textContent === text,
    );
  try {
    const selected = host.container.querySelector(
      'input[value="viking://agent/skills/wiki/SKILL.md"]',
    );
    await click(selected);
    assert.equal(selected.checked, true);
    await click(host.container.querySelector('[aria-haspopup="dialog"]'));
    await settle();
    await click(named('.ov-context-picker', 'Upload skill'));
    assert.ok(document.querySelector('.ov-viking-bot__compile-skill-upload'));
    assert.equal(document.body.style.overflowY, 'hidden');
    await host.render(frame('command', conversation));
    assert.equal(
      document.querySelector('.ov-viking-bot__compile-skill-upload'),
      null,
    );
    assert.equal(document.body.style.overflowY, initialOverflow);
    const terminal = host.container.querySelector('textarea');
    await act(async () => terminal.focus());
    await settle();
    assert.equal(document.activeElement === terminal, true);
    assert.equal(
      host.container.querySelector(
        'input[value="viking://agent/skills/wiki/SKILL.md"]',
      ) === selected,
      true,
    );
    assert.equal(selected.checked, true);
  } finally {
    await host.unmount();
  }
});
