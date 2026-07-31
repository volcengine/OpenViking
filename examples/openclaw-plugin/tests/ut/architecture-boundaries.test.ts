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

function collectSourceFiles(dir: string, extensions: string[]): string[] {
  if (!existsSync(dir)) {
    return [];
  }

  const files: string[] = [];
  for (const entry of readdirSync(dir)) {
    const fullPath = join(dir, entry);
    const stat = statSync(fullPath);
    if (stat.isDirectory()) {
      files.push(...collectSourceFiles(fullPath, extensions));
      continue;
    }
    if (extensions.some((extension) => entry.endsWith(extension))) {
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
  it("keeps the test suite off global fetch stubbing", () => {
    const violations = collectSourceFiles(join(rootDir, "tests"), [".ts", ".tsx", ".js", ".mjs", ".cjs"])
      .filter((file) => readFileSync(file, "utf8").match(/vi\.stubGlobal\(\s*["']fetch["']/m))
      .map((file) => relative(rootDir, file));

    expect(violations).toEqual([]);
  });

  it("keeps tests from carrying stale global fetch cleanup after transport seam migration", () => {
    const violations = collectSourceFiles(join(rootDir, "tests"), [".ts", ".tsx", ".js", ".mjs", ".cjs"])
      .flatMap((file) => {
        const source = readFileSync(file, "utf8");
        const relativePath = relative(rootDir, file);
        if (relativePath === "tests/ut/architecture-boundaries.test.ts") {
          return [];
        }
        const fileViolations: string[] = [];
        if (source.match(/vi\.unstubAllGlobals\(\)/) && !source.match(/vi\.stubGlobal\(/)) {
          fileViolations.push(`${relativePath} removes globals without stubbing globals`);
        }
        if (source.match(/mock(?: at the)? fetch|mock fetch|override the global fetch/i)) {
          fileViolations.push(`${relativePath} still documents global fetch mocking`);
        }
        return fileViolations;
      });

    expect(violations).toEqual([]);
  });

  it("parses dynamic import and require edges for boundary checks", () => {
    const imports = importSpecifiers(`
      import { readOpenClawConfig } from "../services/setup/config-writer.js";
      export * from "../routing/identity-routing.js";
      const root = await import("../index.js");
      const client = require("../client.js");
    `);

    expect(imports).toEqual(expect.arrayContaining([
      "../services/setup/config-writer.js",
      "../routing/identity-routing.js",
      "../index.js",
      "../client.js",
    ]));
  });

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

  it("keeps recall resource type consumers wired to the registry instead of the recall-trace compatibility facade", () => {
    const violations = collectTypeScriptFiles(rootDir)
      .filter((file) => {
        const relativePath = relative(rootDir, file);
        return !relativePath.startsWith("tests/") && relativePath !== "recall-trace.ts";
      })
      .filter((file) => {
        const source = readFileSync(file, "utf8");
        return Array.from(source.matchAll(/import\s+\{([^{}]*?)\}\s+from\s+["']\.\/recall-trace\.js["']/g))
          .concat(Array.from(source.matchAll(/import\s+type\s+\{([^{}]*?)\}\s+from\s+["']\.\.\/recall-trace\.js["']/g)))
          .some((match) => match[1]?.match(/\b(?:normalizeResourceTypes|resolveRecallSearchPlan|RecallResourceType)\b/));
      })
      .map((file) => `${relative(rootDir, file)} imports recall resource helpers from recall-trace.js`);

    expect(violations).toEqual([]);
  });

  it("keeps dead recall-trace resource helper compatibility exports removed", () => {
    const recallTraceSource = readFileSync(join(rootDir, "recall-trace.ts"), "utf8");
    const recallResourceTestSource = readFileSync(join(rootDir, "tests/ut/recall-resource-types.test.ts"), "utf8");
    const recallTraceTestSource = readFileSync(join(rootDir, "tests/ut/recall-trace.test.ts"), "utf8");

    expect(recallTraceSource).not.toMatch(/export\s+type\s+\{\s*RecallResourceType\s*\}/);
    expect(recallTraceSource).not.toMatch(/export\s+function\s+normalizeResourceTypes\b/);
    expect(recallTraceSource).not.toMatch(/export\s+function\s+resolveRecallSearchPlan\b/);
    expect(recallResourceTestSource).not.toMatch(/from\s+["']\.\.\/\.\.\/recall-trace\.js["']/);
    expect(recallTraceTestSource).not.toMatch(/\b(?:normalizeResourceTypes|resolveRecallSearchPlan)\b/);
  });

  it("keeps tool registry consumers wired to the registry instead of config compatibility exports", () => {
    const violations = collectSourceFiles(rootDir, [".ts"])
      .filter((file) => relative(rootDir, file) !== "config.ts")
      .flatMap((file) => {
        const source = readFileSync(file, "utf8");
        return Array.from(source.matchAll(/import\s+\{([^{}]*?OPENVIKING_[^{}]*?)\}\s+from\s+["'][^"']*config\.js["']/g))
          .map(() => relative(rootDir, file));
      });

    expect(violations).toEqual([]);
  });

  it("keeps production code off context-engine compatibility exports for message and routing helpers", () => {
    const compatibilityHelpers = [
      "convertToAgentMessages",
      "ensureAlternation",
      "formatMessageFaithful",
      "mergeConsecutiveAssistants",
      "mergeConsecutiveUsers",
      "toRoleId",
      "openClawSessionRefToOvStorageId",
      "openClawSessionToOvStorageId",
    ];
    const violations = collectTypeScriptFiles(rootDir)
      .filter((file) => {
        const relativePath = relative(rootDir, file);
        return !relativePath.startsWith("tests/") && relativePath !== "context-engine.ts";
      })
      .flatMap((file) => {
        const source = readFileSync(file, "utf8");
        return Array.from(source.matchAll(/import\s+\{([^{}]*?)\}\s+from\s+["']\.\/context-engine\.js["']/g))
          .flatMap((match) => {
            const importedNames = match[1] ?? "";
            return compatibilityHelpers
              .filter((helper) => importedNames.match(new RegExp(`\\b${helper}\\b`)))
              .map((helper) => `${relative(rootDir, file)} imports ${helper} from ./context-engine.js`);
          });
      });

    expect(violations).toEqual([]);
  });

  it("keeps tests off context-engine helper re-exports", () => {
    const compatibilityHelpers = [
      "convertToAgentMessages",
      "ensureAlternation",
      "formatMessageFaithful",
      "mergeConsecutiveAssistants",
      "mergeConsecutiveUsers",
      "toRoleId",
      "openClawSessionRefToOvStorageId",
      "openClawSessionToOvStorageId",
    ];
    const violations = collectSourceFiles(join(rootDir, "tests"), [".ts", ".tsx"])
      .flatMap((file) => {
        const source = readFileSync(file, "utf8");
        return Array.from(source.matchAll(/import\s+\{([^{}]*?)\}\s+from\s+["'][^"']*context-engine\.js["']/g))
          .flatMap((match) => {
            const importedNames = match[1] ?? "";
            return compatibilityHelpers
              .filter((helper) => importedNames.match(new RegExp(`\\b${helper}\\b`)))
              .map((helper) => `${relative(rootDir, file)} imports ${helper} from context-engine.js`);
          });
      });

    expect(violations).toEqual([]);
  });

  it("keeps dead context-engine helper compatibility re-exports removed", () => {
    const contextEngineSource = readFileSync(join(rootDir, "context-engine.ts"), "utf8");

    expect(contextEngineSource).not.toMatch(/export\s*\{[\s\S]*(?:convertToAgentMessages|ensureAlternation|formatMessageFaithful|mergeConsecutiveAssistants|mergeConsecutiveUsers|toRoleId)[\s\S]*\}\s*from\s*["']\.\/services\/context-message-adapter\.js["']/);
    expect(contextEngineSource).not.toMatch(/export\s*\{[\s\S]*(?:openClawSessionRefToOvStorageId|openClawSessionToOvStorageId)[\s\S]*\}\s*from\s*["']\.\/routing\/identity-routing\.js["']/);
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

  it("keeps setup CLI free of direct network fetch logic", () => {
    const setupSource = readFileSync(join(rootDir, "commands/setup.ts"), "utf8");

    expect(setupSource).not.toMatch(/\bfetch\s*\(/);
    expect(setupSource).not.toMatch(/\bAbortController\b/);
  });

  it("keeps setup tests off the legacy __test__ aggregate export", () => {
    const violations = collectSourceFiles(join(rootDir, "tests"), [".ts", ".tsx"])
      .filter((file) => readFileSync(file, "utf8").match(/import\s+\{\s*__test__\s*\}\s+from\s+["'][^"']*commands\/setup\.js["']/))
      .map((file) => relative(rootDir, file));

    expect(violations).toEqual([]);
  });

  it("keeps setup service helper tests off the setup command compatibility facade", () => {
    const setupCommandSource = readFileSync(join(rootDir, "commands/setup.ts"), "utf8");
    const setupCliTestSource = readFileSync(join(rootDir, "tests/ut/setup-cli.test.ts"), "utf8");

    expect(setupCommandSource).not.toMatch(/export\s*\{\s*isLegacyLocalMode\s*\}\s*from\s*["']\.\.\/services\/setup\/setup-flow\.js["']/);
    expect(setupCommandSource).not.toMatch(/export\s*\{[\s\S]*activateContextEngineSlot[\s\S]*\}\s*from\s*["']\.\.\/services\/setup\/config-writer\.js["']/);
    expect(setupCliTestSource).not.toMatch(/import\s*\{[\s\S]*(?:isLegacyLocalMode|activateContextEngineSlot|ensureInstallRecord|isContextEngineSlotActive)[\s\S]*\}\s*from\s*["']\.\.\/\.\.\/commands\/setup\.js["']/);
  });

  it("keeps non-interactive setup tests on the setup service seam", () => {
    const setupCommandSource = readFileSync(join(rootDir, "commands/setup.ts"), "utf8");
    const setupCommandTestSource = readFileSync(join(rootDir, "tests/ut/setup-command.test.ts"), "utf8");

    expect(setupCommandSource).not.toMatch(/export\s+const\s+setupNonInteractive\b/);
    expect(setupCommandTestSource).not.toMatch(/import\s*\{\s*setupNonInteractive\s*\}\s*from\s*["']\.\.\/\.\.\/commands\/setup\.js["']/);
    expect(setupCommandTestSource).toContain("createOpenVikingSetupService");
  });

  it("keeps setup pure helper tests on concrete setup modules", () => {
    const setupCommandSource = readFileSync(join(rootDir, "commands/setup.ts"), "utf8");
    const setupCliTestSource = readFileSync(join(rootDir, "tests/ut/setup-cli.test.ts"), "utf8");
    const helperNames = [
      "findPluginPackageRoot",
      "parseVersionTuple",
      "compareVersions",
      "checkVersionCompatibility",
      "setExitCodeOnFailure",
    ];

    for (const helperName of helperNames) {
      expect(setupCommandSource).not.toMatch(new RegExp(`export\\s+function\\s+${helperName}\\b`));
    }
    expect(setupCliTestSource).not.toMatch(/import\s*\{[\s\S]*(?:findPluginPackageRoot|parseVersionTuple|compareVersions|checkVersionCompatibility|setExitCodeOnFailure)[\s\S]*\}\s*from\s*["']\.\.\/\.\.\/commands\/setup\.js["']/);
    expect(setupCliTestSource).toContain("../../services/setup/package-metadata.js");
    expect(setupCliTestSource).toContain("../../services/setup/version-compatibility.js");
  });

  it("keeps temp upload body construction inside the resource packager seam", () => {
    const clientSource = readFileSync(join(rootDir, "client.ts"), "utf8");
    const packagerSource = readFileSync(join(rootDir, "adapters/resource-packager.ts"), "utf8");

    expect(clientSource).not.toMatch(/\breadFile\s*\(/);
    expect(clientSource).not.toMatch(/new\s+FormData\s*\(/);
    expect(clientSource).not.toMatch(/new\s+Blob\s*\(/);
    expect(clientSource).not.toMatch(/function\s+toBlobPart\s*\(/);
    expect(packagerSource).toContain("createTempUploadBody");
  });

  it("keeps memory URI classification on the routing seam instead of the client facade", () => {
    const clientSource = readFileSync(join(rootDir, "client.ts"), "utf8");
    const memoryToolSource = readFileSync(join(rootDir, "plugin/openviking-memory-tools.ts"), "utf8");
    const clientTestSource = readFileSync(join(rootDir, "tests/ut/client.test.ts"), "utf8");

    expect(clientSource).not.toMatch(/export\s+function\s+isMemoryUri\b/);
    expect(memoryToolSource).toContain("../routing/memory-uri.js");
    expect(memoryToolSource).not.toMatch(/import\s*\{[\s\S]*isMemoryUri[\s\S]*\}\s*from\s*["']\.\.\/client\.js["']/);
    expect(clientTestSource).not.toMatch(/import\s*\{[\s\S]*isMemoryUri[\s\S]*\}\s*from\s*["']\.\.\/\.\.\/client\.js["']/);
    expect(clientTestSource).toContain("../../routing/memory-uri.js");
  });

  it("keeps client request happy-path tests on the injected transport seam", () => {
    const clientTestSource = readFileSync(join(rootDir, "tests/ut/client.test.ts"), "utf8");
    const start = clientTestSource.indexOf('it("addResource posts remote URL as path"');
    const end = clientTestSource.indexOf('  it("addResource uploads local file before posting temp_file_id"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const happyPathBlock = clientTestSource.slice(start, end);
    expect(happyPathBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(happyPathBlock).toMatch(/new OpenVikingClient\([\s\S]*\{\s*transport\s*\}/);
  });

  it("keeps local file addResource happy-path tests on the injected transport seam", () => {
    const clientTestSource = readFileSync(join(rootDir, "tests/ut/client.test.ts"), "utf8");
    const start = clientTestSource.indexOf('it("addResource uploads local file before posting temp_file_id"');
    const end = clientTestSource.indexOf('  it("addResource zips local directory before upload"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const happyPathBlock = clientTestSource.slice(start, end);
    expect(happyPathBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(happyPathBlock).toMatch(/new OpenVikingClient\([\s\S]*\{\s*transport\s*\}/);
  });

  it("keeps local directory addResource tests on injected transport and resource packager seams", () => {
    const clientTestSource = readFileSync(join(rootDir, "tests/ut/client.test.ts"), "utf8");
    const start = clientTestSource.indexOf('it("addResource zips local directory before upload"');
    const end = clientTestSource.indexOf('  it("addSkill uploads local SKILL.md file"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const directoryBlock = clientTestSource.slice(start, end);
    expect(directoryBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(directoryBlock).toMatch(/new OpenVikingClient\([\s\S]*\{\s*transport[\s\S]*resourcePackager[\s\S]*\}/);
    expect(directoryBlock).toContain("prepareResourceSource");
    expect(directoryBlock).toContain("cleanup");
  });

  it("keeps local file addSkill tests on the injected transport seam", () => {
    const clientTestSource = readFileSync(join(rootDir, "tests/ut/client.test.ts"), "utf8");
    const start = clientTestSource.indexOf('it("addSkill uploads local SKILL.md file"');
    const end = clientTestSource.indexOf('  it("addSkill removes temporary zip directory after uploading a skill directory"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const localSkillBlock = clientTestSource.slice(start, end);
    expect(localSkillBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(localSkillBlock).toMatch(/new OpenVikingClient\([\s\S]*\{\s*transport\s*\}/);
  });

  it("keeps local directory addSkill tests on injected transport and resource packager seams", () => {
    const clientTestSource = readFileSync(join(rootDir, "tests/ut/client.test.ts"), "utf8");
    const start = clientTestSource.indexOf('it("addSkill removes temporary zip directory after uploading a skill directory"');
    const end = clientTestSource.indexOf('  it("addSkill posts raw skill data directly"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const directorySkillBlock = clientTestSource.slice(start, end);
    expect(directorySkillBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(directorySkillBlock).toMatch(/new OpenVikingClient\([\s\S]*\{\s*transport[\s\S]*resourcePackager[\s\S]*\}/);
    expect(directorySkillBlock).toContain("prepareLocalUploadSource");
    expect(directorySkillBlock).toContain("cleanup");
  });

  it("keeps inline addSkill data tests on the injected transport seam", () => {
    const clientTestSource = readFileSync(join(rootDir, "tests/ut/client.test.ts"), "utf8");
    const start = clientTestSource.indexOf('it("addSkill posts raw skill data directly"');
    const end = clientTestSource.indexOf('  it("addSkill posts MCP tool dict directly"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const inlineSkillBlock = clientTestSource.slice(start, end);
    expect(inlineSkillBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(inlineSkillBlock).toMatch(/new OpenVikingClient\([\s\S]*\{\s*transport\s*\}/);
  });

  it("keeps MCP addSkill data tests on the injected transport seam", () => {
    const clientTestSource = readFileSync(join(rootDir, "tests/ut/client.test.ts"), "utf8");
    const start = clientTestSource.indexOf('it("addSkill posts MCP tool dict directly"');
    const end = clientTestSource.indexOf('  it("surfaces OpenViking error responses"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const mcpSkillBlock = clientTestSource.slice(start, end);
    expect(mcpSkillBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(mcpSkillBlock).toMatch(/new OpenVikingClient\([\s\S]*\{\s*transport\s*\}/);
  });

  it("keeps OpenViking error response client tests on the injected transport seam", () => {
    const clientTestSource = readFileSync(join(rootDir, "tests/ut/client.test.ts"), "utf8");
    const start = clientTestSource.indexOf('it("surfaces OpenViking error responses"');
    const end = clientTestSource.indexOf('  it("uses an extended request timeout for wait=true imports"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const errorResponseBlock = clientTestSource.slice(start, end);
    expect(errorResponseBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(errorResponseBlock).toMatch(/new OpenVikingClient\([\s\S]*\{\s*transport\s*\}/);
  });

  it("keeps import timeout client tests on the injected transport seam", () => {
    const clientTestSource = readFileSync(join(rootDir, "tests/ut/client.test.ts"), "utf8");
    const waitStart = clientTestSource.indexOf('it("uses an extended request timeout for wait=true imports"');
    const waitEnd = clientTestSource.indexOf('  it("still uses the default request timeout for non-wait imports"', waitStart);
    const nonWaitEnd = clientTestSource.indexOf('  it("keeps polling wait=true commit long enough for slow Phase 2 completion"', waitEnd);

    expect(waitStart).toBeGreaterThanOrEqual(0);
    expect(waitEnd).toBeGreaterThan(waitStart);
    expect(nonWaitEnd).toBeGreaterThan(waitEnd);

    const timeoutBlock = clientTestSource.slice(waitStart, nonWaitEnd);
    expect(timeoutBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(timeoutBlock).toMatch(/new OpenVikingClient\([\s\S]*\{\s*transport\s*\}/);
  });

  it("keeps commit polling client tests on the injected transport seam", () => {
    const clientTestSource = readFileSync(join(rootDir, "tests/ut/client.test.ts"), "utf8");
    const start = clientTestSource.indexOf('it("keeps polling wait=true commit long enough for slow Phase 2 completion"');
    const end = clientTestSource.indexOf('});\n\ndescribe("OpenVikingClient tenant headers', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const commitPollingBlock = clientTestSource.slice(start, end);
    expect(commitPollingBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(commitPollingBlock).toMatch(/new OpenVikingClient\([\s\S]*\{\s*transport\s*\}/);
  });

  it("keeps tenant header client tests on the injected transport seam", () => {
    const clientTestSource = readFileSync(join(rootDir, "tests/ut/client.test.ts"), "utf8");
    const start = clientTestSource.indexOf('describe("OpenVikingClient tenant headers');
    const end = clientTestSource.indexOf('describe("OpenVikingClient canonical namespace policy"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const tenantHeaderBlock = clientTestSource.slice(start, end);
    expect(tenantHeaderBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(tenantHeaderBlock).toMatch(/new OpenVikingClient\([\s\S]*\{\s*transport\s*\}/);
  });

  it("keeps canonical namespace client tests on the injected transport seam", () => {
    const clientTestSource = readFileSync(join(rootDir, "tests/ut/client.test.ts"), "utf8");
    const start = clientTestSource.indexOf('describe("OpenVikingClient canonical namespace policy"');
    const end = clientTestSource.indexOf('  it("includes peer_id when addSessionMessage receives one"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const canonicalNamespaceBlock = clientTestSource.slice(start, end);
    expect(canonicalNamespaceBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(canonicalNamespaceBlock).toMatch(/new OpenVikingClient\([\s\S]*\{\s*transport\s*\}/);
  });

  it("keeps addSessionMessage client tests on the injected transport seam", () => {
    const clientTestSource = readFileSync(join(rootDir, "tests/ut/client.test.ts"), "utf8");
    const start = clientTestSource.indexOf('it("includes peer_id when addSessionMessage receives one"');
    const end = clientTestSource.indexOf('\n});', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const addSessionMessageBlock = clientTestSource.slice(start, end);
    expect(addSessionMessageBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(addSessionMessageBlock).toMatch(/new OpenVikingClient\([\s\S]*\{\s*transport\s*\}/);
  });

  it("keeps memory_recall L2 content tests on the plugin-injected transport seam", () => {
    const toolsTestSource = readFileSync(join(rootDir, "tests/ut/tools.test.ts"), "utf8");
    const start = toolsTestSource.indexOf('it("fills L2 content and filters explicit recall results like auto-recall"');
    const end = toolsTestSource.indexOf('  it("applies recallMaxInjectedChars to explicit memory_recall output"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const recallL2Block = toolsTestSource.slice(start, end);
    expect(recallL2Block).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(recallL2Block).toContain("openVikingTransport");
  });

  it("keeps memory_recall injected char budget tests on the plugin-injected transport seam", () => {
    const toolsTestSource = readFileSync(join(rootDir, "tests/ut/tools.test.ts"), "utf8");
    const start = toolsTestSource.indexOf('it("applies recallMaxInjectedChars to explicit memory_recall output"');
    const end = toolsTestSource.indexOf('  it("applies /ov-query-config session settings to subsequent memory_recall"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const recallBudgetBlock = toolsTestSource.slice(start, end);
    expect(recallBudgetBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(recallBudgetBlock).toContain("openVikingTransport");
  });

  it("keeps memory_recall runtime query config tests on the plugin-injected transport seam", () => {
    const toolsTestSource = readFileSync(join(rootDir, "tests/ut/tools.test.ts"), "utf8");
    const start = toolsTestSource.indexOf('it("applies /ov-query-config session settings to subsequent memory_recall"');
    const end = toolsTestSource.indexOf('  it("supports /ov-query-config get, unset, and reset for session scope"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const runtimeQueryConfigBlock = toolsTestSource.slice(start, end);
    expect(runtimeQueryConfigBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(runtimeQueryConfigBlock).toContain("openVikingTransport");
  });

  it("keeps memory_store behavioral tests on the plugin-injected transport seam", () => {
    const toolsTestSource = readFileSync(join(rootDir, "tests/ut/tools.test.ts"), "utf8");
    const start = toolsTestSource.indexOf('describe("Tool: memory_store (behavioral)"');
    const end = toolsTestSource.indexOf('describe("Tool: memory_forget (behavioral)"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const memoryStoreBlock = toolsTestSource.slice(start, end);
    expect(memoryStoreBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(memoryStoreBlock).toContain("openVikingTransport");
  });

  it("keeps OpenViking tool result access tests on the plugin-injected transport seam", () => {
    const toolsTestSource = readFileSync(join(rootDir, "tests/ut/tools.test.ts"), "utf8");
    const start = toolsTestSource.indexOf('describe("Tool: OpenViking tool result access"');
    const end = toolsTestSource.indexOf('describe("Tool: add_resource, add_skill, and ov_search (registration)"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const toolResultBlock = toolsTestSource.slice(start, end);
    expect(toolResultBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(toolResultBlock).toContain("openVikingTransport");
  });

  it("keeps ov_search behavioral tests on the plugin-injected transport seam", () => {
    const toolsTestSource = readFileSync(join(rootDir, "tests/ut/tools.test.ts"), "utf8");
    const start = toolsTestSource.indexOf('describe("Tool: ov_search (behavioral)"');
    const end = toolsTestSource.indexOf('describe("Tool: ov_recall_trace"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const ovSearchBlock = toolsTestSource.slice(start, end);
    expect(ovSearchBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(ovSearchBlock).toContain("openVikingTransport");
  });

  it("keeps ov_recall_trace tests on the plugin-injected transport seam", () => {
    const toolsTestSource = readFileSync(join(rootDir, "tests/ut/tools.test.ts"), "utf8");
    const start = toolsTestSource.indexOf('describe("Tool: ov_recall_trace"');
    const end = toolsTestSource.indexOf('describe("Plugin registration"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const recallTraceBlock = toolsTestSource.slice(start, end);
    expect(recallTraceBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(recallTraceBlock).toContain("openVikingTransport");
  });

  it("keeps plugin registration command and import tests on the plugin-injected transport seam", () => {
    const toolsTestSource = readFileSync(join(rootDir, "tests/ut/tools.test.ts"), "utf8");
    const start = toolsTestSource.indexOf('describe("Plugin registration"');
    const end = toolsTestSource.indexOf('describe("Tool: memory_forget (error paths)"', start);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const pluginRegistrationBlock = toolsTestSource.slice(start, end);
    expect(pluginRegistrationBlock).not.toMatch(/vi\.stubGlobal\("fetch"/);
    expect(pluginRegistrationBlock).toContain("openVikingTransport");
  });

  it("keeps setup modules wired to concrete setup subservices instead of compatibility barrels and adapters", () => {
    const checkedFiles = ["commands/setup.ts"];
    const violations: string[] = [];

    for (const file of checkedFiles) {
      const imports = importSpecifiers(readFileSync(join(rootDir, file), "utf8"));
      for (const specifier of imports) {
        if (
          specifier.endsWith("/services/setup-service.js") ||
          specifier === "../services/setup-service.js" ||
          specifier.endsWith("/adapters/setup-network.js") ||
          specifier === "../adapters/setup-network.js" ||
          specifier.endsWith("/adapters/setup-io.js") ||
          specifier === "../adapters/setup-io.js"
        ) {
          violations.push(`${file} -> ${specifier}`);
        }
      }
    }

    expect(violations).toEqual([]);
  });

  it("keeps setup modules off the legacy setup-service compatibility barrel", () => {
    const violations: string[] = [];
    const legacyBarrel = join(rootDir, "services/setup-service.ts");

    if (existsSync(legacyBarrel)) {
      violations.push(relative(rootDir, legacyBarrel));
    }

    for (const dir of ["commands", "tests/ut"]) {
      for (const file of collectTypeScriptFiles(join(rootDir, dir))) {
        const imports = importSpecifiers(readFileSync(file, "utf8"));
        for (const specifier of imports) {
          if (
            specifier === "../services/setup-service.js" ||
            specifier === "../../services/setup-service.js" ||
            specifier.endsWith("/services/setup-service.js")
          ) {
            violations.push(`${relative(rootDir, file)} -> ${specifier}`);
          }
        }
      }
    }

    expect(violations).toEqual([]);
  });

  it("keeps context-engine wired to the service message adapter instead of the legacy adapter path", () => {
    const contextEngineImports = importSpecifiers(readFileSync(join(rootDir, "context-engine.ts"), "utf8"));

    expect(contextEngineImports).toContain("./services/context-message-adapter.js");
    expect(contextEngineImports).not.toContain("./adapters/context-engine-message-adapter.js");
  });

  it("keeps context-engine wired to the canonical lifecycle service module", () => {
    const contextEngineImports = importSpecifiers(readFileSync(join(rootDir, "context-engine.ts"), "utf8"));

    expect(contextEngineImports).toContain("./services/context-lifecycle-service.js");
    expect(contextEngineImports).not.toContain("./services/context-engine-lifecycle.js");
  });

  it("keeps lifecycle service reusing message adapter role normalization", () => {
    const lifecycleSource = readFileSync(join(rootDir, "services/context-lifecycle-service.ts"), "utf8");
    const lifecycleImports = importSpecifiers(lifecycleSource);

    expect(lifecycleImports).toContain("./context-message-adapter.js");
    expect(lifecycleSource).not.toMatch(/function\s+toRoleId\s*\(/);
  });

  it("keeps provider message sanitation owned by the message adapter seam", () => {
    const contextEngineSource = readFileSync(join(rootDir, "context-engine.ts"), "utf8");
    const contextEngineImports = importSpecifiers(contextEngineSource);
    const messageAdapterSource = readFileSync(join(rootDir, "services/context-message-adapter.ts"), "utf8");
    const messageAdapterImports = importSpecifiers(messageAdapterSource);

    expect(contextEngineImports).not.toContain("./session-transcript-repair.js");
    expect(contextEngineSource).not.toMatch(/function\s+(normalizeAssistantContent|canonicalizeAgentMessages)\s*\(/);
    expect(messageAdapterImports).toContain("../session-transcript-repair.js");
    expect(messageAdapterSource).toContain("sanitizeAgentMessagesForProvider");
  });

  it("keeps synthetic missing tool-result construction private to transcript repair", () => {
    const repairSource = readFileSync(join(rootDir, "session-transcript-repair.ts"), "utf8");
    const repairTestSource = readFileSync(join(rootDir, "tests/ut/session-transcript-repair.test.ts"), "utf8");

    expect(repairSource).not.toMatch(/export\s*\{\s*makeMissingToolResult\s*\}/);
    expect(repairTestSource).not.toMatch(/import\s*\{[\s\S]*makeMissingToolResult[\s\S]*\}\s*from\s*["']\.\.\/\.\.\/session-transcript-repair\.js["']/);
    expect(repairTestSource).toContain("repairToolUseResultPairing");
  });

  it("keeps assembled context construction inside the lifecycle service", () => {
    const contextEngineSource = readFileSync(join(rootDir, "context-engine.ts"), "utf8");
    const lifecycleSource = readFileSync(join(rootDir, "services/context-lifecycle-service.ts"), "utf8");

    expect(contextEngineSource).not.toMatch(/function\s+buildAssembledContext\s*\(/);
    expect(contextEngineSource).not.toContain("buildAssembledContext,");
    expect(lifecycleSource).toMatch(/function\s+buildAssembledContext\s*\(/);
  });

  it("keeps Phase 2 polling lifecycle inside the lifecycle service", () => {
    const contextEngineSource = readFileSync(join(rootDir, "context-engine.ts"), "utf8");
    const lifecycleSource = readFileSync(join(rootDir, "services/context-lifecycle-service.ts"), "utf8");

    expect(contextEngineSource).not.toMatch(/function\s+pollPhase2ExtractionOutcome\s*\(/);
    expect(contextEngineSource).not.toContain("DEFAULT_PHASE2_POLL_TIMEOUT_MS");
    expect(contextEngineSource).not.toContain("getTask(");
    expect(lifecycleSource).toMatch(/function\s+pollPhase2ExtractionOutcome\s*\(/);
  });

  it("keeps externalized tool-result handlers out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("registerOpenVikingToolResultTools");
    expect(indexSource).not.toMatch(/name:\s*"openviking_tool_result_/);
    expect(indexSource).not.toContain("ToolResultRef");
    expect(indexSource).not.toContain("parseToolResultRef");
  });

  it("keeps archive handlers out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("registerOpenVikingArchiveTools");
    expect(indexSource).not.toMatch(/name:\s*"ov_archive_(search|expand)"/);
    expect(indexSource).not.toContain("grepSessionArchives");
  });

  it("keeps memory store and forget handlers out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("registerOpenVikingMemoryTools");
    expect(indexSource).not.toMatch(/name:\s*"memory_(store|forget)"/);
  });

  it("keeps memory recall handler out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("registerOpenVikingMemoryRecallTools");
    expect(indexSource).not.toMatch(/name:\s*"memory_recall"/);
  });

  it("keeps import tool handlers out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("registerOpenVikingImportTools");
    expect(indexSource).not.toMatch(/name:\s*"add_(resource|skill)"/);
  });

  it("keeps import runtime out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("createOpenVikingImportRuntime");
    expect(indexSource).not.toContain("formatResourceImportText");
    expect(indexSource).not.toContain("formatSkillImportText");
    expect(indexSource).not.toContain("resource_imported");
    expect(indexSource).not.toContain("skill_imported");
  });

  it("keeps query tool handlers out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("registerOpenVikingQueryTools");
    expect(indexSource).not.toMatch(/name:\s*"ov_(search|read)"/);
  });

  it("keeps tool enabled-set construction out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("createOpenVikingToolRegistrationRuntime");
    expect(indexSource).not.toContain("new Set<string>(cfg.enabledTools)");
    expect(indexSource).not.toContain("enabledToolNames");
  });

  it("keeps OpenViking query runtime out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("createOpenVikingQueryRuntime");
    expect(indexSource).not.toContain("mergeFindResults");
    expect(indexSource).not.toContain("formatOVSearchRows");
    expect(indexSource).not.toContain("formatOVSearchText");
    expect(indexSource).not.toContain("readOpenVikingContent = async");
    expect(indexSource).not.toContain("searchOpenViking = async");
  });

  it("keeps recall trace tool handler out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("registerOpenVikingRecallTraceTools");
    expect(indexSource).not.toMatch(/name:\s*"ov_recall_trace"/);
  });

  it("keeps recall trace query and route runtime out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("createOpenVikingRecallTraceRuntime");
    expect(indexSource).not.toContain("parseRecallTraceInput");
    expect(indexSource).not.toContain("queryTraceForRoute");
    expect(indexSource).not.toContain("handleUriDetail");
    expect(indexSource).not.toContain("handleLatestOvSearchList");
    expect(indexSource).not.toContain("findTraceItem");
    expect(indexSource).not.toContain("toQueryObject");
  });

  it("keeps OpenViking command handlers out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("createOpenVikingCommandDefinitions");
    expect(indexSource).not.toContain("openVikingCommands.push");
    expect(indexSource).not.toContain("OpenViking add resource failed");
    expect(indexSource).not.toContain("OpenViking recall trace query failed");
  });

  it("keeps slash-command argument parsing out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");
    const toolsTestSource = readFileSync(join(rootDir, "tests/ut/tools.test.ts"), "utf8");

    expect(indexSource).toContain("./plugin/openviking-command-args.js");
    expect(indexSource).not.toMatch(/function\s+tokenizeCommandArgs\s*\(/);
    expect(indexSource).not.toMatch(/function\s+parseAddResourceCommandArgs\s*\(/);
    expect(indexSource).not.toMatch(/function\s+parseAddSkillCommandArgs\s*\(/);
    expect(indexSource).not.toMatch(/function\s+parseOVSearchCommandArgs\s*\(/);
    expect(toolsTestSource).toContain("../../plugin/openviking-command-args.js");
  });

  it("keeps auto-recall helper tests off the index compatibility facade", () => {
    const violations = [
      "tests/ut/build-memory-lines.test.ts",
      "tests/context-bloat-730.test.ts",
      "tests/ut/index-utils.test.ts",
    ].flatMap((file) => {
      const source = readFileSync(join(rootDir, file), "utf8");
      const importsFromIndex = source.match(/from\s+["'](?:\.\.\/|\.\.\/\.\.\/)index\.js["']|import\s*\(\s*["'](?:\.\.\/|\.\.\/\.\.\/)index\.js["']\s*\)/g) ?? [];
      const mentionsAutoRecallHelpers = source.match(/\b(?:buildMemoryLines|buildMemoryLinesWithBudget|estimateTokenCount|prepareRecallQuery)\b/);
      return importsFromIndex.length > 0 && mentionsAutoRecallHelpers ? [file] : [];
    });

    expect(violations).toEqual([]);
  });

  it("keeps identity-routing tests off the index compatibility facade", () => {
    const source = readFileSync(join(rootDir, "tests/ut/identity-routing.test.ts"), "utf8");

    expect(source).toContain("../../routing/identity-routing.js");
    expect(source).not.toMatch(/from\s+["']\.\.\/\.\.\/index\.js["']/);
  });

  it("keeps dead helper compatibility re-exports out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).not.toMatch(/export\s*\{[\s\S]*buildMemoryLines/);
    expect(indexSource).not.toContain("estimateAgentMessageTokens");
    expect(indexSource).not.toContain("SessionAgentResolveResult");
    expect(indexSource).not.toContain("tokenizeCommandArgs");
    expect(indexSource).not.toContain("estimateTokenCount");
    expect(indexSource).not.toContain("prepareRecallQuery");
  });

  it("keeps OpenViking runtime utility helpers out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("./plugin/openviking-runtime-utils.js");
    expect(indexSource).not.toMatch(/function\s+previewText\s*\(/);
    expect(indexSource).not.toMatch(/function\s+inferRecallResourceType\s*\(/);
    expect(indexSource).not.toMatch(/function\s+createTraceId\s*\(/);
    expect(indexSource).not.toMatch(/function\s+boundTraceQuery\s*\(/);
    expect(indexSource).not.toMatch(/function\s+extractToolSenderId\s*\(/);
    expect(indexSource).not.toMatch(/const\s+makeBypassedToolResult\s*=/);
    expect(indexSource).not.toContain("memory-store-${Date.now()}");
    expect(indexSource).not.toContain("Math.random().toString(36)");
  });

  it("keeps OpenViking session routing runtime out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("./plugin/openviking-session-routing-runtime.js");
    expect(indexSource).toContain("createOpenVikingSessionRoutingRuntime");
    expect(indexSource).not.toMatch(/const\s+resolvePluginSessionRouting\s*=/);
    expect(indexSource).not.toMatch(/const\s+toQueryConfigContext\s*=/);
    expect(indexSource).not.toMatch(/const\s+rememberSessionAgentId\s*=/);
    expect(indexSource).not.toMatch(/const\s+resolveAgentId\s*=/);
    expect(indexSource).not.toContain("sessionAgentResolver.resolve");
  });

  it("keeps OpenViking client runtime creation out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("./plugin/openviking-client-runtime.js");
    expect(indexSource).toContain("createOpenVikingClientRuntime");
    expect(indexSource).not.toContain("new OpenVikingClient");
    expect(indexSource).not.toMatch(/const\s+clientPromise\s*=/);
    expect(indexSource).not.toMatch(/const\s+getClient\s*=/);
    expect(indexSource).not.toMatch(/const\s+routingDebugLog\s*=/);
    expect(indexSource).not.toMatch(/const\s+verboseRoutingInfo\s*=/);
  });

  it("keeps OpenViking runtime state construction out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("./plugin/openviking-runtime-state.js");
    expect(indexSource).toContain("createOpenVikingRuntimeState");
    expect(indexSource).not.toContain("new RuntimeQueryConfigStore");
    expect(indexSource).not.toContain("new RecallTraceRecorder");
    expect(indexSource).not.toContain("queryConfigStore.load().catch");
  });

  it("keeps bypass-session runtime construction out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("./plugin/openviking-bypass-runtime.js");
    expect(indexSource).toContain("createOpenVikingBypassRuntime");
    expect(indexSource).not.toContain("compileSessionPatterns");
    expect(indexSource).not.toContain("shouldBypassSession(ctx ?? {}, bypassSessionPatterns)");
    expect(indexSource).not.toMatch(/const\s+isBypassedSession\s*=/);
  });

  it("keeps query config command handling out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("createOpenVikingQueryConfigCommandHandler");
    expect(indexSource).not.toContain("parseQueryConfigPatch");
    expect(indexSource).not.toContain("No query config parameters provided for /ov-query-config set");
    expect(indexSource).not.toContain("Reset OpenViking query config");
  });

  it("keeps lifecycle hook handlers out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("registerOpenVikingLifecycleHooks");
    expect(indexSource).not.toContain('api.on("session_start"');
    expect(indexSource).not.toContain('api.on("before_reset"');
    expect(indexSource).not.toContain("committed OV session on reset");
  });

  it("keeps context-engine registration out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("registerOpenVikingContextEngine");
    expect(indexSource).not.toContain("api.registerContextEngine(contextEnginePlugin.id");
    expect(indexSource).not.toContain("createMemoryOpenVikingContextEngine({");
    expect(indexSource).not.toContain("registerContextEngine is unavailable");
  });

  it("keeps context-engine ref state out of the composition root", () => {
    const indexSource = readFileSync(join(rootDir, "index.ts"), "utf8");

    expect(indexSource).toContain("./plugin/openviking-context-engine-ref.js");
    expect(indexSource).toContain("createOpenVikingContextEngineRef");
    expect(indexSource).not.toMatch(/let\s+contextEngineRef\s*:/);
    expect(indexSource).not.toContain("contextEngineRef = engine");
    expect(indexSource).not.toContain("getContextEngine: () => contextEngineRef");
  });
});
