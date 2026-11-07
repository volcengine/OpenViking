import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync, spawnSync } from 'node:child_process';

const launcher = fileURLToPath(new URL('../../../scripts/windows-pm2-launch.mjs', import.meta.url));
const pm2Module = new URL('../../updater/src/pm2.mjs', import.meta.url).href;

test('installed PM2 entry preserves argv, selects private home and propagates exit status without invoking real PM2', () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika PM2 中文 '));
  const release = path.join(home, '.vika', 'current');
  try {
    fs.mkdirSync(path.join(release, 'packages/updater/src'), { recursive: true });
    fs.mkdirSync(path.join(release, 'scripts'), { recursive: true });
    fs.writeFileSync(path.join(release, 'packages/updater/src/pm2.mjs'), `export { cleanPm2ControlEnv } from ${JSON.stringify(pm2Module)};`);
    fs.writeFileSync(path.join(release, 'scripts/pm2-windows.cjs'), `console.log(JSON.stringify({args:process.argv.slice(2),home:process.env.PM2_HOME,secret:process.env.UNRELATED_SECRET,node:process.execPath})); process.exitCode=process.argv.includes('fail')?7:0;`);
    const env = { ...process.env, USERPROFILE: home, HOME: home, UNRELATED_SECRET: 'must-not-pass' };
    delete env.PM2_HOME;
    const args = ['jlist', '空 格', '$literal', '"quoted"', ''];
    const result = JSON.parse(execFileSync(process.execPath, [launcher, ...args], { env, encoding: 'utf8', timeout: 10000 }));
    assert.deepEqual(result.args, args);
    assert.equal(result.home, path.join(home, '.vika', 'pm2'));
    assert.equal(result.secret, undefined);
    assert.equal(result.node, process.execPath);
    const custom = path.join(home, 'custom pm2');
    const failure = spawnSync(process.execPath, [launcher, 'fail'], { env: { ...env, PM2_HOME: custom }, encoding: 'utf8', timeout: 10000 });
    assert.equal(failure.status, 7);
    assert.equal(JSON.parse(failure.stdout).home, custom);
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
});
