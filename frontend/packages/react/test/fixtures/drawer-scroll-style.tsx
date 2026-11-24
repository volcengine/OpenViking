/**
 * Real-browser regression entry. Bundle with the React Rsbuild plugin and run it
 * in Chrome; jsdom does not reproduce overflow shorthand/longhand serialization.
 * Exercises the published component and stylesheet, with no source aliases.
 */
/// <reference types="@rsbuild/core/types" />
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { FileBrowserActionBar } from '@openviking/react/components/file-browser-action-bar';
import '@openviking/react/components/file-browser-action-bar/style.css';

const cases = [
  'overflow-y: hidden;',
  'overflow-x: hidden;',
  'overflow-y: hidden !important;',
  'overflow-x: hidden !important;',
  'overflow: auto hidden !important; padding-right: 13px !important;',
  'overflow-x: scroll !important; overflow-y: hidden;',
  'overflow-x: hidden; overflow-y: scroll !important; padding-right: 7px !important;',
  'overflow: scroll !important; padding-right: 7px !important;',
];
const properties = ['overflow-x', 'overflow-y', 'padding-right'];
const snapshot = () =>
  properties.map((property) => [
    property,
    document.body.style.getPropertyValue(property),
    document.body.style.getPropertyPriority(property),
  ]);
const frame = () =>
  new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
async function until(check: () => boolean) {
  const deadline = performance.now() + 3000;
  while (!check()) {
    if (performance.now() > deadline)
      throw new Error('Drawer transition timed out');
    await frame();
  }
  await frame();
  await frame();
}

interface Result {
  style: string;
  release: string;
  restored: string;
  passed: boolean;
}
function Regression() {
  const [results, setResults] = useState<Result[]>([]);
  const [status, setStatus] = useState('Ready');
  async function run() {
    setResults([]);
    setStatus('Running');
    const original = document.body.style.cssText;
    try {
      for (const style of cases) {
        for (const release of ['close', 'unmount']) {
          document.body.style.cssText = style;
          const before = snapshot();
          const container = document.createElement('div');
          document.body.append(container);
          const root = createRoot(container);
          let mounted = true;
          try {
            flushSync(() =>
              root.render(
                <FileBrowserActionBar
                  locale="en-US"
                  agentConnect={{ apiKey: 'demo-key', locale: 'en-US' }}
                />,
              ),
            );
            flushSync(() => container.querySelector('button')!.click());
            await until(() =>
              Boolean(document.querySelector('[role="dialog"]')),
            );
            if (release === 'unmount') {
              flushSync(() => root.unmount());
              mounted = false;
            } else {
              flushSync(() =>
                document
                  .querySelector<HTMLButtonElement>('.ov-drawer__close')!
                  .click(),
              );
            }
            await until(() => !document.querySelector('[role="dialog"]'));
            const passed =
              JSON.stringify(snapshot()) === JSON.stringify(before);
            const restored = document.body.style.cssText;
            setResults((previous) => [
              ...previous,
              { style, release, restored, passed },
            ]);
            if (!passed)
              throw new Error(`Host style lost after ${release}: ${style}`);
          } finally {
            if (mounted) flushSync(() => root.unmount());
            container.remove();
          }
        }
      }
      setStatus(`PASS ${cases.length * 2}/${cases.length * 2}`);
    } catch (error) {
      setStatus(`FAIL: ${String(error)}`);
    } finally {
      document.body.style.cssText = original;
    }
  }
  return (
    <main style={{ fontFamily: 'system-ui', padding: 24 }}>
      <h1>Drawer overflow restoration</h1>
      <button disabled={status === 'Running'} onClick={run}>
        Run browser regression
      </button>
      <p role="status">{status}</p>
      <table>
        <thead>
          <tr>
            <th>Host styles</th>
            <th>Release</th>
            <th>Restored styles</th>
            <th>Result</th>
          </tr>
        </thead>
        <tbody>
          {results.map((result) => (
            <tr key={`${result.style}-${result.release}`}>
              <td>{result.style}</td>
              <td>{result.release}</td>
              <td>{result.restored || '(empty)'}</td>
              <td>{result.passed ? 'PASS' : 'FAIL'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
createRoot(document.getElementById('root')!).render(<Regression />);
