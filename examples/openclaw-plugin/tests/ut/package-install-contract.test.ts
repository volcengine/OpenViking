import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const rootDir = join(__dirname, "../..");

function readText(path: string): string {
  return readFileSync(join(rootDir, path), "utf8");
}

describe("standalone package install contract", () => {
  it("uses compiled OpenClaw entries", () => {
    const packageJson = JSON.parse(readText("package.json"));

    expect(packageJson.openclaw.extensions).toEqual(["./dist/index.js"]);
    expect(packageJson.openclaw.setupEntry).toBe("./dist/commands/setup.js");
  });

  it("packages planned architecture modules for the refactor phases", () => {
    const packageJson = JSON.parse(readText("package.json"));

    expect(packageJson.files).toEqual(expect.arrayContaining([
      "registries/",
      "routing/",
      "plugin/",
      "services/",
      "adapters/",
    ]));
  });

  it("requires planned architecture modules for source install builds", () => {
    const installManifest = JSON.parse(readText("install-manifest.json"));

    expect(installManifest.files.required).toEqual(expect.arrayContaining([
      "registries/",
      "routing/",
      "plugin/",
      "services/",
      "adapters/",
    ]));
  });

  it("build script creates the standalone OpenViking package artifacts", () => {
    const buildScript = readText("build.sh");

    expect(buildScript).toContain("set -euo pipefail");
    expect(buildScript).toContain("BUILD_VERSION");
    expect(buildScript).toContain("VERSION=\"${BUILD_VERSION:-$PACKAGE_VERSION}\"");
    expect(buildScript).toContain("BUILD_RELEASE_PATH");
    expect(buildScript).toContain("RELEASE_PATH=\"${BUILD_RELEASE_PATH:-latest}\"");
    expect(buildScript).toContain("npm run typecheck");
    expect(buildScript).toContain("npm test");
    expect(buildScript).toContain("npm run build");
    expect(buildScript).toContain("dist/index.js");
    expect(buildScript).toContain("dist/commands/setup.js");
    expect(buildScript).toContain("output/openviking.tgz");
    expect(buildScript).toContain("output/install.sh");
    expect(buildScript).toContain("require_file \"install-manifest.json\"");
    expect(buildScript).toContain("require_file \"config/feature-gates.json\"");
    expect(buildScript).toContain("cp install-manifest.json");
    expect(buildScript).toContain("cp -R config \"$PACKAGE_DIR/\"");
    expect(buildScript).toContain("pkg.version = process.env.BUILD_VERSION");
    expect(buildScript).toContain("INSTALL_RELEASE_PATH:-$RELEASE_PATH");
    expect(buildScript).toContain("COPYFILE_DISABLE=1");
  });

  it("TOS release stamps dated installers to download artifacts from their own release directory", () => {
    const releaseScript = readText("scripts/release-to-tos.sh");

    expect(releaseScript).toContain("BUILD_RELEASE_PATH=\"$RELEASE_DIR\"");
    expect(releaseScript).toContain("BUILD_VERSION=\"$VERSION\"");
    expect(releaseScript).toContain("--release-dir \"$RELEASE_DIR\"");
    expect(releaseScript).not.toContain("BUILD_RELEASE_PATH=\"latest\"");
  });

  it("packages runtime dependencies required for OpenClaw to load the plugin", () => {
    const packageJson = JSON.parse(readText("package.json"));
    const buildScript = readText("build.sh");
    const installScript = readText("scripts/install.sh");

    expect(packageJson.dependencies["@sinclair/typebox"]).toBeDefined();
    expect(buildScript).toContain("npm install --omit=dev");
    expect(buildScript).toContain("--prefix \"$PACKAGE_DIR\"");
    expect(buildScript).toContain("$PACKAGE_DIR/node_modules/@sinclair/typebox");
    expect(installScript).toContain("$PACKAGE_DIR/node_modules/@sinclair/typebox");
  });

  it("cleans stale compiled artifacts before building the published dist", () => {
    const packageJson = JSON.parse(readText("package.json"));

    expect(packageJson.scripts.build).toContain("rmSync('dist'");
    expect(packageJson.scripts.build).toContain("tsc -p tsconfig.build.json");
  });

  it("build script creates staging directories from absolute paths", () => {
    const buildScript = readText("build.sh");

    expect(buildScript).toContain("ROOT_DIR=");
    expect(buildScript).toContain("cd \"$ROOT_DIR\"");
    expect(buildScript).toContain("OUTPUT_DIR=\"$ROOT_DIR/output\"");
    expect(buildScript).toContain("STAGING_DIR=$(mktemp -d \"$OUTPUT_DIR/.package.XXXXXX\")");
    expect(buildScript).toContain("require_dir \"$STAGING_DIR\"");
    expect(buildScript).toContain("PACKAGE_DIR=\"$STAGING_DIR/$PACKAGE_NAME\"");
    expect(buildScript).not.toContain("OUTPUT_DIR=\"output\"");
  });

  it("build script suppresses macOS extended metadata in tar packages", () => {
    const buildScript = readText("build.sh");

    expect(buildScript).toContain("TAR_CREATE_FLAGS=");
    expect(buildScript).toContain("supports_tar_flag");
    expect(buildScript).toContain("--files-from /dev/null");
    expect(buildScript).toContain("--no-xattrs");
    expect(buildScript).toContain("--disable-copyfile");
    expect(buildScript).toContain("tar \"${TAR_CREATE_FLAGS[@]}\"");
  });

  it("install script deploys plugin files without overwriting user config", () => {
    const installScript = readText("scripts/install.sh");

    expect(installScript).toContain("set -euo pipefail");
    expect(installScript).toContain("openviking.tgz");
    expect(installScript).toContain("OPENCLAW_STATE_DIR=\"${OPENCLAW_STATE_DIR:-$HOME/.openclaw}\"");
    expect(installScript).toContain("EXTENSION_DIR=\"$OPENCLAW_DIR/extensions/openviking\"");
    expect(installScript).toContain(".agents/skills");
    expect(installScript).toContain("plugins.entries.openviking.config = (.plugins.entries.openviking.config // {})");
    expect(installScript).toContain("openclaw gateway restart");
    expect(installScript).toContain("openclaw openviking setup");
    expect(installScript).toContain("--recall-target-types resource");
    expect(installScript).toContain("openclaw config set plugins.entries.openviking.config.recallTargetTypes '[\"resource\"]'");
    expect(installScript).not.toContain("recallTargetTypes '[\\\"resource\\\"]'");
    expect(installScript).toContain("escaped=${value//\\'/\\'\\\\\\'\\'}");
    expect(installScript).toContain("openclaw openviking status --json");
  });
});
