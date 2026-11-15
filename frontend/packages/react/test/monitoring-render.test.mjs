import { test } from 'node:test';
import assert from 'node:assert/strict';
import { act, h, mount } from './helpers/monitoring-dom.mjs';
import { runtime as r } from './helpers/monitoring.mjs';
const options = (events) => ({
  onEvent(event) {
    events.push(event);
  },
});

test('lazy failure, render propagation and retry preserve the component and create a new incident', async () => {
  const events = [],
    error = new Error('private cached import error');
  let loads = 0;
  const load = async () => {
    loads++;
    throw error;
  };
  function View() {
    return h(
      r.ErrorBoundary,
      null,
      h(r.LazyContent, {
        load,
        monitoringTarget: 'file_preview_viewer_load',
        contentProps: {},
        fallback: 'loading',
      }),
    );
  }
  const Preview = r.withMonitoring(View, 'FilePreview');
  const app = await mount(
    h(r.OpenVikingProvider, { monitoring: options(events) }, h(Preview)),
  );
  try {
    assert.equal(loads, 1);
    const failures = () =>
      events.filter((event) => event.name === 'file_preview_viewer_load_fail');
    assert.equal(failures().length, 1);
    const render = events.find(
      (event) => event.name === 'file_preview_render_error',
    );
    assert.equal(render.source.component.name, 'FilePreview');
    assert.equal(render.params.incidentId, failures()[0].params.incidentId);
    assert.equal(render.params.causeEventId, failures()[0].eventId);
    assert.equal(JSON.stringify(events).includes(error.message), false);
    await app.event(app.container.querySelector('button'), 'click');
    assert.equal(loads, 2);
    assert.equal(failures().length, 2);
    assert.notEqual(
      failures()[0].params.incidentId,
      failures()[1].params.incidentId,
    );
    assert.notEqual(failures()[0].trace.traceId, failures()[1].trace.traceId);
    assert.equal(failures()[1].trace.trigger, 'retry');
    assert.equal(
      failures()[1].params.traceSnapshot.links[0].relation,
      'retry-of',
    );
  } finally {
    await app.dispose();
  }
});

test('omitted monitoring, explicit false and empty subscriptions never deliver lazy failures', async () => {
  for (const config of [
    undefined,
    false,
    {
      events: [],
      onEvent() {
        assert.fail('disabled');
      },
    },
  ]) {
    const events = [];
    const load = async () => {
      throw new Error('load failed');
    };
    const Preview = r.withMonitoring(
      () =>
        h(
          r.ErrorBoundary,
          null,
          h(r.LazyContent, {
            load,
            monitoringTarget: 'file_preview_viewer_load',
            contentProps: {},
            fallback: 'loading',
          }),
        ),
      'FilePreview',
    );
    const app = await mount(
      h(
        r.OpenVikingProvider,
        { monitoring: options(events) },
        h(
          r.OpenVikingProvider,
          { monitoring: config === undefined ? false : config },
          h(Preview),
        ),
      ),
    );
    assert.equal(events.length, 0);
    await app.dispose();
  }
});

test('changing the receiver does not restart an in-flight lazy import or replay a finished one', async () => {
  const a = [],
    b = [];
  let resolve,
    loads = 0;
  const load = () => {
    loads++;
    return new Promise((done) => {
      resolve = done;
    });
  };
  const Preview = r.withMonitoring(
    () =>
      h(r.LazyContent, {
        load,
        monitoringTarget: 'file_preview_viewer_load',
        contentProps: {},
        fallback: 'loading',
      }),
    'FilePreview',
  );
  const tree = (monitoring) =>
    h(r.OpenVikingProvider, { monitoring }, h(Preview));
  const app = await mount(tree(options(a)));
  await app.render(tree(options(b)));
  await act(() => resolve({ default: () => h('p', null, 'content') }));
  assert.equal(loads, 1);
  assert.equal(a.length, 0);
  assert.equal(
    b.filter((event) => event.name === 'file_preview_viewer_load_ready').length,
    1,
  );
  await app.render(tree(false));
  await app.render(tree(options(b)));
  assert.equal(loads, 1);
  assert.equal(b.length, 1);
  await app.dispose();
});
