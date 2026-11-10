// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

const host = process.env.VIKA_HOST || '127.0.0.1';
const port = process.env.VIKA_PORT || '3738';

try {
  const response = await fetch(`http://${host}:${port}/ready`, {
    signal: AbortSignal.timeout(3_000),
  });
  const body = await response.text();
  process.stdout.write(`${body}\n`);
  if (!response.ok) process.exitCode = 1;
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
}
