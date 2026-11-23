import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, StrictMode, useState } from 'react';
import { VikingBotSessionActions } from '@openviking/react/components/viking-bot';
import { mount } from './helpers/module-dom.mjs';

for (const activation of ['click', 'Enter', ' ']) {
  test(`session selection notifies once for current and different sessions (${activation})`, async () => {
    const selected = [];
    function Host() {
      const [currentId, setCurrent] = useState('first');
      return h(VikingBotSessionActions, {
        sessions: {
          currentId,
          items: [
            { id: 'first', title: 'First' },
            { id: 'second', title: 'Second' },
          ],
          onNew() {},
          onSelect(id) {
            selected.push(id);
            setCurrent(id);
          },
        },
      });
    }
    const app = await mount(h(StrictMode, null, h(Host)));
    const trigger = app.container.querySelector('[role=combobox]');
    const choose = async (title, type = activation) => {
      await act(() => trigger.click());
      const option = [...document.querySelectorAll('[role=option]')].find(
        (node) => node.textContent === title,
      );
      await act(() => {
        if (type === 'click') option.click();
        else {
          option.focus();
          option.dispatchEvent(
            new window.KeyboardEvent('keydown', {
              key: type,
              bubbles: true,
              cancelable: true,
            }),
          );
        }
      });
      assert.equal(document.querySelector('[role=listbox]'), null);
      assert.equal(document.activeElement, trigger);
    };
    try {
      await choose('First');
      assert.deepEqual(selected, ['first']);
      await choose('Second', 'click');
      assert.deepEqual(selected, ['first', 'second']);
      await choose('Second');
      assert.deepEqual(selected, ['first', 'second', 'second']);
    } finally {
      await app.unmount();
    }
  });
}
