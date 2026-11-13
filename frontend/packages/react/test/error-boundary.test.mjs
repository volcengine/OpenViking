import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement as h, lazy, Suspense, useState } from 'react';
import { act } from 'react';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { mount, setTextarea } from './helpers/module-dom.mjs';

const { ErrorBoundary } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'error-boundary.js'))
);
function Crash() {
  throw new Error('intentional render failure');
}

test('boundaries preserve sibling DOM/state, report the component stack and recover explicitly', async () => {
  const errors = [];
  let broken = true;
  function Content() {
    return broken ? h(Crash) : h('p', null, 'Recovered');
  }
  function Host() {
    const [value, setValue] = useState('');
    return h(
      'main',
      null,
      h('textarea', {
        value,
        onChange: (event) => setValue(event.target.value),
      }),
      h(
        ErrorBoundary,
        { onError: (error, info) => errors.push({ error, info }) },
        h(Content),
      ),
    );
  }
  const host = await mount(h(Host));
  try {
    const input = host.container.querySelector('textarea');
    await setTextarea(input, 'draft survives');
    assert.equal(errors.length, 1);
    assert.match(errors[0].error.message, /intentional/);
    assert.match(errors[0].info.componentStack, /Crash/);
    broken = false;
    await act(() => host.container.querySelector('button').click());
    assert.match(host.container.textContent, /Recovered/);
    assert.equal(host.container.querySelector('textarea'), input);
    assert.equal(input.value, 'draft survives');
  } finally {
    await host.unmount();
  }
});

test('identity changes reset only failed content; locale changes update fallback without retrying', async () => {
  const errors = [];
  const tree = (identity, locale, content = h(Crash)) =>
    h(
      ErrorBoundary,
      {
        resetKeys: [identity],
        locale,
        onError: (error) => errors.push(error),
      },
      content,
    );
  const host = await mount(tree('one', 'zh-CN'));
  try {
    assert.match(host.container.textContent, /此区域暂时无法显示/);
    await host.render(tree('one', 'en-US'));
    assert.match(
      host.container.textContent,
      /This section could not be displayed/,
    );
    assert.equal(errors.length, 1);
    await host.render(tree('two', 'en-US'));
    assert.equal(
      errors.length,
      2,
      'a still-broken new identity is attempted once',
    );
    await host.render(tree('three', 'en-US', h('p', null, 'Healthy file')));
    assert.match(host.container.textContent, /Healthy file/);
  } finally {
    await host.unmount();
  }
});

test('lazy rejection is caught outside Suspense and a failed fallback reaches the outer boundary', async () => {
  const Lazy = lazy(() => Promise.reject(new Error('chunk unavailable')));
  const caught = [];
  const host = await mount(
    h(
      ErrorBoundary,
      { onError: (error) => caught.push(error) },
      h(Suspense, { fallback: 'Loading' }, h(Lazy)),
    ),
  );
  try {
    assert.equal(caught[0].message, 'chunk unavailable');
    assert.match(host.container.textContent, /Retry display/);
    await host.render(
      h(
        ErrorBoundary,
        {
          resetKeys: ['fallback'],
          fallback: 'Outer fallback',
          onError: () => {},
        },
        h(ErrorBoundary, { fallback: h(Crash), onError: () => {} }, h(Crash)),
      ),
    );
    assert.equal(host.container.textContent, 'Outer fallback');
  } finally {
    await host.unmount();
  }
});
