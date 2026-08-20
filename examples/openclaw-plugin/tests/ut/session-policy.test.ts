import { describe, expect, it, vi } from "vitest";

import { OpenVikingClient } from "../../client.js";
import {
  applyOpenVikingSessionPolicy,
  buildIdleAutoCommitPolicy,
  normalizeIdleTimeoutSeconds,
} from "../../plugin/openviking-session-policy.js";

function response(status: number, value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("OpenClaw session auto-commit policy", () => {
  it("normalizes config values and builds the idle-only policy", () => {
    expect(normalizeIdleTimeoutSeconds("off")).toBe(0);
    expect(normalizeIdleTimeoutSeconds(9999999)).toBe(604800);
    expect(buildIdleAutoCommitPolicy(3600)).toEqual({
      idle_timeout_seconds: 3600,
      pending_token_threshold: 0,
      message_count_threshold: 0,
    });
  });

  it("serializes create and PATCH config bodies", async () => {
    const transport = vi.fn()
      .mockResolvedValueOnce(response(200, {
        status: "ok",
        result: {
          session_id: "oc-policy",
          auto_commit_policy: buildIdleAutoCommitPolicy(3600),
          auto_commit_idle_enabled: true,
        },
      }))
      .mockResolvedValueOnce(response(200, {
        status: "ok",
        result: {
          session_id: "oc-policy",
          auto_commit_policy: buildIdleAutoCommitPolicy(7200),
          auto_commit_idle_enabled: true,
        },
      }));
    const client = new OpenVikingClient(
      "http://127.0.0.1:1933",
      "",
      "agent",
      5000,
      "",
      "",
      undefined,
      { transport },
    );

    await client.createSession("oc-policy", {
      autoCommitPolicy: buildIdleAutoCommitPolicy(3600)!,
    });
    await client.updateSessionConfig(
      "oc-policy",
      buildIdleAutoCommitPolicy(7200)!,
    );

    expect(transport.mock.calls[0]![0]).toBe(
      "http://127.0.0.1:1933/api/v1/sessions",
    );
    expect(JSON.parse(String(transport.mock.calls[0]![1].body))).toEqual({
      session_id: "oc-policy",
      auto_commit_policy: buildIdleAutoCommitPolicy(3600),
    });
    expect(transport.mock.calls[1]![0]).toBe(
      "http://127.0.0.1:1933/api/v1/sessions/oc-policy/config",
    );
    expect(transport.mock.calls[1]![1].method).toBe("PATCH");
  });

  it("falls back from ALREADY_EXISTS to PATCH", async () => {
    const transport = vi.fn()
      .mockResolvedValueOnce(response(409, {
        status: "error",
        error: { code: "ALREADY_EXISTS", message: "exists" },
      }))
      .mockResolvedValueOnce(response(200, {
        status: "ok",
        result: {
          session_id: "oc-existing",
          auto_commit_policy: buildIdleAutoCommitPolicy(3600),
          auto_commit_idle_enabled: false,
        },
      }));
    const client = new OpenVikingClient(
      "http://127.0.0.1:1933",
      "",
      "agent",
      5000,
      "",
      "",
      undefined,
      { transport },
    );

    const result = await applyOpenVikingSessionPolicy(
      client,
      "oc-existing",
      3600,
    );

    expect(result).toMatchObject({
      ensured: true,
      applied: true,
      idleActive: false,
      method: "patch",
    });
    expect(transport).toHaveBeenCalledTimes(2);
  });

  it("retries create without the policy on legacy validation errors", async () => {
    const transport = vi.fn()
      .mockResolvedValueOnce(response(422, {
        status: "error",
        error: { code: "VALIDATION_ERROR", message: "unknown field" },
      }))
      .mockResolvedValueOnce(response(200, {
        status: "ok",
        result: { session_id: "oc-legacy" },
      }));
    const client = new OpenVikingClient(
      "http://127.0.0.1:1933",
      "",
      "agent",
      5000,
      "",
      "",
      undefined,
      { transport },
    );

    const result = await applyOpenVikingSessionPolicy(
      client,
      "oc-legacy",
      3600,
    );

    expect(result).toMatchObject({
      ensured: true,
      applied: false,
      method: "create-legacy",
    });
    expect(JSON.parse(String(transport.mock.calls[1]![1].body))).toEqual({
      session_id: "oc-legacy",
    });
  });
});
