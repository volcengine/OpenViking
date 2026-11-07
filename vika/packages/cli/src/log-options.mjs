// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
export function parseLogOptions(args) {
  let target;
  let lines = 100;
  let follow = true;
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--no-follow' || arg === '--nostream') follow = false;
    else if (arg === '--lines') {
      const value = args[++i];
      if (!/^\d+$/.test(value ?? '') || !Number.isSafeInteger(Number(value))) throw new Error('--lines requires a non-negative integer');
      lines = Number(value);
    } else if (arg.startsWith('-') || target !== undefined) throw new Error(`Unexpected logs argument: ${arg}`);
    else target = arg;
  }
  return { target, lines, follow };
}
