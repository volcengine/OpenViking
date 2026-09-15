import assert from "node:assert/strict";
import test from "node:test";
import { resolveConfig } from "./config.mjs";

test("attribution tags default to empty (feature off)", () => {
  const config = resolveConfig({}, {
    OPENVIKING_URL: "http://127.0.0.1:19464/",
  }, "/workspace/project");

  assert.equal(config.attributionTags, "");
});

test("attribution tags opt in via environment", () => {
  const config = resolveConfig({}, {
    OPENVIKING_URL: "http://127.0.0.1:19464/",
    OPENVIKING_ATTRIBUTION_TAGS: "agent=dsh",
  }, "/workspace/project");

  assert.equal(config.attributionTags, "agent=dsh");
});
