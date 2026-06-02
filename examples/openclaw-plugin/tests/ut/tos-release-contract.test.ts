import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const rootDir = join(__dirname, "../..");

function readText(path: string): string {
  return readFileSync(join(rootDir, path), "utf8");
}

function resolveReleaseVersion(args: string[]): { version: string; tag: string; channel: string } {
  return JSON.parse(execFileSync("node", [join(rootDir, "scripts/resolve-release-version.mjs"), ...args], {
    encoding: "utf8",
  }));
}

describe("TOS release and single installer contract", () => {
  it("generates a release manifest and checksums for artifacts", () => {
    const tempDir = mkdtempSync(join(tmpdir(), "openviking-manifest-"));
    const packagePath = join(tempDir, "openviking.tgz");
    const installerPath = join(tempDir, "install.sh");
    const notesPath = join(tempDir, "release-notes.md");
    const manifestPath = join(tempDir, "manifest.json");
    const checksumsPath = join(tempDir, "checksums.sha256");

    writeFileSync(packagePath, "package-content");
    writeFileSync(installerPath, "#!/bin/sh\n");
    writeFileSync(notesPath, "Release notes");

    execFileSync("node", [
      join(rootDir, "scripts/generate-release-manifest.mjs"),
      "--env",
      "prod",
      "--version",
      "2026.5.8",
      "--tag",
      "v2026.5.8",
      "--git-hash",
      "0123456789abcdef0123456789abcdef01234567",
      "--artifact",
      packagePath,
      "--artifact",
      installerPath,
      "--notes",
      notesPath,
      "--out",
      manifestPath,
      "--checksums-out",
      checksumsPath,
    ]);

    const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
    const checksums = readFileSync(checksumsPath, "utf8");

    expect(manifest.schemaVersion).toBe("1.0");
    expect(manifest.environment).toBe("prod");
    expect(manifest.plugin.id).toBe("openviking");
    expect(manifest.release.version).toBe("2026.5.8");
    expect(manifest.release.tag).toBe("v2026.5.8");
    expect(manifest.release.gitShortHash).toBe("0123456");
    expect(manifest.tos.bucket).toBe("arkclaw-openviking");
    expect(manifest.artifacts.map((artifact: { name: string }) => artifact.name)).toEqual([
      "openviking.tgz",
      "install.sh",
    ]);
    expect(manifest.artifacts.every((artifact: { sha256: string; size: number }) => artifact.sha256.length === 64 && artifact.size > 0)).toBe(true);
    expect(checksums).toContain("openviking.tgz");
    expect(checksums).toContain("install.sh");
  });

  it("generates environment-specific manifest metadata for non-prod releases", () => {
    const tempDir = mkdtempSync(join(tmpdir(), "openviking-manifest-stg-"));
    const packagePath = join(tempDir, "openviking.tgz");
    const installerPath = join(tempDir, "install.sh");
    const manifestPath = join(tempDir, "manifest.json");
    const checksumsPath = join(tempDir, "checksums.sha256");

    writeFileSync(packagePath, "package-content");
    writeFileSync(installerPath, "#!/bin/sh\n");

    execFileSync("node", [
      join(rootDir, "scripts/generate-release-manifest.mjs"),
      "--env",
      "stg",
      "--version",
      "2026.5.8",
      "--tag",
      "v2026.5.8-stg",
      "--git-hash",
      "0123456789abcdef0123456789abcdef01234567",
      "--artifact",
      packagePath,
      "--artifact",
      installerPath,
      "--bucket",
      "arkclaw-openviking-stg",
      "--region",
      "cn-shanghai",
      "--endpoint",
      "tos-cn-shanghai.volces.com",
      "--out",
      manifestPath,
      "--checksums-out",
      checksumsPath,
    ]);

    const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
    const installer = manifest.artifacts.find((artifact: { name: string }) => artifact.name === "install.sh");

    expect(manifest.environment).toBe("stg");
    expect(manifest.tos).toMatchObject({
      bucket: "arkclaw-openviking-stg",
      region: "cn-shanghai",
      endpoint: "tos-cn-shanghai.volces.com",
    });
    expect(installer.defaultArgs.channel).toBe("stg");
  });

  it("defines release-to-tos dry-run and credential rules", () => {
    const releaseScript = readText("scripts/release-to-tos.sh");

    expect(releaseScript).toContain("set -euo pipefail");
    expect(releaseScript).toContain("--release-dir <date>");
    expect(releaseScript).toContain("Defaults to today's yyyy.m.d");
    expect(releaseScript).toContain("today_release_dir");
    expect(releaseScript).toContain("--publish-latest");
    expect(releaseScript).toContain("BUILD_RELEASE_PATH=\"$RELEASE_DIR\" BUILD_VERSION=\"$VERSION\" bash \"$ROOT_DIR/build.sh\"");
    expect(releaseScript).toContain("TEAM_TEST_AK");
    expect(releaseScript).toContain("TEAM_TEST_SK");
    expect(releaseScript).toContain("--dry-run");
    expect(releaseScript).toContain("scripts/generate-release-manifest.mjs");
    expect(releaseScript).toContain("bash \"$ROOT_DIR/build.sh\"");
    expect(releaseScript).toContain("scripts/upload_tos.py");
    expect(releaseScript).toContain("output/manifest.json");
    expect(releaseScript).not.toContain("latest.json");
  });

  it("defaults release version to the next beta for the package base version", () => {
    const tempDir = mkdtempSync(join(tmpdir(), "openviking-version-beta-"));
    const packagePath = join(tempDir, "package.json");
    writeFileSync(packagePath, JSON.stringify({ version: "2026.6.1" }));

    const resolved = resolveReleaseVersion([
      "--package",
      packagePath,
      "--existing-versions",
      "2026.6.1-beta.1,2026.6.1-beta.2,2026.6.1",
    ]);

    expect(resolved).toMatchObject({
      version: "2026.6.1-beta.3",
      tag: "v2026.6.1-beta.3",
      channel: "beta",
    });
  });

  it("resolves explicit stable releases to date-style versions without beta suffix", () => {
    const tempDir = mkdtempSync(join(tmpdir(), "openviking-version-stable-"));
    const packagePath = join(tempDir, "package.json");
    writeFileSync(packagePath, JSON.stringify({ version: "2026.6.1-beta.7" }));

    const resolved = resolveReleaseVersion(["--package", packagePath, "--stable"]);

    expect(resolved).toMatchObject({
      version: "2026.6.1",
      tag: "v2026.6.1",
      channel: "stable",
    });
  });

  it("keeps explicitly requested release versions and derives their tag", () => {
    const resolved = resolveReleaseVersion(["--version", "2026.6.1-beta.9"]);

    expect(resolved).toMatchObject({
      version: "2026.6.1-beta.9",
      tag: "v2026.6.1-beta.9",
      channel: "beta",
    });
  });

  it("defines a TOS SDK release client for upload, verify, and latest update", () => {
    const packageJson = JSON.parse(readText("package.json"));
    const clientScript = readText("scripts/tos-release-client.mjs");
    const uploader = readText("scripts/upload_tos.py");

    expect(packageJson.dependencies["@volcengine/tos-sdk"]).toBeUndefined();
    expect(packageJson.devDependencies["@volcengine/tos-sdk"]).toBeDefined();
    expect(packageJson.overrides?.axios).toMatch(/^\^1\./);
    expect(uploader).toContain("TosClientV2");
    expect(uploader).toContain("put_object_from_file");
    expect(uploader).toContain("put_object_acl");
    expect(uploader).toContain("ensure_bucket");
    expect(uploader).toContain("create_bucket");
    expect(uploader).toContain("stamp_bucket_installer");
    expect(uploader).toContain("stamp_bucket_manifest");
    expect(uploader).toContain("hashlib.sha256");
    expect(uploader).toContain("DEFAULT_TOS_BASE_URL=");
    expect(uploader).toContain("upload_bucket_installer");
    expect(uploader).toContain("default_release_dir");
    expect(uploader).not.toContain('DEFAULT_RELEASE_DIR = "2026.6.3"');
    expect(uploader).toContain("forbid_overwrite=False");
    expect(uploader).toContain("--publish-latest");
    expect(uploader).toContain("manifest.json");
    expect(uploader).toContain("TEAM_TEST_AK");
    expect(uploader).toContain("TEAM_TEST_SK");
    expect(clientScript).toContain("Simplified TOS protocol");
    expect(clientScript).toContain("scripts/release-to-tos.sh");
    expect(clientScript).not.toContain("TOS_ACCESS_KEY");
    expect(clientScript).not.toContain("TOS_SECRET_KEY");
  });

  it("keeps install.sh as the single global installer with default TOS latest", () => {
    const installScript = readText("scripts/install.sh");

    expect(installScript).toContain("requires bash");
    expect(installScript).toContain("Usage: bash install.sh [options]");
    expect(installScript).toContain("<tos-base-url>/latest/openviking.tgz");
    expect(installScript).toContain("DEFAULT_TOS_BASE_URL=\"\"");
    expect(installScript).toContain("INSTALL_TOS_BASE_URL=\"${INSTALL_TOS_BASE_URL:-$DEFAULT_TOS_BASE_URL}\"");
    expect(installScript).toContain("RELEASE_PATH=\"${INSTALL_RELEASE_PATH:-latest}\"");
    expect(installScript).toContain("--source tos|tarball|local|existing");
    expect(installScript).toContain("--date <date>");
    expect(installScript).toContain("--manifest-url <url>");
    expect(installScript).toContain("--verify-only");
    expect(installScript).toContain("openviking.env");
    expect(installScript).toContain("openclaw openviking setup");
  });

  it("does not run OpenViking setup during verify-only mode", () => {
    execFileSync("bash", [join(rootDir, "scripts/install.sh"), "--source", "existing", "--verify-only"], {
      env: {
        ...process.env,
        OPENVIKING_API_KEY: "test-secret-key",
        OPENVIKING_BASE_URL: "",
      },
    });
  });

  it("keeps the Volcengine installer as a thin compatibility wrapper", () => {
    const wrapper = readText("scripts/volcengine-openviking-install.sh");

    expect(wrapper).toContain("Compatibility wrapper");
    expect(wrapper).toContain("exec \"$SCRIPT_DIR/install.sh\"");
    expect(wrapper).not.toContain("openclaw openviking setup");
    expect(wrapper).not.toContain("openclaw plugins install clawhub:@openviking/openclaw-plugin");
  });
});
