import { test } from 'node:test';
import assert from 'node:assert/strict';
import { h, mount } from './helpers/monitoring-dom.mjs';
import { runtime as r } from './helpers/monitoring.mjs';
const options = (events) => ({
  onEvent(event) {
    events.push(event);
  },
});
test('rendering existing content after enabling marks the missing earlier work', async () => {
  const events = [];
  function Broken() {
    throw new Error('existing view failed');
  }
  const View = r.withMonitoring(
    ({ fail }) =>
      h(
        r.ErrorBoundary,
        null,
        fail ? h(Broken) : h('p', null, 'existing content'),
      ),
    'FileTree',
  );
  const tree = (enabled, fail) =>
    h(
      r.OpenVikingProvider,
      { monitoring: enabled ? options(events) : false },
      h(View, { fail }),
    );
  const app = await mount(tree(false, false));
  try {
    await app.render(tree(true, true));
    const failure = events.find(
      (event) => event.name === 'file_tree_render_error',
    );
    assert.equal(failure.params.traceSnapshot.coverage, 'partial');
    assert.ok(
      failure.params.traceSnapshot.gaps.includes('started-before-enabled'),
    );
  } finally {
    await app.dispose();
  }
});

test('a deeper explicit receiver marks the disabled ancestor scope without delivering to it', async () => {
  const events = [];
  function Broken() {
    throw new Error('render');
  }
  const Child = r.withMonitoring(
    () => h(r.ErrorBoundary, null, h(Broken)),
    'FileTree',
  );
  const Parent = r.withMonitoring(
    () => h(r.OpenVikingProvider, { monitoring: options(events) }, h(Child)),
    'FileBrowser',
  );
  const app = await mount(
    h(r.OpenVikingProvider, { monitoring: false }, h(Parent)),
  );
  try {
    const failure = events.find(
      (event) => event.name === 'file_tree_render_error',
    );
    assert.ok(failure.params.traceSnapshot.gaps.includes('disabled-scope'));
    assert.equal(failure.params.traceSnapshot.coverage, 'partial');
  } finally {
    await app.dispose();
  }
});

test('an independent block does not inherit a containing block render workflow', async () => {
  const { useLayoutEffect, useState } = await import('react');
  const events = [];
  let outer;
  function Broken() {
    throw new Error('independent block view');
  }
  const Leaf = r.withMonitoring(
    () => h(r.ErrorBoundary, null, h(Broken)),
    'FileTree',
  );
  const Block = r.withMonitoring(() => h(Leaf), 'Console');
  function OuterView() {
    const binding = r.useMonitoringBinding();
    const [span, setSpan] = useState();
    useLayoutEffect(() => {
      outer = r.startSpan(binding, 'file_browser_preview_load');
      outer.finish();
      setSpan(outer);
    }, [binding]);
    return span ? h(r.MonitoringFlow, { spans: [span] }, h(Block)) : null;
  }
  const Outer = r.withMonitoring(OuterView, 'FileBrowser');
  const app = await mount(
    h(r.OpenVikingProvider, { monitoring: options(events) }, h(Outer)),
  );
  try {
    const failure = events.find(
      (event) => event.name === 'file_tree_render_error',
    );
    assert.equal(failure.source.module.name, 'Console');
    assert.notEqual(failure.trace.traceId, outer.trace.traceId);
    assert.equal(failure.params.traceSnapshot.links.length, 0);
    assert.equal(failure.params.traceSnapshot.coverage, 'complete');
  } finally {
    await app.dispose();
  }
});
