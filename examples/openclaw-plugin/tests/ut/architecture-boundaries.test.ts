import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const rootDir = join(__dirname, "../..");

function collectTypeScriptFiles(dir: string): string[] {
  if (!existsSync(dir)) {
    return [];
  }

  const files: string[] = [];
  for (const entry of readdirSync(dir)) {
    const fullPath = join(dir, entry);
    const stat = statSync(fullPath);
    if (stat.isDirectory()) {
      files.push(...collectTypeScriptFiles(fullPath));
      continue;
    }
    if (entry.endsWith(".ts")) {
      files.push(fullPath);
    }
  }
  return files;
}

function importSpecifiers(source: string): string[] {
  const staticImports = Array.from(
    source.matchAll(/\b(?:import|export)\s+(?:type\s+)?(?:[^"']+\s+from\s+)?["']([^"']+)["']/g),
    (match) => match[1],
  );
  const dynamicImports = Array.from(
    source.matchAll(/\bimport\s*\(\s*["']([^"']+)["']\s*\)/g),
    (match) => match[1],
  );
  const requires = Array.from(
    source.matchAll(/\brequire\s*\(\s*["']([^"']+)["']\s*\)/g),
    (match) => match[1],
  );
  return [...new Set([...staticImports, ...dynamicImports, ...requires])];
}

describe("architecture boundaries", () => {
  it("keeps extracted architecture modules from importing the composition root", () => {
    const checkedDirs = ["registries", "routing", "plugin", "services", "adapters"];
    const violations: string[] = [];

    for (const dir of checkedDirs) {
      for (const file of collectTypeScriptFiles(join(rootDir, dir))) {
        const imports = importSpecifiers(readFileSync(file, "utf8"));
        for (const specifier of imports) {
          if (specifier === "../index.js" || specifier.endsWith("/index.js")) {
            violations.push(`${relative(rootDir, file)} -> ${specifier}`);
          }
        }
      }
    }

    expect(violations).toEqual([]);
  });

  it("keeps registry modules independent from runtime composition and client modules", () => {
    const violations: string[] = [];

    for (const file of collectTypeScriptFiles(join(rootDir, "registries"))) {
      const imports = importSpecifiers(readFileSync(file, "utf8"));
      for (const specifier of imports) {
        if (specifier === "../index.js" || specifier.endsWith("/index.js") || specifier === "../client.js" || specifier.endsWith("/client.js")) {
          violations.push(`${relative(rootDir, file)} -> ${specifier}`);
        }
      }
    }

    expect(violations).toEqual([]);
  });

  it("keeps services independent from plugin adapters", () => {
    const violations: string[] = [];

    for (const file of collectTypeScriptFiles(join(rootDir, "services"))) {
      const imports = importSpecifiers(readFileSync(file, "utf8"));
      for (const specifier of imports) {
        if (specifier.includes("/plugin/") || specifier.startsWith("../plugin/")) {
          violations.push(`${relative(rootDir, file)} -> ${specifier}`);
        }
      }
    }

    expect(violations).toEqual([]);
  });

  it("keeps services independent from filesystem and network adapters", () => {
    const violations: string[] = [];

    for (const file of collectTypeScriptFiles(join(rootDir, "services"))) {
      const imports = importSpecifiers(readFileSync(file, "utf8"));
      for (const specifier of imports) {
        if (specifier.includes("/adapters/") || specifier.startsWith("../adapters/")) {
          violations.push(`${relative(rootDir, file)} -> ${specifier}`);
        }
      }
    }

    expect(violations).toEqual([]);
  });

  it("keeps adapters independent from business services", () => {
    const violations: string[] = [];

    for (const file of collectTypeScriptFiles(join(rootDir, "adapters"))) {
      const imports = importSpecifiers(readFileSync(file, "utf8"));
      for (const specifier of imports) {
        if (specifier.includes("/services/") || specifier.startsWith("../services/")) {
          violations.push(`${relative(rootDir, file)} -> ${specifier}`);
        }
      }
    }

    expect(violations).toEqual([]);
  });
});
