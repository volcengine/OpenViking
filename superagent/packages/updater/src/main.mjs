#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createUpdaterServer } from './server.mjs';

const updater = await createUpdaterServer();
if (typeof process.send === 'function') process.send('ready');

async function stop() {
  await updater.close();
  process.exit(0);
}

process.once('SIGINT', stop);
process.once('SIGTERM', stop);
