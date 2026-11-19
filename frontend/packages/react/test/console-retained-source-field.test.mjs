import assert from 'node:assert/strict';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement as h } from 'react';
import { mount, ok, settle } from './helpers/module-dom.mjs';

const { Console, OpenVikingProvider, createOvClient, presentCard } =
  await import(
    pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'console-overlays.js'))
  );

test('removing all suggested sources survives mode switches without reapplying suggestions', async () => {
  const uri = 'viking://resources/suggested';
  let statReads = 0;
  const fetch = async (input) => {
    const path = new URL(input, 'https://ov.test').pathname;
    if (path.endsWith('/fs/stat')) {
      statReads++;
      return ok({ uri, isDir: true });
    }
    return ok([]);
  };
  const client = createOvClient({ fetch });
  const interaction = {
    ...presentCard(
      {
        id: 'source',
        cardType: 'compile_source',
        title: 'Source',
        payload: {},
        request: { kind: 'text', question: 'Source' },
      },
      'en-US',
      { client },
      [],
      [uri],
      [],
    ),
    requestKey: 'source',
  };
  const frame = (mode) =>
    h(
      OpenVikingProvider,
      { fetch },
      h(Console, {
        mode,
        conversation: { messages: [], composer: false, interaction },
      }),
    );
  const host = await mount(frame('conversation'));
  const click = async (node) =>
    act(async () => {
      assert.ok(node);
      node.focus();
      node.click();
    });
  try {
    await settle();
    assert.equal(statReads, 1);
    assert.ok(host.container.querySelector(`[aria-label="Remove ${uri}"]`));
    const trigger = host.container.querySelector('[aria-haspopup="dialog"]');
    await click(trigger);
    await settle();
    const cancel = [
      ...document.querySelectorAll('.ov-context-picker button'),
    ].find((button) => button.textContent === 'Cancel');
    await click(cancel);
    await settle();
    await click(host.container.querySelector(`[aria-label="Remove ${uri}"]`));
    assert.equal(
      host.container.querySelector('.ov-viking-bot__compile-source-tag'),
      null,
    );
    const readsAfterRemoval = statReads;
    await host.render(frame('command'));
    await host.render(frame('conversation'));
    await settle();
    assert.equal(
      host.container.querySelector('[aria-haspopup="dialog"]') === trigger,
      true,
    );
    assert.equal(
      host.container.querySelector('.ov-viking-bot__compile-source-tag'),
      null,
    );
    assert.equal(statReads, readsAfterRemoval);
  } finally {
    await host.unmount();
  }
});
