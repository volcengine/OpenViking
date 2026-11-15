#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0


import { startLogRotation } from './log-rotation.mjs';
import { createUpdaterServer } from './server.mjs';

const updater = await createUpdaterServer();
// The updater outlives every other app, so it keeps the shared PM2 logs bounded.
const stopLogRotation = startLogRotation();
if (typeof process.send === 'function') process.send('ready');

async function stop() {
  stopLogRotation();
  await updater.close();
  process.exit(0);
}

process.once('SIGINT', stop);
process.once('SIGTERM', stop);
