import assert from "node:assert/strict";
import test from "node:test";

import { contextRequestTimeoutMs } from "../shared/recall-core.mjs";

test("explicit recall context timeout applies without rewrite or expansion", () => {
  const timeout = contextRequestTimeoutMs(
    { recallContextTimeoutMs: 25000, timeoutMs: 15000 },
    { session_id: "s1", query_expansion: "off" },
  );

  assert.equal(timeout, 25000);
});

