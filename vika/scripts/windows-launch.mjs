// Native Windows bootstrap, also used by the per-user login startup entry.
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const home = process.env.USERPROFILE || os.homedir();
process.env.HOME ||= home;
process.env.PM2_HOME ||= path.join(home, '.vika', 'pm2');
const pathKey = Object.keys(process.env).find(key => key.toUpperCase() === 'PATH');
const inherited = process.env[pathKey] || '';
if (pathKey && pathKey !== 'PATH') delete process.env[pathKey];
process.env.PATH = [path.dirname(process.execPath), path.join(home, '.vika', 'bin'), inherited].join(path.delimiter);
await import(pathToFileURL(path.join(home, '.vika', 'current', 'packages', 'cli', 'bin', 'vika.mjs')).href);
