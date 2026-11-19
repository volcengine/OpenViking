import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok } from './helpers/module-dom.mjs';

test('search dates cannot be reversed using the calendar or keyboard', async () => {
  const view = await mount(
    h(
      OpenVikingProvider,
      { baseURL: 'https://ov.test', fetch: async () => ok([]) },
      h(FileBrowser, { locale: 'en-US' }),
    ),
  );
  const click = (element) => act(async () => element.click());
  const field = (name) => document.querySelector(`input[aria-label="${name}"]`);
  const picker = (input) => input.closest('.ov-date-picker');
  const day = (input, number) =>
    Array.from(
      document
        .getElementById(input.getAttribute('aria-controls'))
        .querySelectorAll(
          '.ov-date-picker__cell:not(.ov-date-picker__cell--outside)',
        ),
    ).find((button) => button.textContent === String(number));
  const now = new Date();
  const date = (day) =>
    `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${day}`;
  const enterDate = async (input, value, method = 'enter') => {
    await act(async () => input.focus());
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, value);
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    await act(async () => {
      if (method === 'blur') input.blur();
      else
        input.dispatchEvent(
          new window.KeyboardEvent('keydown', {
            key: 'Enter',
            bubbles: true,
            cancelable: true,
          }),
        );
    });
  };

  try {
    await click(
      view.container.querySelector('.ov-file-browser__search-trigger'),
    );
    const modal = document.querySelector('.ov-file-browser-search');
    modal.style.zIndex = '2400';
    await click(document.querySelector('[aria-label="Advanced filters"]'));
    await click(document.querySelector('input[type="radio"]'));
    const start = field('Start date');
    const end = field('End date');
    await enterDate(start, date(15));

    await act(async () => end.focus());
    const popup = document.getElementById(end.getAttribute('aria-controls'));
    const overlay = popup.closest('.ov-date-picker__overlay');
    assert.equal(picker(end).contains(popup), false);
    assert.ok(overlay.isConnected);
    assert.ok(
      Number(window.getComputedStyle(overlay).zIndex) >
        Number(window.getComputedStyle(modal).zIndex),
    );
    const title = () =>
      popup.querySelector('.ov-date-picker__title').textContent;
    const originalTitle = title();
    await click(popup.querySelector('[aria-label="Previous year"]'));
    assert.match(title(), new RegExp(String(now.getFullYear() - 1)));
    assert.equal(end.value, '');
    await click(popup.querySelector('[aria-label="Next year"]'));
    assert.equal(title(), originalTitle);
    await click(popup.querySelector('[aria-label="Previous month"]'));
    assert.notEqual(title(), originalTitle);
    await click(popup.querySelector('[aria-label="Next month"]'));
    assert.equal(title(), originalTitle);
    assert.equal(day(end, 14).disabled, true);
    assert.equal(day(end, 15).disabled, false);
    await click(day(end, 14));
    assert.equal(end.value, '');
    await enterDate(end, date(14));
    assert.equal(end.value, '');
    await click(day(end, 15));
    assert.equal(end.value, date(15));

    await act(async () => start.focus());
    assert.equal(day(start, 16).disabled, true);
    await enterDate(start, date(16), 'blur');
    assert.equal(start.value, date(15));

    await click(picker(end).querySelector('.ov-date-picker__clear'));
    assert.equal(end.getAttribute('aria-controls'), null);
    await enterDate(start, date(16));
    assert.equal(start.value, date(16));
    assert.equal(end.value, '');
    const calendar = picker(start).querySelector('.ov-date-picker__calendar');
    await click(start);
    assert.notEqual(start.getAttribute('aria-controls'), null);
    await click(calendar);
    assert.equal(start.getAttribute('aria-controls'), null);
    await click(calendar);
    assert.notEqual(start.getAttribute('aria-controls'), null);
    await click(calendar);
    assert.equal(start.getAttribute('aria-controls'), null);
  } finally {
    await view.unmount();
  }
});
