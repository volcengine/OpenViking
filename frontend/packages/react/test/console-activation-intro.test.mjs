import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { act } from './helpers/command-console-dom.mjs';
import { mount } from './helpers/console-dom.mjs';

const tick = () => act(() => new Promise((resolve) => setTimeout(resolve, 10)));
const noRequests = async () => assert.fail('Controlled chat must not fetch');
const propsFor = (activation, extra = {}) => ({
  locale: 'en-US',
  defaultMode: 'conversation',
  conversation: { messages: [], composer: false },
  chatOptions: { activation },
  ...extra,
});
const customIntro = (text = 'Host introduction') =>
  h('div', { 'data-intro': true }, text);
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

test('introduction overrides restore defaults without new checks or losing the draft', async () => {
  const calls = { eligible: 0, status: 0, start: 0 };
  let running = false;
  const activation = {
    media: undefined,
    async isEligible() {
      calls.eligible++;
      return true;
    },
    async getStatus() {
      calls.status++;
      return running ? 'ready' : 'not-started';
    },
    async start() {
      calls.start++;
      running = true;
    },
  };
  const changes = [];
  const conversation = {
    messages: [],
    composer: false,
    quickInputs: [
      { id: 'a', label: 'First draft', prompt: 'Draft A' },
      { id: 'b', label: 'Second draft', prompt: 'Draft B' },
    ],
    onDraftChange: (value) => changes.push(value),
  };
  const props = propsFor(activation, { conversation });
  const host = await mount(noRequests, props);
  const root = host.container;
  const title = () => root.querySelector('.ov-console-activation__title');
  const description = () =>
    root.querySelector('.ov-console-activation__description');
  try {
    await tick();
    const baseline = { ...calls };
    assert.match(title().textContent, /Turn scattered material/);
    assert.match(description().textContent, /Compile directories/);
    assert.ok(root.querySelector('.ov-console-activation__artwork'));
    activation.media = h('div', { 'data-media': true }, 'Host media');
    await host.render({ ...props });
    for (const [extra, expected] of [
      [
        {
          labels: {
            activationTitle: 'Host title',
            activationDescription: 'Host description',
          },
        },
        'text',
      ],
      [
        { labels: { activationTitle: '', activationDescription: '' } },
        'hidden-text',
      ],
      [{ slots: { activationIntro: customIntro() } }, 'custom'],
      [{ slots: { activationIntro: null } }, 'hidden'],
      [{}, 'default'],
    ]) {
      await host.render({ ...props, ...extra });
      await tick();
      assert.deepEqual(calls, baseline);
      assert.ok(host.button('Enable VikingBot'));
      if (expected === 'text') {
        assert.equal(title().textContent, 'Host title');
        assert.equal(description().textContent, 'Host description');
      } else if (expected === 'default')
        assert.match(title().textContent, /Turn scattered material/);
      else {
        assert.equal(title(), null);
        assert.equal(description(), null);
      }
      assert.equal(
        Boolean(root.querySelector('[data-intro]')),
        expected === 'custom',
      );
      assert.equal(
        Boolean(root.querySelector('[data-media]')),
        ['text', 'hidden-text', 'default'].includes(expected),
      );
      assert.equal(
        Boolean(root.querySelector('.ov-console-activation__cover')),
        !['custom', 'hidden'].includes(expected),
      );
    }
    await host.press('Enable VikingBot');
    await tick();
    await host.press('First draft');
    // Release quick-input ownership via the public takeover callback so the
    // next prompt appends; replacement would conceal an accidental draft reset.
    await host.render({
      ...props,
      conversation: { ...conversation, onQuickInputSelect() {} },
    });
    await host.press('First draft');
    const readyCalls = { ...calls };
    await host.render({
      ...props,
      slots: { activationIntro: customIntro('Changed') },
      labels: { activationTitle: 'Changed title' },
    });
    await tick();
    await host.press('Second draft');
    assert.equal(changes.at(-1), 'Draft A\nDraft B');
    assert.deepEqual(calls, readyCalls);
  } finally {
    await host.dispose();
  }
});

test('custom or hidden introductions keep checking, Enable and Retry lifecycle controls', async () => {
  const eligible = deferred(),
    started = deferred(),
    ready = deferred();
  const calls = { eligible: 0, status: 0, start: 0 };
  let initial = true,
    running = false;
  const activation = {
    async isEligible() {
      calls.eligible++;
      return initial ? eligible.promise : true;
    },
    async getStatus() {
      calls.status++;
      return running ? ready.promise : 'not-started';
    },
    async start() {
      if (++calls.start === 1) throw new Error('Start failed');
      await started.promise;
      running = true;
    },
  };
  const props = propsFor(activation, {
    slots: { activationIntro: customIntro() },
  });
  const host = await mount(noRequests, props);
  const card = () => host.container.querySelector('.ov-console-activation');
  try {
    assert.match(card().textContent, /Checking VikingBot status/);
    assert.equal(host.container.querySelector('[data-intro]'), null);
    const pendingCalls = { ...calls };
    await host.render({
      ...props,
      labels: { activationTitle: 'Pending title' },
    });
    await tick();
    assert.deepEqual(calls, pendingCalls);
    initial = false;
    await act(() => eligible.resolve(true));
    await tick();
    assert.ok(host.container.querySelector('[data-intro]'));
    await host.press('Enable VikingBot');
    await tick();
    assert.ok(host.button('Retry'));
    assert.ok(host.container.querySelector('[data-intro]'));
    await host.press('Retry');
    await tick();
    assert.match(card().textContent, /Enabling VikingBot/);
    const startingCalls = { ...calls };
    await host.render({ ...props, slots: { activationIntro: null } });
    await tick();
    assert.equal(host.container.querySelector('[data-intro]'), null);
    assert.match(card().textContent, /Enabling VikingBot/);
    assert.deepEqual(calls, startingCalls);
    await act(() => started.resolve());
    await tick();
    assert.match(card().textContent, /Checking VikingBot status/);
    assert.equal(host.button('Retry'), undefined);
    await act(() => ready.resolve('ready'));
    await tick();
    assert.ok(host.container.querySelector('.ov-viking-bot'));
    assert.equal(card(), null);
    assert.equal(calls.start, 2);
  } finally {
    await host.dispose();
  }
});
