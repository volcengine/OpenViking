// Compatibility entry for the Windows source install's private PM2 daemon.
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';

const home = process.env.USERPROFILE || os.homedir();
const release = path.join(home, '.vika', 'current');
const { cleanPm2ControlEnv } = await import(pathToFileURL(path.join(release, 'packages', 'updater', 'src', 'pm2.mjs')).href);
const env = cleanPm2ControlEnv({
  ...process.env,
  HOME: process.env.HOME || home,
  PM2_HOME: process.env.PM2_HOME || path.join(process.env.HOME || home, '.vika', 'pm2'),
});
// Never call node_modules/.bin/pm2: it bypasses our per-home Windows pipes.
const child = spawn(process.execPath, [path.join(release, 'scripts', 'pm2-windows.cjs'), ...process.argv.slice(2)], {
  env, stdio: 'inherit', windowsHide: true,
});
child.once('error', error => { console.error(`Vika PM2: ${error.message}`); process.exitCode = 1; });
child.once('exit', code => { process.exitCode = code ?? 1; });
