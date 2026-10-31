#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Builds the release archives the installer and updater download: one
// vika-<version>-<platform>.tar.gz per platform, each holding the committed
// code, its production dependencies, the pinned lark-cli and Slack CLI binaries and the
// official Node it runs on, plus a release manifest with their SHA-256s.
//
//   node scripts/build-release.mjs [--ref HEAD] [--platform linux-x64 ...|--all]
//                                  [--out dist] [--version X] [--download-base URL]
//
// Only committed files go in (git archive). A tag openviking-vika@X on the ref
// makes the version X; otherwise it is <package version>-<commit>. The download
// base is written into each release so its updater knows where newer ones live.

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  PLATFORMS, artifactName, assertSha256, assertVersion, checksumFor, download, fetchText, hostPlatform, platform, sha256File,
} from '../packages/updater/src/artifacts.mjs';
import { installLarkCliBinary } from './release/lark-cli.mjs';
import { installSlackCliBinary } from './release/slack-cli.mjs';

const REPO = fileURLToPath(new URL('..', import.meta.url));
const NODE_MIRROR = (process.env.VIKA_NODE_MIRROR || 'https://nodejs.org/dist').replace(/\/$/, '');

function options(argv) {
  const result = { ref: 'HEAD', platforms: [], out: path.join(REPO, 'dist'), version: null, downloadBase: process.env.VIKA_DOWNLOAD_BASE || '' };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    const value = () => {
      const next = argv[++index];
      if (!next) throw new Error(`${flag} needs a value`);
      return next;
    };
    if (flag === '--ref') result.ref = value();
    else if (flag === '--platform') result.platforms.push(value());
    else if (flag === '--all') result.platforms.push(...Object.keys(PLATFORMS));
    else if (flag === '--out') result.out = path.resolve(value());
    else if (flag === '--version') result.version = value();
    else if (flag === '--download-base') result.downloadBase = value();
    else throw new Error(`unknown option ${flag}`);
  }
  if (result.platforms.length === 0) result.platforms.push(hostPlatform());
  result.platforms = [...new Set(result.platforms)];
  result.platforms.forEach(platform);
  return result;
}

const git = (...args) => execFileSync('git', args, { cwd: REPO, encoding: 'utf8' }).trim();
const say = (line) => process.stderr.write(`${line}\n`);

function releaseVersion(ref, explicit) {
  if (explicit) return assertVersion(explicit);
  const tag = git('tag', '--points-at', ref).split('\n').find((name) => /^openviking-vika@/.test(name));
  if (tag) return assertVersion(tag.slice('openviking-vika@'.length));
  const { version } = JSON.parse(git('show', `${ref}:./package.json`));
  return assertVersion(`${version}-${git('rev-parse', '--short=12', ref)}`);
}

// The committed tree with production dependencies, shared by every platform.
function stageApp(ref, stage) {
  fs.mkdirSync(stage, { recursive: true });
  const archive = `${stage}.tar`;
  execFileSync('git', ['archive', '--format=tar', `--output=${archive}`, ref], { cwd: REPO });
  execFileSync('tar', ['-xf', archive, '-C', stage]);
  fs.rmSync(archive);
  say('installing production dependencies ...');
  execFileSync('npm', ['ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund'], { cwd: stage, stdio: ['ignore', 'ignore', 'inherit'] });
}

async function nodeBinary(version, platformId, cache) {
  const name = `node-v${version}-${platform(platformId).node}`;
  const file = path.join(cache, `${name}.tar.gz`);
  const sums = await fetchText(`${NODE_MIRROR}/v${version}/SHASUMS256.txt`);
  const expected = checksumFor(sums, `${name}.tar.gz`);
  if (!fs.existsSync(file) || sha256File(file) !== expected) {
    say(`downloading ${name} ...`);
    await download(`${NODE_MIRROR}/v${version}/${name}.tar.gz`, file);
  }
  assertSha256(file, expected);
  return { file, name };
}

async function buildPlatform({ stage, platformId, version, commit, nodeVersion, downloadBase, out, cache }) {
  const directory = `vika-${version}-${platformId}`;
  const root = path.join(out, 'work', directory);
  fs.rmSync(root, { recursive: true, force: true });
  fs.cpSync(stage, root, { recursive: true, verbatimSymlinks: true });

  const node = await nodeBinary(nodeVersion, platformId, cache);
  const unpacked = path.join(out, 'work', `.node-${platformId}`);
  fs.rmSync(unpacked, { recursive: true, force: true });
  fs.mkdirSync(unpacked, { recursive: true });
  execFileSync('tar', ['-xzf', node.file, '-C', unpacked, `${node.name}/bin/node`, `${node.name}/LICENSE`]);
  fs.mkdirSync(path.join(root, 'node', 'bin'), { recursive: true });
  fs.renameSync(path.join(unpacked, node.name, 'bin', 'node'), path.join(root, 'node', 'bin', 'node'));
  fs.renameSync(path.join(unpacked, node.name, 'LICENSE'), path.join(root, 'node', 'LICENSE'));
  fs.rmSync(unpacked, { recursive: true, force: true });

  say(`fetching lark-cli for ${platformId} ...`);
  await installLarkCliBinary({ packageDir: path.join(root, 'node_modules', '@larksuite', 'cli'), platformId });
  const slack = await installSlackCliBinary({ root, platformId });

  const metadata = {
    name: 'vika', version, commit, platform: platformId, node: nodeVersion, slackCli: slack.version,
    builtAt: new Date().toISOString(), ...(downloadBase ? { downloadBase: downloadBase.replace(/\/+$/, '') } : {}),
  };
  fs.writeFileSync(path.join(root, 'release.json'), `${JSON.stringify(metadata, null, 2)}\n`);

  if (platformId === hostPlatform()) {
    // Only a release for this machine can be started here; CI checks the rest on their own runners.
    const run = (command, args) => execFileSync(command, args, { cwd: root, stdio: ['ignore', 'pipe', 'inherit'], encoding: 'utf8' }).trim();
    say(`smoke test: ${run(path.join(root, 'node', 'bin', 'node'), ['--version'])}, `
      + `${run(path.join(root, 'node', 'bin', 'node'), [path.join(root, 'packages', 'cli', 'bin', 'vika.mjs'), '--version'])}, `
      + `${run(path.join(root, 'node_modules', '@larksuite', 'cli', 'bin', 'lark-cli'), ['--version'])}`);
    run(path.join(root, 'node', 'bin', 'node'), [path.join(root, 'packages', 'daemon', 'scripts', 'check-pi.mjs')]);
    run(path.join(root, 'node', 'bin', 'node'), [path.join(root, 'packages', 'daemon', 'scripts', 'check-durable.mjs')]);
    run(slack.bin, ['version', '--skip-update', '--no-color']);
    say(run(path.join(root, 'node', 'bin', 'node'), [path.join(root, 'packages', 'daemon', 'scripts', 'check-agent-clis.mjs')]));
  }

  const name = artifactName(version, platformId);
  const archive = path.join(out, name);
  execFileSync('tar', ['-czf', archive, '-C', path.join(out, 'work'), directory]);
  const sha256 = sha256File(archive);
  fs.writeFileSync(`${archive}.sha256`, `${sha256}  ${name}\n`);
  fs.rmSync(root, { recursive: true, force: true });
  return { name, sha256, size: fs.statSync(archive).size };
}

const opts = options(process.argv.slice(2));
const version = releaseVersion(opts.ref, opts.version);
const commit = git('rev-parse', opts.ref);
const nodeVersion = JSON.parse(git('show', `${opts.ref}:./package.json`)).vika.node;
fs.mkdirSync(opts.out, { recursive: true });
const cache = path.join(os.homedir(), '.cache', 'vika-build');
fs.mkdirSync(cache, { recursive: true });
const stage = path.join(opts.out, 'work', '.stage');
fs.rmSync(stage, { recursive: true, force: true });
say(`building vika ${version} (${commit.slice(0, 12)}) for ${opts.platforms.join(', ')}; Node ${nodeVersion}`);
stageApp(opts.ref, stage);

const files = {};
for (const platformId of opts.platforms) {
  files[platformId] = await buildPlatform({ stage, platformId, version, commit, nodeVersion, downloadBase: opts.downloadBase, out: opts.out, cache });
  say(`${files[platformId].name}  ${(files[platformId].size / 1024 / 1024).toFixed(1)} MB  ${files[platformId].sha256}`);
}
fs.rmSync(path.join(opts.out, 'work'), { recursive: true, force: true });
const manifest = { name: 'vika', version, commit, node: nodeVersion, builtAt: new Date().toISOString(), files };
fs.writeFileSync(path.join(opts.out, 'release.json'), `${JSON.stringify(manifest, null, 2)}\n`);
process.stdout.write(`${path.join(opts.out, 'release.json')}\n`);
