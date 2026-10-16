#!/usr/bin/env node

import { existsSync } from "node:fs";
import { cp, mkdir, mkdtemp, readFile, rename, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

const PLUGIN_ID = "openviking-memory";
async function readJson(file) {
  return JSON.parse(await readFile(file, "utf8"));
}

async function readRegistry(kimiHome) {
  const file = join(kimiHome, "plugins", "installed.json");
  if (!existsSync(file)) return { version: 1, plugins: [] };
  const value = await readJson(file);
  if (!value || !Array.isArray(value.plugins)) {
    throw new Error(`invalid Kimi plugin registry: ${file}`);
  }
  return value;
}

async function writeRegistry(kimiHome, value) {
  const pluginsDir = join(kimiHome, "plugins");
  await mkdir(pluginsDir, { recursive: true });
  const file = join(pluginsDir, "installed.json");
  const temporary = `${file}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await rename(temporary, file);
}

async function install(kimiHome, source) {
  const sourceRoot = resolve(source);
  const manifest = await readJson(join(sourceRoot, "kimi.plugin.json"));
  if (manifest.name !== PLUGIN_ID) {
    throw new Error(`expected plugin name ${PLUGIN_ID}, got ${String(manifest.name)}`);
  }

  const managedDir = join(kimiHome, "plugins", "managed");
  const managedRoot = join(managedDir, PLUGIN_ID);
  await mkdir(managedDir, { recursive: true });
  const registry = await readRegistry(kimiHome);
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
      enabled: existing?.enabled ?? true,
      installedAt: existing?.installedAt || now,
      updatedAt: now,
    };
    // The unified installer deletes its staging bundle after this helper exits.
    // Do not preserve a stale originalSource from this or an earlier install.
    delete record.originalSource;
    await writeRegistry(kimiHome, {
      ...registry,
      version: registry.version || 1,
      plugins: [...registry.plugins.filter((plugin) => plugin.id !== PLUGIN_ID), record],
    });
  } catch (error) {
    await rm(managedRoot, { recursive: true, force: true });
    if (previousMoved) await rename(previousRoot, managedRoot);
    else await rm(stagingRoot, { recursive: true, force: true });
    throw error;
  }
  if (previousMoved) await rm(previousRoot, { recursive: true, force: true }).catch(() => {});
}

async function remove(kimiHome) {
  const registry = await readRegistry(kimiHome);
  await writeRegistry(kimiHome, {
    ...registry,
    version: registry.version || 1,
    plugins: registry.plugins.filter((plugin) => plugin.id !== PLUGIN_ID),
  });
  await rm(join(kimiHome, "plugins", "managed", PLUGIN_ID), { recursive: true, force: true });
}

async function verify(kimiHome) {
  const registry = await readRegistry(kimiHome);
  const root = join(kimiHome, "plugins", "managed", PLUGIN_ID);
  return registry.plugins.some((plugin) => plugin.id === PLUGIN_ID && plugin.root === root)
    && existsSync(join(root, "kimi.plugin.json"));
}

async function main(argv) {
  const [action, kimiHomeArg, source] = argv;
  if (!action || !kimiHomeArg) {
    throw new Error("usage: kimicode-plugin.mjs <install|remove|verify> <kimi-home> [source]");
  }
  const kimiHome = resolve(kimiHomeArg);
  if (action === "install") {
    if (!source) throw new Error("install requires a plugin source directory");
    await install(kimiHome, source);
  } else if (action === "remove") {
    await remove(kimiHome);
  } else if (action === "verify") {
    if (!(await verify(kimiHome))) process.exitCode = 1;
  } else {
    throw new Error(`unknown action: ${action}`);
  }
}

main(process.argv.slice(2)).catch((error) => {
  process.stderr.write(`${error?.message || error}\n`);
  process.exitCode = 1;
});
