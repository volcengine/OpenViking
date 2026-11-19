import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  act,
  createElement as h,
  createRef,
  forwardRef,
  useImperativeHandle,
  useState,
} from 'react';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { mount, setTextarea } from './helpers/module-dom.mjs';
import { retrySlot } from './helpers/error-retry.mjs';

const internal = (name) =>
  import(pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, `${name}.js`)));
const { ErrorBoundary } = await internal('error-boundary');
const { LazyContent } = await internal('lazy-content');

test('a rejected lazy load retries on reset without refetching or remounting healthy siblings', async (t) => {
  t.mock.method(console, 'error', () => {});
  let attempts = 0;
  let allowLoad = false;
  const ref = createRef();
  const Content = forwardRef(function Content({ title }, handle) {
    const [value] = useState('retained view state');
    useImperativeHandle(handle, () => ({ value }), [value]);
    return h('p', null, `${title}: ${value}`);
  });
  const load = async () => {
    attempts++;
    if (!allowLoad) throw new Error('chunk unavailable');
    return { default: Content };
  };
  const tree = (title = 'Recovered') =>
    h(
      'main',
      null,
      h('textarea', { defaultValue: '' }),
      h(
        ErrorBoundary,
        { fallback: retrySlot },
        h(LazyContent, {
          load,
          contentProps: { ref, title },
          fallback: 'Loading',
        }),
      ),
    );
  const host = await mount(tree());
  try {
    const input = host.container.querySelector('textarea');
    await setTextarea(input, 'Draft');
    assert.equal(attempts, 1);
    await act(() => host.container.querySelector('[data-host-retry]').click());
    assert.equal(
      attempts,
      2,
      'explicit retry reruns a still-failing loader once',
    );
    allowLoad = true;
    await act(() => host.container.querySelector('[data-host-retry]').click());
    assert.equal(attempts, 3);
    assert.match(host.container.textContent, /Recovered: retained view state/);
    assert.equal(ref.current.value, 'retained view state');
    const handle = ref.current;
    const content = host.container.querySelector('p');
    await host.render(tree('Updated'));
    assert.equal(
      attempts,
      3,
      'healthy prop updates retain the loaded lazy instance',
    );
    assert.equal(host.container.querySelector('p'), content);
    assert.equal(ref.current, handle);
    assert.equal(host.container.querySelector('textarea'), input);
    assert.equal(input.value, 'Draft');
  } finally {
    await host.unmount();
  }
});
