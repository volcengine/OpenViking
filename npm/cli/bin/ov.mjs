#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const platforms = {
  "darwin-arm64":  "@openviking/cli-darwin-arm64",
  "darwin-x64":    "@openviking/cli-darwin-x64",
  "linux-arm64":   "@openviking/cli-linux-arm64",
  "linux-x64":     "@openviking/cli-linux-x64",
  "win32-x64":     "@openviking/cli-win32-x64",
};

const key = `${process.platform}-${process.arch}`;
const pkg = platforms[key];
if (!pkg) {
  console.error(`Unsupported platform: ${key}`);
  process.exit(1);
}

const ext = process.platform === "win32" ? ".exe" : "";
const bin = join(require.resolve(`${pkg}/package.json`), "..", "bin", `ov${ext}`);

try {
  execFileSync(bin, process.argv.slice(2), { stdio: "inherit" });
} catch (e) {
  process.exit(e.status ?? 1);
}
