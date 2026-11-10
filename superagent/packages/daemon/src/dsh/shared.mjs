// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The two pieces every dsh/ module needs and none of them owns: the helper that
// installs a module's methods onto the agent prototype, and the numeric helpers
// several responsibilities share. It deliberately holds no agent state and
// imports nothing from the daemon, so `dsh.mjs -> dsh/*` stays the only
// direction of the dependency graph.

export function delay(ms) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    timer.unref?.();
  });
}

export function number(value) {
  return Number.isFinite(Number(value)) ? Number(value) : 0;
}

// Install each module's methods with the property attributes a class body would
// have produced (non-enumerable, writable, configurable): the split moves where a
// method is written, it must not change what the prototype looks like. A name
// claimed twice would silently shadow another module, so it fails loudly instead.
export function installMembers(target, ...sources) {
  for (const source of sources) {
    for (const [name, value] of Object.entries(source)) {
      if (Object.hasOwn(target, name)) throw new Error(`dsh member defined twice: ${name}`);
      Object.defineProperty(target, name, {
        value, writable: true, configurable: true, enumerable: false,
      });
    }
  }
  return target;
}
