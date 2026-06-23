#!/usr/bin/env node

import { readFileSync } from "node:fs";

const args = process.argv.slice(2);

function die(message) {
  console.error(`[resolve-release-version] ERROR: ${message}`);
  process.exit(1);
}

function readOption(name, fallback = "") {
  const index = args.indexOf(name);
  if (index === -1) return fallback;
  const value = args[index + 1];
  if (!value || value.startsWith("--")) die(`Missing value for ${name}`);
  return value;
}

function hasFlag(name) {
  return args.includes(name);
}

function readPackageVersion(packagePath) {
  return JSON.parse(readFileSync(packagePath, "utf8")).version;
}

function stableBase(version) {
  const match = String(version).match(/^(\d{4}\.\d{1,2}\.\d{1,2})(?:-beta\.\d+)?$/);
  if (!match) die(`Invalid release version: ${version}`);
  return match[1];
}

function channelOf(version) {
  return /-beta\.\d+$/.test(version) ? "beta" : "stable";
}

function nextBetaVersion(baseVersion, existingVersions) {
  const prefix = `${baseVersion}-beta.`;
  const maxBeta = existingVersions.reduce((max, version) => {
    if (!version.startsWith(prefix)) return max;
    const number = Number(version.slice(prefix.length));
    return Number.isInteger(number) && number > max ? number : max;
  }, 0);
  return `${prefix}${maxBeta + 1}`;
}

const packagePath = readOption("--package", "package.json");
const explicitVersion = readOption("--version", "");
const existingVersions = readOption("--existing-versions", "")
  .split(/[,\n]/)
  .map((version) => version.trim())
  .filter(Boolean);
const stable = hasFlag("--stable");

let version;
if (explicitVersion) {
  version = explicitVersion;
} else {
  const base = stableBase(readPackageVersion(packagePath));
  version = stable ? base : nextBetaVersion(base, existingVersions);
}

console.log(JSON.stringify({
  version,
  tag: `v${version}`,
  channel: stable ? "stable" : channelOf(version),
}));
