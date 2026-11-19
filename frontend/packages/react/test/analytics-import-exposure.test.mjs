import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
  cloneElement,
  createElement as h,
  createRef,
  useState,
} from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowserActionBar } from '@openviking/react/components/file-browser-action-bar';
import { mount } from './helpers/module-dom.mjs';
import { exposureObserver } from './helpers/exposure-observer.mjs';

for (const hostRef of [false, true]) {
  test(`delayed import trigger exposes on mount, including host ref=${hostRef}`, async () => {
    const observer = exposureObserver();
    const events = [];
    const anchor = createRef();
    let reveal;
    function Delayed({ trigger }) {
      const [visible, setVisible] = useState(false);
      reveal = setVisible;
      return visible ? trigger : null;
    }
    const mounted = await mount(
      h(
        OpenVikingProvider,
        {
          analytics: {
            events: ['openviking_file_browser_import_resources_show'],
            onEvent: (event) => events.push(event.name),
          },
        },
        h(FileBrowserActionBar, {
          onImportResources() {},
          renderImportResources: (trigger) =>
            h(Delayed, {
              trigger: hostRef
                ? cloneElement(trigger, { ref: anchor })
                : trigger,
            }),
        }),
      ),
    );
    try {
      assert.equal(mounted.container.querySelectorAll('button').length, 0);
      assert.equal(observer.active, 0);
      await act(async () => reveal(true));
      assert.equal(mounted.container.querySelectorAll('button').length, 1);
      if (hostRef)
        assert.equal(anchor.current, mounted.container.querySelector('button'));
      assert.equal(observer.active, 1);
      await act(async () => observer.expose());
      await act(async () => observer.expose());
      assert.deepEqual(events, [
        'openviking_file_browser_import_resources_show',
      ]);
      await act(async () => reveal(false));
      assert.equal(observer.active, 0);
      if (hostRef) assert.equal(anchor.current, null);
      await act(async () => reveal(true));
      await act(async () => observer.expose());
      assert.equal(events.length, 2);
    } finally {
      await mounted.unmount();
      observer.restore();
    }
  });
}
