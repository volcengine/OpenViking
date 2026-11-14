import './browser-env.mjs';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
export const runtime = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'monitoring.js'))
);
export function setup(name = 'FileBrowser', config) {
  const events = [];
  const controller = new runtime.MonitoringController();
  controller.configure(config ?? { onEvent: (event) => events.push(event) });
  const frame = new runtime.MonitoringFrame(name, controller);
  frame.live = true;
  return { events, controller, frame };
}
export const ok = (result) => Response.json({ status: 'ok', result });
export const settle = async () => {
  for (let i = 0; i < 16; i++) await Promise.resolve();
};
export function streamBody(text) {
  const bytes = new TextEncoder().encode(text);
  return new ReadableStream({
    start(controller) {
      controller.enqueue(bytes);
      controller.close();
    },
  });
}
