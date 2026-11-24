import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { act, click } from './helpers/command-console-dom.mjs';
const { VikingBotSubmitActions } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-submit.js'))
);
const { composerEvents } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-composer-events.js'))
);

test('native renders stop and follow-up, commercial renders only disabled send; pending input blocks send but keeps stop', async () => {
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  let sends = 0;
  let stops = 0;
  const render = (canSteer, cancel, pending = false) =>
    act(() =>
      root.render(
        createElement(VikingBotSubmitActions, {
          streaming: true,
          canSteer,
          onCancel: cancel
            ? () => {
                stops++;
              }
            : undefined,
          labels: { send: 'Send', stop: 'Stop' },
          props: {
            onSend: () => {
              sends++;
            },
            disabled: pending,
          },
        }),
      ),
    );
  try {
    await render(true, true);
    assert.equal(container.querySelectorAll('button').length, 2);
    await click(container.querySelector('[aria-label="Send"]'));
    await click(container.querySelector('[aria-label="Stop"]'));
    assert.equal(sends, 1);
    assert.equal(stops, 1);
    await render(true, true, true);
    assert.equal(container.querySelector('[aria-label="Send"]').disabled, true);
    assert.equal(
      container.querySelector('[aria-label="Stop"]').disabled,
      false,
    );
    await render(false, false);
    assert.equal(container.querySelector('[aria-label="Stop"]'), null);
    assert.equal(container.querySelector('[aria-label="Send"]').disabled, true);
    await render(true, false);
    assert.equal(container.querySelector('[aria-label="Stop"]'), null);
    assert.equal(
      container.querySelector('[aria-label="Send"]').disabled,
      false,
    );
  } finally {
    await act(() => root.unmount());
    container.remove();
  }
});

test('IME confirmation never reaches the submit handler, including Safari keyCode 229', () => {
  const events = composerEvents(false, false, () => {});
  for (const nativeEvent of [
    { isComposing: true, keyCode: 13 },
    { isComposing: false, keyCode: 229 },
  ]) {
    let stopped = false;
    events.onKeyDownCapture({
      key: 'Enter',
      nativeEvent,
      stopPropagation: () => {
        stopped = true;
      },
    });
    assert.equal(stopped, true);
  }
  let stopped = false;
  events.onKeyDownCapture({
    key: 'Enter',
    nativeEvent: { isComposing: false, keyCode: 13 },
    stopPropagation: () => {
      stopped = true;
    },
  });
  assert.equal(stopped, false);
});
