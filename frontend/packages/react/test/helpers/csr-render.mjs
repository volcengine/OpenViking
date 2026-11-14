import './browser-env.mjs';
import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';

/** Serialize the committed DOM for markup assertions, including normal effects. */
export function renderMarkup(element) {
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  try {
    flushSync(() => root.render(element));
    return container.innerHTML;
  } finally {
    flushSync(() => root.unmount());
    container.remove();
  }
}
