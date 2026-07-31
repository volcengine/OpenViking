import { describe, expect, it } from "vitest";

import {
  cleanOpenVikingRequestHeaders,
  resolveOpenVikingRequestHeaders,
} from "../../request-headers.js";

describe("OpenViking request headers", () => {
  it("preserves string-valued headers exactly", () => {
    expect(cleanOpenVikingRequestHeaders({
      openviking: " i18n-instance ",
      empty: "",
    })).toEqual({
      openviking: " i18n-instance ",
      empty: "",
    });
  });

  it("resolves configured OpenViking routing headers", () => {
    expect(resolveOpenVikingRequestHeaders({
      headers: {
        openviking: "i18n_bi_claw_1781078526__bi_claw_openviking",
        region: "SG",
      },
    })).toEqual({
      openviking: "i18n_bi_claw_1781078526__bi_claw_openviking",
      region: "SG",
    });
  });

  it("does not synthesize auth headers", () => {
    expect(resolveOpenVikingRequestHeaders()).toEqual({});
  });

  it("keeps explicitly configured auth-like headers when provided", () => {
    expect(resolveOpenVikingRequestHeaders({
      headers: {
        token: "explicit-token",
        "X-API-Key": "explicit-key",
      },
    })).toEqual({
      token: "explicit-token",
      "X-API-Key": "explicit-key",
    });
  });

  it("rejects non-string header values", () => {
    expect(() => resolveOpenVikingRequestHeaders({
      headers: { openviking: 123 },
    })).toThrow("openviking request header openviking must be a string");
  });
});
