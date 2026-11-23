import { spawn, spawnSync } from 'node:child_process';

// Keep the watchdog outside the test event loops: synchronous assertion error
// formatting can block their timers. Test workers inherit the V8 heap limit.
export function startTests(
  args,
  { cwd, env, timeout = 300_000, stdio = 'inherit' } = {},
) {
  const child = spawn(
    process.execPath,
    [
      '--max-old-space-size=512',
      '--max-semi-space-size=16',
      '--test',
      '--test-concurrency=4',
      ...args,
    ],
    { cwd, env, stdio, detached: process.platform !== 'win32' },
  );
  let stopped = false;
  const stop = () => {
    if (stopped || !child.pid) return;
    stopped = true;
    if (process.platform === 'win32') {
      const result = spawnSync(
        'taskkill',
        ['/pid', String(child.pid), '/T', '/F'],
        {
          stdio: 'ignore',
          timeout: 5000,
        },
      );
      if (result.error || result.status !== 0) child.kill('SIGKILL');
    } else {
      try {
        process.kill(-child.pid, 'SIGKILL');
      } catch (error) {
        if (error.code !== 'ESRCH') throw error;
      }
    }
  };
  let forcedCode;
  const interrupt = (code) => {
    forcedCode ??= code;
    stop();
  };
  const onInterrupt = () => interrupt(130);
  const onTerminate = () => interrupt(143);
  process.once('SIGINT', onInterrupt);
  process.once('SIGTERM', onTerminate);
  process.once('exit', stop);
  const timer = setTimeout(() => {
    console.error(
      `Node tests exceeded the ${timeout}ms wall-clock limit; stopping the test process tree.`,
    );
    interrupt(124);
  }, timeout);
  const completed = new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timer);
      stop();
      process.off('SIGINT', onInterrupt);
      process.off('SIGTERM', onTerminate);
      process.off('exit', stop);
    };
    child.once('error', (error) => {
      cleanup();
      reject(error);
    });
    child.once('exit', stop);
    child.once('close', (code) => {
      cleanup();
      resolve(forcedCode ?? code ?? 1);
    });
  });
  return { child, completed };
}
