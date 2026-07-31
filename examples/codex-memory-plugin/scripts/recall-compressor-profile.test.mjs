import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  detectRecallCompressorProfile,
  invalidateRecallCompressorProfileCache,
  loadCachedRecallCompressorProfile,
  loadCodexModelsCache,
  markRecallCompressorRuntimeFailed,
  resolveRecallCompressorProfile,
} from "./recall-compressor-profile.mjs";

function baseCfg(overrides = {}) {
  return {
    recallCompress: true,
    recallCompressModel: "",
    recallCompressThinking: "",
    recallCompressConfigured: false,
    recallCompressDetectOnStartup: true,
    recallCompressDetectTtlMs: 604_800_000,
    ...overrides,
  };
}

async function withTempState(action) {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-compressor-state-"));
  const codexHome = await mkdtemp(join(tmpdir(), "ov-codex-home-"));
  const prevState = process.env.OPENVIKING_CODEX_STATE_DIR;
  process.env.OPENVIKING_CODEX_STATE_DIR = stateDir;
  try {
    await action({ stateDir, codexHome });
  } finally {
    if (prevState === undefined) delete process.env.OPENVIKING_CODEX_STATE_DIR;
    else process.env.OPENVIKING_CODEX_STATE_DIR = prevState;
    await rm(stateDir, { recursive: true, force: true });
    await rm(codexHome, { recursive: true, force: true });
  }
}

async function writeModelsCache(codexHome, slugs) {
  await writeFile(
    join(codexHome, "models_cache.json"),
    JSON.stringify({
      fetched_at: "2026-06-15T00:00:00Z",
      models: slugs.map((slug) => ({ slug })),
    }),
  );
}

test("loadCodexModelsCache returns empty set when cache missing", async () => {
  await withTempState(async ({ codexHome }) => {
    const result = await loadCodexModelsCache({ CODEX_HOME: codexHome });
    assert.equal(result.present, false);
    assert.equal(result.slugs.size, 0);
  });
});

test("loadCodexModelsCache parses available slugs", async () => {
  await withTempState(async ({ codexHome }) => {
    await writeModelsCache(codexHome, ["gpt-5.3-codex-spark", "gpt-5.5", "codex-auto-review"]);
    const result = await loadCodexModelsCache({ CODEX_HOME: codexHome });
    assert.equal(result.present, true);
    assert.equal(result.slugs.size, 3);
    assert.equal(result.slugs.has("gpt-5.3-codex-spark"), true);
    assert.equal(result.slugs.has("gpt-5.5"), true);
  });
});

test("resolveRecallCompressorProfile picks first candidate present in cache", async () => {
  await withTempState(async ({ stateDir, codexHome }) => {
    await writeModelsCache(codexHome, ["gpt-5.5", "codex-auto-review"]);
    const profile = await resolveRecallCompressorProfile(
      baseCfg(),
      {},
      { CODEX_HOME: codexHome },
    );
    assert.equal(profile.enabled, true);
    assert.equal(profile.model, "gpt-5.5");
    assert.equal(profile.detected, true);
    const persisted = JSON.parse(
      await readFile(join(stateDir, "recall-compressor-profile.json"), "utf-8"),
    );
    assert.equal(persisted.profile.model, "gpt-5.5");
  });
});

test("resolveRecallCompressorProfile honors configured candidate first when present", async () => {
  await withTempState(async ({ codexHome }) => {
    await writeModelsCache(codexHome, ["gpt-5.5", "gpt-5.3-codex-spark"]);
    const profile = await resolveRecallCompressorProfile(
      baseCfg({
        recallCompressModel: "gpt-5.5",
        recallCompressThinking: "low",
        recallCompressConfigured: true,
      }),
      {},
      { CODEX_HOME: codexHome },
    );
    assert.equal(profile.enabled, true);
    assert.equal(profile.model, "gpt-5.5");
    assert.equal(profile.thinking, "low");
    assert.equal(profile.source, "configured");
    assert.equal(profile.detected, true);
  });
});

test("resolveRecallCompressorProfile falls back optimistically when cache missing", async () => {
  await withTempState(async ({ codexHome }) => {
    const profile = await resolveRecallCompressorProfile(
      baseCfg(),
      {},
      { CODEX_HOME: codexHome },
    );
    assert.equal(profile.enabled, true);
    assert.equal(profile.model, "gpt-5.3-codex-spark");
    assert.equal(profile.detected, false);
  });
});

test("resolveRecallCompressorProfile disables when compress is configured off", async () => {
  await withTempState(async ({ codexHome }) => {
    await writeModelsCache(codexHome, ["gpt-5.5"]);
    const profile = await resolveRecallCompressorProfile(
      baseCfg({ recallCompress: false }),
      {},
      { CODEX_HOME: codexHome },
    );
    assert.equal(profile.enabled, false);
    assert.equal(profile.source, "configured_off");
  });
});

test("invalidateRecallCompressorProfileCache removes the persisted profile", async () => {
  await withTempState(async ({ stateDir, codexHome }) => {
    await writeModelsCache(codexHome, ["gpt-5.3-codex-spark"]);
    await resolveRecallCompressorProfile(baseCfg(), {}, { CODEX_HOME: codexHome });
    const path = join(stateDir, "recall-compressor-profile.json");
    await readFile(path, "utf-8"); // exists
    await invalidateRecallCompressorProfileCache();
    await assert.rejects(() => readFile(path, "utf-8"), /ENOENT/);
  });
});

test("detectRecallCompressorProfile prefers cached profile (no probe even when cache exists)", async () => {
  await withTempState(async ({ stateDir, codexHome }) => {
    // Seed cache with a different model than what the resolver would pick now.
    await writeModelsCache(codexHome, ["gpt-5.3-codex-spark"]);
    await resolveRecallCompressorProfile(baseCfg(), {}, { CODEX_HOME: codexHome });

    // Change the catalogue so the resolver would pick something else if it
    // ran again; cache-first detect should NOT re-resolve.
    await writeModelsCache(codexHome, ["gpt-5.5"]);

    const profile = await detectRecallCompressorProfile(
      baseCfg(),
      {},
      { CODEX_HOME: codexHome },
    );
    assert.equal(profile.model, "gpt-5.3-codex-spark");
    // Persisted profile is unchanged.
    const persisted = JSON.parse(
      await readFile(join(stateDir, "recall-compressor-profile.json"), "utf-8"),
    );
    assert.equal(persisted.profile.model, "gpt-5.3-codex-spark");
  });
});

test("detectRecallCompressorProfile resolves on cache miss", async () => {
  await withTempState(async ({ codexHome }) => {
    await writeModelsCache(codexHome, ["gpt-5.5"]);
    const profile = await detectRecallCompressorProfile(
      baseCfg(),
      {},
      { CODEX_HOME: codexHome },
    );
    assert.equal(profile.enabled, true);
    assert.equal(profile.model, "gpt-5.5");
  });
});

test("detectRecallCompressorProfile after invalidate re-resolves against current catalogue", async () => {
  await withTempState(async ({ codexHome }) => {
    await writeModelsCache(codexHome, ["gpt-5.3-codex-spark", "gpt-5.5"]);
    const initial = await detectRecallCompressorProfile(
      baseCfg(),
      {},
      { CODEX_HOME: codexHome },
    );
    assert.equal(initial.model, "gpt-5.3-codex-spark");

    // Simulate runtime compress failure → invalidate cache, then drop the
    // failing slug from the catalogue. Next detect should pick the next one.
    await invalidateRecallCompressorProfileCache();
    await writeModelsCache(codexHome, ["gpt-5.5"]);

    const next = await detectRecallCompressorProfile(
      baseCfg(),
      {},
      { CODEX_HOME: codexHome },
    );
    assert.equal(next.model, "gpt-5.5");
    assert.equal(next.detected, true);
  });
});

test("detectRecallCompressorProfile with detect_on_startup=false returns null on cache miss", async () => {
  await withTempState(async ({ codexHome }) => {
    await writeModelsCache(codexHome, ["gpt-5.5"]);
    const profile = await detectRecallCompressorProfile(
      baseCfg({ recallCompressDetectOnStartup: false }),
      {},
      { CODEX_HOME: codexHome },
    );
    assert.equal(profile, null);
  });
});

test("markRecallCompressorRuntimeFailed writes a disabled profile cached for UPS skip", async () => {
  await withTempState(async ({ codexHome }) => {
    await writeModelsCache(codexHome, ["gpt-5.5"]);
    await resolveRecallCompressorProfile(baseCfg(), {}, { CODEX_HOME: codexHome });

    await markRecallCompressorRuntimeFailed(baseCfg(), { failedModel: "gpt-5.3-codex-spark" });
    const cached = await loadCachedRecallCompressorProfile(baseCfg());
    assert.ok(cached, "expected cached profile to exist");
    assert.equal(cached.enabled, false);
    assert.equal(cached.source, "runtime_failed");
    assert.equal(cached.failedModel, "gpt-5.3-codex-spark");
  });
});

test("detectRecallCompressorProfile recovers across SessionStart after runtime_failed marker", async () => {
  await withTempState(async ({ codexHome }) => {
    // Initial healthy resolve.
    await writeModelsCache(codexHome, ["gpt-5.3-codex-spark", "gpt-5.5"]);
    const initial = await detectRecallCompressorProfile(baseCfg(), {}, { CODEX_HOME: codexHome });
    assert.equal(initial.model, "gpt-5.3-codex-spark");

    // Runtime compress failure marks the cache disabled. UPS within the
    // same session would now read this disabled profile and skip compress.
    await markRecallCompressorRuntimeFailed(baseCfg(), { failedModel: "gpt-5.3-codex-spark" });
    const within = await loadCachedRecallCompressorProfile(baseCfg());
    assert.equal(within.enabled, false);

    // Next codex SessionStart: catalogue shrinks (the failed slug went
    // away). Detect should treat runtime_failed as cache miss and
    // re-resolve against the current catalogue, picking gpt-5.5.
    await writeModelsCache(codexHome, ["gpt-5.5"]);
    const recovered = await detectRecallCompressorProfile(baseCfg(), {}, { CODEX_HOME: codexHome });
    assert.equal(recovered.enabled, true);
    assert.equal(recovered.model, "gpt-5.5");
  });
});

test("detectRecallCompressorProfile with detect_on_startup=false honors runtime_failed marker", async () => {
  await withTempState(async ({ codexHome }) => {
    await writeModelsCache(codexHome, ["gpt-5.5"]);
    await resolveRecallCompressorProfile(baseCfg(), {}, { CODEX_HOME: codexHome });
    await markRecallCompressorRuntimeFailed(baseCfg(), { failedModel: "gpt-5.5" });

    const profile = await detectRecallCompressorProfile(
      baseCfg({ recallCompressDetectOnStartup: false }),
      {},
      { CODEX_HOME: codexHome },
    );
    // With detect-on-startup disabled, we respect the marker (don't auto-
    // recover); UPS keeps skipping compress until manual intervention.
    assert.ok(profile);
    assert.equal(profile.enabled, false);
    assert.equal(profile.source, "runtime_failed");
  });
});
