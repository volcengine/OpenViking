#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Separate from the stable release/updater protocol. Run on Windows x64.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { assertSha256, checksumFor, download, fetchText, sha256File } from '../packages/updater/src/artifacts.mjs';

if (process.platform !== 'win32' || process.arch !== 'x64') {
  throw new Error('Build Windows previews on a Windows x64 runner');
}
if (process.argv.length !== 3) throw new Error('Usage: node scripts/build-windows-preview.mjs <output-directory>');
const source = fileURLToPath(new URL('..', import.meta.url));
const out = path.resolve(process.argv[2]);
if (fs.existsSync(out)) throw new Error(`Output already exists: ${out}`);
const run = (cmd, args, cwd = source) => execFileSync(cmd, args, { cwd, stdio: 'inherit' });
const git = (...args) => execFileSync('git', args, { cwd: source, encoding: 'utf8' }).trim();
const commit = git('rev-parse', 'HEAD');
const pkg = JSON.parse(git('show', 'HEAD:./package.json'));
const version = `${pkg.version}-windows-preview-${commit.slice(0, 12)}`;
const name = `superagent-${version}-win32-x64`;
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-win-build-'));
const root = path.join(work, name);
try {
  fs.mkdirSync(root);
  const sourceArchive = path.join(work, 'source.tar');
  run('git', ['archive', '--format=tar', `--output=${sourceArchive}`, 'HEAD']);
  run('tar', ['-xf', sourceArchive, '-C', root]);
  // Install using the same official Node/npm that will be shipped.
  const nodeName = `node-v${pkg.vika.node}-win-x64`;
  const nodeZip = `${nodeName}.zip`;
  const nodeBase = `https://nodejs.org/dist/v${pkg.vika.node}`;
  const nodeFile = path.join(work, nodeZip);
  await download(`${nodeBase}/${nodeZip}`, nodeFile);
  assertSha256(nodeFile, checksumFor(await fetchText(`${nodeBase}/SHASUMS256.txt`), nodeZip));
  run('tar', ['-xf', nodeFile, '-C', work]);
  fs.renameSync(path.join(work, nodeName), path.join(root, 'runtime'));
  const node = path.join(root, 'runtime', 'node.exe');
  run(node, [path.join(root, 'runtime', 'node_modules', 'npm', 'bin', 'npm-cli.js'),
    'ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund'], root);
  run(node, [path.join(root, 'scripts', 'install-lark-cli.mjs')], root);
  // npm workspace junctions point at the staging directory. Materialize only
  // these known links before ZIP creation, so the extracted tree is portable.
  for (const workspace of pkg.workspaces) {
    const workspaceDir = path.join(root, workspace);
    const workspacePkg = JSON.parse(fs.readFileSync(path.join(workspaceDir, 'package.json'), 'utf8'));
    const link = path.join(root, 'node_modules', workspacePkg.name);
    if (!fs.lstatSync(link).isSymbolicLink()) throw new Error(`Expected workspace junction: ${link}`);
    fs.unlinkSync(link);
    fs.cpSync(workspaceDir, link, { recursive: true });
  }
  fs.copyFileSync(path.join(root, 'scripts', 'windows', 'superagent-preview.ps1'), path.join(root, 'superagent-preview.ps1'));
  const metadata = { name: 'superagent', version, commit, platform: 'win32-x64', channel: 'windows-preview',
    node: pkg.vika.node, builtAt: new Date().toISOString() };
  fs.writeFileSync(path.join(root, 'windows-preview.json'), `${JSON.stringify(metadata, null, 2)}\n`);
  // Ship the repository license alongside this source-containing preview.
  fs.writeFileSync(path.join(root, 'LICENSE'), git('show', 'HEAD:LICENSE'));
  run('pwsh', ['-NoProfile', '-File', path.join(root, 'superagent-preview.ps1'), '-Action', 'check'], root);
  fs.mkdirSync(out, { recursive: true });
  const archive = path.join(out, `${name}.zip`);
  // Use .NET ZIP, which includes dotfiles and avoids Compress-Archive's hidden-file exclusions.
  run('pwsh', ['-NoProfile', '-File', path.join(root, 'scripts', 'windows', 'zip-preview.ps1'),
    '-Source', root, '-Destination', archive], root);
  const sha256 = sha256File(archive);
  fs.writeFileSync(`${archive}.sha256`, `${sha256}  ${path.basename(archive)}\n`);
  fs.writeFileSync(path.join(out, 'release.json'), `${JSON.stringify({ ...metadata,
    file: path.basename(archive), sha256, size: fs.statSync(archive).size }, null, 2)}\n`);
  const installer = fs.readFileSync(path.join(root, 'scripts', 'windows', 'install-preview.ps1'), 'utf8');
  const downloadBase = process.env.OV_SUPERAGENT_DOWNLOAD_BASE || 'https://ovrelease.tos-cn-beijing.volces.com/superagent';
  if (!/^https:\/\/[^'\r\n]+$/.test(downloadBase)) throw new Error('Invalid OV_SUPERAGENT_DOWNLOAD_BASE');
  // HTTP declares UTF-8; a BOM passed through irm becomes an iex token.
  const publishedInstaller = installer.replace(/^\uFEFF/, '').replaceAll('__OV_SUPERAGENT_DOWNLOAD_BASE__', downloadBase.replace(/\/$/, ''));
  for (const file of ['install.ps1', 'install-preview.ps1']) {
    // Keep a BOM for the offline file: Windows PowerShell 5.1 defaults to ANSI.
    fs.writeFileSync(path.join(out, file), (file === 'install-preview.ps1' ? '\uFEFF' : '') + publishedInstaller);
  }
  fs.copyFileSync(path.join(root, 'docs', 'windows-preview.md'), path.join(out, 'README.md'));
  console.log(archive);
} finally {
  fs.rmSync(work, { recursive: true, force: true });
}
