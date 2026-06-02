#!/usr/bin/env node

import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname } from "node:path";

const args = process.argv.slice(2);

function die(message) {
  console.error(`[generate-release-manifest] ERROR: ${message}`);
  process.exit(1);
}

function readOption(name, fallback = "") {
  const index = args.indexOf(name);
  if (index === -1) {
    return fallback;
  }
  const value = args[index + 1];
  if (!value || value.startsWith("--")) {
    die(`Missing value for ${name}`);
  }
  return value;
}

function readRepeated(name) {
  const values = [];
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === name) {
      const value = args[index + 1];
      if (!value || value.startsWith("--")) {
        die(`Missing value for ${name}`);
      }
      values.push(value);
      index += 1;
    }
  }
  return values;
}

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

const environment = readOption("--env");
const version = readOption("--version");
const tag = readOption("--tag");
const gitHash = readOption("--git-hash");
const notesPath = readOption("--notes", "");
const outPath = readOption("--out", "output/manifest.json");
const checksumsOut = readOption("--checksums-out", "");
const bucket = readOption("--bucket", "arkclaw-openviking");
const region = readOption("--region", "cn-beijing");
const endpoint = readOption("--endpoint", "tos-cn-beijing.volces.com");
const releaseDir = readOption("--release-dir", "");
const artifacts = readRepeated("--artifact");

if (!["stg", "ppe", "prod"].includes(environment)) {
  die("--env must be one of stg, ppe, or prod");
}
if (!version) {
  die("--version is required");
}
if (!tag) {
  die("--tag is required");
}
if (!/^[0-9a-f]{40}$/i.test(gitHash)) {
  die("--git-hash must be a 40-character git hash");
}
if (artifacts.length === 0) {
  die("At least one --artifact is required");
}

const releaseNotes = notesPath ? readFileSync(notesPath, "utf8") : "";
const artifactEntries = artifacts.map((path) => {
  const name = basename(path);
  const stats = statSync(path);
  const entry = {
    name,
    type: name === "openviking.tgz" ? "package" : "installer",
    path: releaseDir ? `${releaseDir}/${name}` : `${environment}/releases/${version}/${name}`,
    size: stats.size,
    sha256: sha256(path),
  };
  if (name === "install.sh") {
    entry.entrypoint = true;
    entry.defaultArgs = {
      source: "tos",
      channel: environment,
    };
  }
  return entry;
});

const manifest = {
  schemaVersion: "1.0",
  plugin: {
    id: "openviking",
    packageName: "@openviking/openclaw-plugin",
    version,
  },
  environment,
  channel: environment,
  release: {
    version,
    tag,
    gitHash,
    gitShortHash: gitHash.slice(0, 7),
    createdAt: new Date().toISOString(),
    createdBy: process.env.USER || process.env.CI || "unknown",
    buildHost: process.env.HOSTNAME || "local",
    releaseNotes,
  },
  compatibility: {
    minOpenclawVersion: "2026.4.8",
    minGatewayVersion: "2026.4.8",
    minNodeVersion: "22.0.0",
  },
  artifacts: artifactEntries,
  checksums: {
    algorithm: "sha256",
    file: releaseDir ? "manifest.json" : `${environment}/releases/${version}/checksums.sha256`,
  },
  tos: {
    bucket,
    region,
    endpoint,
  },
  update: {
    defaultInstall: environment === "prod",
    rollbackAllowed: true,
    immutable: true,
  },
};

const checksumLines = artifactEntries.map((artifact) => `${artifact.sha256}  ${artifact.name}`);
mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, `${JSON.stringify(manifest, null, 2)}\n`);
if (checksumsOut) {
  mkdirSync(dirname(checksumsOut), { recursive: true });
  writeFileSync(checksumsOut, `${checksumLines.join("\n")}\n`);
}
