// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
'use strict';

// PM2 6.0.14 overwrites its configured Windows RPC/PUB paths with global pipe
// names, even when PM2_HOME differs. Adapt the pinned dependency in memory in
// both the CLI and its daemon; never modify the installed dependency files.
const path = require('node:path');
const { createHash } = require('node:crypto');

function pipePaths(home) {
  const identity = path.win32.resolve(home).toLowerCase();
  const digest = createHash('sha256').update(identity).digest('hex').slice(0, 32);
  return Object.fromEntries([
    ['DAEMON_RPC_PORT', 'rpc'], ['DAEMON_PUB_PORT', 'pub'], ['INTERACTOR_RPC_PORT', 'interactor'],
  ].map(([key, name]) => [key, `\\\\.\\pipe\\vika-pm2-${name}-${digest}`]));
}
module.exports = { pipePaths };

if (process.platform === 'win32') {
  const version = require('pm2/package.json').version;
  if (version !== '6.0.14') throw new Error(`Revalidate the Windows PM2 adapter before using PM2 ${version}`);
  const pathsFile = require.resolve('pm2/paths.js');
  const originalPaths = require(pathsFile);
  require.cache[pathsFile].exports = function managedPaths(home) {
    const result = originalPaths(home);
    return { ...result, ...pipePaths(result.PM2_HOME) };
  };

  // Node receives a real argv array, so a Unicode/spaced home needs no quoting.
  // Only PM2's daemon gets this preload; managed apps and model runtimes do not.
  const childProcess = require('node:child_process');
  const originalSpawn = childProcess.spawn;
  const daemonFile = require.resolve('pm2/lib/Daemon.js');
  childProcess.spawn = function spawn(command, args, options) {
    if (Array.isArray(args) && args.includes(daemonFile)) args = ['--require', __filename, ...args];
    return originalSpawn.call(this, command, args, options);
  };
}

if (require.main === module) require('pm2/lib/binaries/CLI.js');
