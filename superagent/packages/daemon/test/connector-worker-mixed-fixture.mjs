// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The real Lark worker, and the scripted fixture for every other Connector, so
// a daemon test can run Lark next to a second Connector it controls.
if (process.argv[2] === 'lark') await import('../src/connectors/worker.mjs');
else await import('./connector-worker-fixture.mjs');
