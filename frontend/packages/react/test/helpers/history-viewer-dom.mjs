import { registerHooks } from 'node:module';
import { createElement, useEffect, useState } from 'react';

// Keep workflow tests focused on the query/selection/clipboard boundary. The real
// browser verifies FileViewer, whose browser-only imports cannot execute in Node.
registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith('@ark-design/ui/file-viewer/'))
      return { url: import.meta.url, shortCircuit: true };
    return next(specifier, context);
  },
});
export const diff = () => ({});
export const markdown = () => ({});
export const code = () => ({});
export function FileViewerCore({ source }) {
  const [content, setContent] = useState('');
  useEffect(() => {
    let active = true;
    void source.file.text().then((value) => {
      if (active) setContent(value);
    });
    return () => {
      active = false;
    };
  }, [source]);
  return createElement(
    'pre',
    { 'data-testid': 'history-viewer-source' },
    content,
  );
}
