import { act, createElement as h } from 'react';
import { useNotification } from './internal-hooks.mjs';

export function Host({ capture, locale, labels }) {
  const [api, holder] = useNotification({ locale, labels });
  capture(api);
  return h('div', null, h('button', null, 'Trigger'), holder);
}
export const cards = () => [...document.querySelectorAll('.ov-notification')];
export const wait = (ms) =>
  act(() => new Promise((resolve) => setTimeout(resolve, ms)));
export const close = async (card) => {
  await act(() => card.querySelector('.ov-notification__close').click());
  await wait(240);
};
