import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

export type SetupCompatibilityRange = {
  min: string;
  max: string;
};

export function findPluginPackageRoot(fromDir = path.dirname(fileURLToPath(import.meta.url))): string | null {
  let current = path.resolve(fromDir);
  for (let depth = 0; depth < 5; depth += 1) {
    if (
      fs.existsSync(path.join(current, "package.json")) &&
      fs.existsSync(path.join(current, "openclaw.plugin.json"))
    ) {
      return current;
    }

    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return null;
}

export function readPluginVersion(): string {
  try {
    const packageRoot = findPluginPackageRoot();
    if (!packageRoot) return "unknown";
    const pkgPath = path.join(packageRoot, "package.json");
    const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf-8"));
    return String(pkg.version ?? "unknown");
  } catch {
    return "unknown";
  }
}

export function readCompatRangeFromManifest(): SetupCompatibilityRange {
  try {
    const packageRoot = findPluginPackageRoot();
    if (!packageRoot) return { min: "", max: "" };
    const manifestPath = path.join(packageRoot, "install-manifest.json");
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
    const compat = manifest?.compatibility ?? {};
    return {
      min: String(compat.minOpenvikingVersion ?? ""),
      max: String(compat.maxOpenvikingVersion ?? ""),
    };
  } catch {
    return { min: "", max: "" };
  }
}

export const PLUGIN_VERSION = readPluginVersion();
export const { min: COMPATIBLE_SERVER_MIN, max: COMPATIBLE_SERVER_MAX } = readCompatRangeFromManifest();
