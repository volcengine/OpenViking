// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// Exercise real HTTP downloads and irm | iex in both Windows shell versions.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
const shell = process.env.POWERSHELL_EXE || 'pwsh';
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-online-test-'));
const quote = value => `'${value.replaceAll("'", "''")}'`;
// Match the published HTTP entry: UTF-8 without BOM (offline .ps1 keeps BOM).
const installer = fs.readFileSync(fileURLToPath(new URL('install-preview.ps1', import.meta.url)), 'utf8').replace(/^\uFEFF/, '');
const version = '0.4.0-windows-preview-online';
const name = `vika-${version}-win32-x64.zip`;
const root = path.join(work, 'fixture');
fs.mkdirSync(root);
fs.writeFileSync(path.join(root, 'windows-preview.json'), JSON.stringify({ version, platform: 'win32-x64', channel: 'windows-preview' }));
fs.writeFileSync(path.join(root, 'vika-preview.ps1'), 'param($Action)\n');
fs.mkdirSync(path.join(root, 'runtime'));
fs.writeFileSync(path.join(root, 'runtime/node.exe'), '');
function run(code, env = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(shell, ['-NoProfile', '-EncodedCommand', Buffer.from(code, 'utf16le').toString('base64')], {
      env: { ...process.env, VIKA_LANG: 'en', NO_COLOR: '1', VIKA_VERSION: '', ...env },
    });
    let output = '';
    child.stdout.on('data', data => { output += data; });
    child.stderr.on('data', data => { output += data; });
    child.on('error', reject);
    child.on('exit', code => resolve({ code, output }));
  });
}
let server;
try {
  const zipped = await run(`Add-Type -AssemblyName System.IO.Compression.FileSystem; [IO.Compression.ZipFile]::CreateFromDirectory(${quote(root)}, ${quote(path.join(work, name))})`);
  assert.equal(zipped.code, 0, zipped.output);
  const zip = fs.readFileSync(path.join(work, name));
  const hash = createHash('sha256').update(zip).digest('hex');
  let mode = 'ok';
  const requests = [];
  server = http.createServer((req, res) => {
    requests.push(req.url);
    res.setHeader('Content-Type', 'text/plain; charset=utf-8');
    if (req.url === '/install.ps1') return res.end(installer);
    const servedVersion = mode === 'mismatch' ? '0.4.0-windows-preview-other' : version;
    const servedName = `vika-${servedVersion}-win32-x64.zip`;
    if (req.url === '/windows-preview/latest/version') return res.end(mode === 'unsafe' ? '../escape' : servedVersion);
    if (req.url === `/windows-preview/${servedVersion}/${servedName}.sha256`) {
      if (mode === 'missing') { res.statusCode = 404; return res.end('missing'); }
      return res.end(`${mode === 'corrupt' ? '0'.repeat(64) : hash}  ${servedName}\n`);
    }
    if (req.url === `/windows-preview/${servedVersion}/${servedName}`) return res.end(zip);
    res.statusCode = 404; res.end('not found');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  for (const scenario of ['ok', 'pinned', 'missing', 'corrupt', 'unsafe', 'mismatch']) {
    mode = scenario;
    requests.length = 0;
    const home = path.join(work, scenario);
    fs.mkdirSync(home);
    const command = scenario === 'pinned'
      ? `& ([scriptblock]::Create((irm ${quote(`${base}/install.ps1`)}))) -Version ${quote(version)} -NoSetup`
      : `irm ${quote(`${base}/install.ps1`)} | iex`;
    const result = await run(`$ErrorActionPreference = 'Stop'; try { ${command}; 'INSTALL-RETURNED' } catch { Write-Output $_; exit 1 }`, {
      VIKA_DOWNLOAD_BASE: base, VIKA_HOME: home, TMPDIR: home, TMP: home, TEMP: home,
      VIKA_VERSION: '',
    });
    if (['ok', 'pinned'].includes(scenario)) {
      assert.equal(result.code, 0, result.output);
      assert.match(result.output, /INSTALL-RETURNED/);
      assert.ok(fs.existsSync(path.join(home, 'releases', version, 'windows-preview.json')));
      assert.ok(fs.existsSync(path.join(home, 'bin/vika.cmd')));
      if (scenario === 'pinned') assert.ok(!requests.includes('/windows-preview/latest/version'));
    } else {
      assert.notEqual(result.code, 0, result.output);
      assert.ok(!fs.existsSync(path.join(home, 'current')));
      assert.match(result.output, scenario === 'unsafe' ? /Invalid release version/ : scenario === 'corrupt' ? /SHA-256 mismatch/ : scenario === 'mismatch' ? /does not match requested version/ : /missing|404/);
      if (scenario === 'missing' || scenario === 'unsafe') assert.ok(!requests.some(url => url.endsWith('.zip')));
    }
    assert.ok(!fs.existsSync(path.join(home, 'deploy.lock')));
    assert.ok(!fs.readdirSync(home).some(name => name.startsWith('vika-download-')));
    console.log(`Online installer: ${scenario} passed (${shell})`);
  }
} finally {
  if (server) await new Promise(resolve => server.close(resolve));
  fs.rmSync(work, { recursive: true, force: true });
}
