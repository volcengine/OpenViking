#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { delimiter, join } from "node:path";
import { fileURLToPath } from "node:url";

const packageTarget = fileURLToPath(new URL("./ov.mjs", import.meta.url));

function write(message = "") {
  process.stderr.write(`${message}\n`);
}

function runVersion(command, args = []) {
  try {
    return execFileSync(command, [...args, "--version"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    }).trim().split(/\r?\n/)[0] || "<version unavailable>";
  } catch {
    return "<version unavailable>";
  }
}

function installedBinTarget() {
  const prefix = process.env.npm_config_prefix;
  if (!prefix || process.env.npm_config_global !== "true") {
    return null;
  }
  return process.platform === "win32"
    ? join(prefix, "ov.cmd")
    : join(prefix, "bin", "ov");
}

function findOvPaths() {
  const names = process.platform === "win32"
    ? ["ov.cmd", "ov.exe", "ov.ps1", "ov"]
    : ["ov"];
  const seen = new Set();
  const paths = [];

  for (const dir of (process.env.PATH || "").split(delimiter)) {
    if (!dir) {
      continue;
    }
    for (const name of names) {
      const candidate = join(dir, name);
      if (seen.has(candidate) || !existsSync(candidate)) {
        continue;
      }
      seen.add(candidate);
      paths.push(candidate);
    }
  }

  return paths;
}

function printPathDiagnostics() {
  const target = installedBinTarget();
  const paths = findOvPaths();

  if (target) {
    if (paths.includes(target)) {
      write(`  PATH target: ${target}`);
    } else {
      write(`  Warning: npm bin target is not on PATH: ${target}`);
    }
  }

  if (target && paths[0] && paths[0] !== target) {
    write(`  Warning: PATH resolves ov to ${paths[0]} before ${target}`);
  }

  if (paths.length > 1) {
    write("  Warning: multiple ov executables found on PATH:");
    for (const path of paths) {
      write(`    ${path} -> ${runVersion(path)}`);
    }
  }
}

process.stderr.write(`
  ╔═══════════════════════════════════════════════════╗
  ║            OpenViking CLI installed               ║
  ╚═══════════════════════════════════════════════════╝

  Installed package target: ${packageTarget}
  Installed package target version: ${runVersion(process.execPath, [packageTarget])}

  Usage:   ov <command> [options]

  Commands:
    ov health              Check server connectivity
    ov search "query"      Context-aware semantic search
    ov ls                  List directory contents
    ov read <uri>          Read full file content
    ov add-resource <path> Add files or URLs
    ov add-memory "text"   Store a memory
    ov config show         Show current configuration

  Run "ov --help" for the full command list.
`);

printPathDiagnostics();
