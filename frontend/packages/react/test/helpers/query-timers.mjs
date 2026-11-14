import { timeoutManager } from '@tanstack/query-core';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
const fromSDK = createRequire(new URL('../../package.json', import.meta.url));
const corePath = dirname(fromSDK.resolve('@tanstack/query-core/package.json'));
const sdkCore = await import(
  pathToFileURL(join(corePath, 'build/modern/index.js'))
);

// Inactive browser caches live for five minutes. Keep their timers from holding
// Node's test workers open, without changing query policies or retry timers.
for (const manager of new Set([timeoutManager, sdkCore.timeoutManager]))
  manager.setTimeoutProvider({
    setTimeout(callback, delay) {
      const timer = setTimeout(callback, delay);
      if (delay >= 300_000) timer.unref();
      return timer;
    },
    clearTimeout,
    setInterval,
    clearInterval,
  });
