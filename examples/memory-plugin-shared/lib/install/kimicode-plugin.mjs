#!/usr/bin/env node

import { cp, mkdir, mkdtemp, readFile, rename, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";

const PLUGIN_ID = "openviking-memory";
const LOCK_NAME = ".openviking-memory.lock";
const LOCK_WAIT_MS = 30_000;
const LOCK_RETRY_MS = 25;

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function withPluginLock(kimiHome, operation) {
  const pluginsDir = join(kimiHome, "plugins");
  const lockDir = join(pluginsDir, LOCK_NAME);
  await mkdir(pluginsDir, { recursive: true });
  const deadline = Date.now() + LOCK_WAIT_MS;
  while (true) {
    let created = false;
    try {
      await mkdir(lockDir);
      created = true;
      await writeFile(join(lockDir, "owner"), `${process.pid}\n`, "utf8");
      break;
    } catch (error) {
      if (created) await rm(lockDir, { recursive: true, force: true });
      if (error.code !== "EEXIST") throw error;
      if (Date.now() >= deadline) {
        throw new Error(`timed out waiting for Kimi plugin lock: ${lockDir}`);
      }
      await delay(LOCK_RETRY_MS);
    }
  }
  try {
    return await operation();
  } finally {
    await rm(lockDir, { recursive: true, force: true });
  }
}

async function readJson(file) {
  return JSON.parse(await readFile(file, "utf8"));
}

async function readInstalled(kimiHome) {
  const file = join(kimiHome, "plugins", "installed.json");
  if (!existsSync(file)) return { version: 1, plugins: [] };
  const data = await readJson(file);
  if (!data || !Array.isArray(data.plugins)) {
    throw new Error(`invalid Kimi plugin registry: ${file}`);
  }
  return data;
}

async function writeInstalled(kimiHome, data) {
  const dir = join(kimiHome, "plugins");
  await mkdir(dir, { recursive: true });
  const file = join(dir, "installed.json");
  const tmp = `${file}.tmp-${process.pid}`;
  await writeFile(tmp, `${JSON.stringify(data, null, 2)}\n`, "utf8");
  await rename(tmp, file);
}

async function readManifest(sourceRoot) {
  const manifestPath = join(sourceRoot, "kimi.plugin.json");
  const manifest = await readJson(manifestPath);
  if (manifest.name !== PLUGIN_ID) {
    throw new Error(`expected plugin name ${PLUGIN_ID}, got ${String(manifest.name)}`);
  }
  return manifest;
}

async function install(kimiHome, source) {
  const sourceRoot = resolve(source);
  await readManifest(sourceRoot);
  return withPluginLock(kimiHome, async () => {
    const managedRoot = join(kimiHome, "plugins", "managed", PLUGIN_ID);
    const managedDir = join(kimiHome, "plugins", "managed");
    await mkdir(managedDir, { recursive: true });

    const registry = await readInstalled(kimiHome);
    const existing = registry.plugins.find((plugin) => plugin.id === PLUGIN_ID);
    const stagingRoot = await mkdtemp(join(managedDir, `${PLUGIN_ID}-`));
    const previousRoot = `${stagingRoot}-previous`;
    let previousMoved = false;
    try {
      await cp(sourceRoot, stagingRoot, { recursive: true });
      try {
        await rename(managedRoot, previousRoot);
        previousMoved = true;
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
      await rename(stagingRoot, managedRoot);
      const now = new Date().toISOString();
      const record = {
        ...(existing || {}),
        id: PLUGIN_ID,
        root: managedRoot,
        source: "local-path",
        originalSource: sourceRoot,
        enabled: existing?.enabled ?? true,
        installedAt: existing?.installedAt || now,
        updatedAt: now,
      };
      const plugins = registry.plugins.filter((plugin) => plugin.id !== PLUGIN_ID);
      await writeInstalled(kimiHome, { version: 1, plugins: [...plugins, record] });
      if (previousMoved) await rm(previousRoot, { recursive: true, force: true });
    } catch (error) {
      await rm(managedRoot, { recursive: true, force: true });
      if (previousMoved) await rename(previousRoot, managedRoot);
      else await rm(stagingRoot, { recursive: true, force: true });
      throw error;
    }
  });
}

async function remove(kimiHome, { purge = false } = {}) {
  return withPluginLock(kimiHome, async () => {
    const registry = await readInstalled(kimiHome);
    const record = registry.plugins.find((plugin) => plugin.id === PLUGIN_ID);
    if (record) {
      await writeInstalled(kimiHome, {
        version: 1,
        plugins: registry.plugins.filter((plugin) => plugin.id !== PLUGIN_ID),
      });
    }
    if (purge) {
      await rm(join(kimiHome, "plugins", "managed", PLUGIN_ID), { recursive: true, force: true });
    }
  });
}

async function verify(kimiHome) {
  const registry = await readInstalled(kimiHome);
  const record = registry.plugins.find((plugin) => plugin.id === PLUGIN_ID);
  if (!record || record.root !== join(kimiHome, "plugins", "managed", PLUGIN_ID)) return false;
  return existsSync(join(record.root, "kimi.plugin.json"));
}

async function main(argv) {
  const [action, kimiHomeArg, source] = argv;
  if (!action || !kimiHomeArg) throw new Error("usage: kimicode-plugin.mjs <install|remove|verify> <kimi-home> [source]");
  const kimiHome = resolve(kimiHomeArg);
  if (action === "install") {
    if (!source) throw new Error("install requires a plugin source directory");
    await install(kimiHome, source);
    return;
  }
  if (action === "remove") {
    await remove(kimiHome, { purge: argv.includes("--purge") });
    return;
  }
  if (action === "verify") {
    if (!(await verify(kimiHome))) process.exitCode = 1;
    return;
  }
  throw new Error(`unknown action: ${action}`);
}

main(process.argv.slice(2)).catch((error) => {
  console.error(error.message || error);
  process.exitCode = 1;
});
