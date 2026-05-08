import { describe, expect, it } from "vitest";
import {
  canonicaliseTranscript,
  fromCaptureToolArgs,
  fromVSCodeChatHistory,
  type CanonicalTurnInput,
  type TranscriptOptions,
  type VSCodeChatTurnLike,
} from "../capture/transcript.js";

const PERMISSIVE: TranscriptOptions = {
  captureAssistantTurns: true,
  captureMaxLength: 100_000,
};

describe("canonicaliseTranscript — sanitisation", () => {
  it("strips injected blocks while preserving whitespace and newlines", () => {
    const turns: CanonicalTurnInput[] = [
      {
        role: "user",
        text: "<openviking-context>recall</openviking-context>\nReal user message\nWith newlines",
      },
    ];
    const out = canonicaliseTranscript(turns, PERMISSIVE);
    expect(out).toHaveLength(1);
    expect(out[0]!.role).toBe("user");
    expect(out[0]!.content).not.toContain("openviking-context");
    expect(out[0]!.content).toContain("Real user message");
    expect(out[0]!.content).toContain("With newlines");
  });

  it("strips system reminders, copilot-context, subagent context lines", () => {
    const turns: CanonicalTurnInput[] = [
      {
        role: "assistant",
        text: "<system-reminder>x</system-reminder>\n<copilot-context>y</copilot-context>\n[Subagent Context] meta\nOnly this survives.",
      },
    ];
    const out = canonicaliseTranscript(turns, PERMISSIVE);
    expect(out).toHaveLength(1);
    expect(out[0]!.content).not.toMatch(/system-reminder|copilot-context|Subagent Context/);
    expect(out[0]!.content).toContain("Only this survives.");
  });
});

describe("canonicaliseTranscript — empty / whitespace handling", () => {
  it("drops turns whose sanitized text is empty (block-only input)", () => {
    const turns: CanonicalTurnInput[] = [
      { role: "user", text: "<openviking-context>only this</openviking-context>" },
    ];
    expect(canonicaliseTranscript(turns, PERMISSIVE)).toEqual([]);
  });

  it("drops turns whose text is just whitespace after sanitising", () => {
    const turns: CanonicalTurnInput[] = [
      { role: "user", text: "   \n\n  " },
      { role: "assistant", text: "" },
    ];
    expect(canonicaliseTranscript(turns, PERMISSIVE)).toEqual([]);
  });

  it("treats an undefined text field defensively (drops the turn)", () => {
    const turns: CanonicalTurnInput[] = [
      { role: "user", text: undefined as unknown as string },
    ];
    expect(canonicaliseTranscript(turns, PERMISSIVE)).toEqual([]);
  });
});

describe("canonicaliseTranscript — captureAssistantTurns", () => {
  it("keeps both roles when captureAssistantTurns=true", () => {
    const turns: CanonicalTurnInput[] = [
      { role: "user", text: "Hi" },
      { role: "assistant", text: "Hello back" },
    ];
    const out = canonicaliseTranscript(turns, { ...PERMISSIVE, captureAssistantTurns: true });
    expect(out.map((t) => t.role)).toEqual(["user", "assistant"]);
  });

  it("drops assistant turns when captureAssistantTurns=false (user-only mode)", () => {
    const turns: CanonicalTurnInput[] = [
      { role: "user", text: "Hi" },
      { role: "assistant", text: "Hello back" },
      { role: "user", text: "Follow-up" },
    ];
    const out = canonicaliseTranscript(turns, { ...PERMISSIVE, captureAssistantTurns: false });
    expect(out.map((t) => t.role)).toEqual(["user", "user"]);
    expect(out.map((t) => t.content)).toEqual(["Hi", "Follow-up"]);
  });
});

describe("canonicaliseTranscript — captureMaxLength rejection", () => {
  it("drops turns whose sanitized text length exceeds captureMaxLength", () => {
    const turns: CanonicalTurnInput[] = [
      { role: "user", text: "short" },
      { role: "assistant", text: "x".repeat(50) },
    ];
    const out = canonicaliseTranscript(turns, {
      captureAssistantTurns: true,
      captureMaxLength: 10,
    });
    expect(out).toHaveLength(1);
    expect(out[0]!.role).toBe("user");
  });

  it("captureMaxLength=0 disables the length cap (mirrors floor at 0)", () => {
    const turns: CanonicalTurnInput[] = [
      { role: "user", text: "x".repeat(5000) },
    ];
    const out = canonicaliseTranscript(turns, {
      captureAssistantTurns: true,
      captureMaxLength: 0,
    });
    expect(out).toHaveLength(1);
  });

  it("a turn exactly at captureMaxLength is kept, not dropped", () => {
    const turns: CanonicalTurnInput[] = [
      { role: "user", text: "x".repeat(10) },
    ];
    const out = canonicaliseTranscript(turns, {
      captureAssistantTurns: true,
      captureMaxLength: 10,
    });
    expect(out).toHaveLength(1);
  });
});

describe("canonicaliseTranscript — order + immutability", () => {
  it("preserves input order across the surviving turns", () => {
    const turns: CanonicalTurnInput[] = [
      { role: "user", text: "first" },
      { role: "assistant", text: "" }, // dropped (empty)
      { role: "user", text: "<openviking-context>x</openviking-context>" }, // dropped
      { role: "assistant", text: "second" },
      { role: "user", text: "third" },
    ];
    const out = canonicaliseTranscript(turns, PERMISSIVE);
    expect(out.map((t) => t.content)).toEqual(["first", "second", "third"]);
  });

  it("does not mutate the input array", () => {
    const turns: CanonicalTurnInput[] = [{ role: "user", text: "ok" }];
    const snapshot = JSON.stringify(turns);
    canonicaliseTranscript(turns, PERMISSIVE);
    expect(JSON.stringify(turns)).toBe(snapshot);
  });
});

describe("fromVSCodeChatHistory", () => {
  it("discriminates ChatRequestTurn (prompt) from ChatResponseTurn (response)", () => {
    const history: VSCodeChatTurnLike[] = [
      { prompt: "user prompt 1" },
      { response: "assistant reply 1" },
      { prompt: "user prompt 2" },
    ];
    const out = fromVSCodeChatHistory(history, PERMISSIVE);
    expect(out).toEqual([
      { role: "user", content: "user prompt 1" },
      { role: "assistant", content: "assistant reply 1" },
      { role: "user", content: "user prompt 2" },
    ]);
  });

  it("skips unrecognised turn shapes silently (forward-compatible)", () => {
    const history = [
      { prompt: "ok" },
      { unknown: "shape" } as unknown as VSCodeChatTurnLike,
      { response: "also ok" },
    ];
    const out = fromVSCodeChatHistory(history, PERMISSIVE);
    expect(out).toHaveLength(2);
    expect(out.map((t) => t.role)).toEqual(["user", "assistant"]);
  });

  it("filters assistant turns when captureAssistantTurns=false", () => {
    const history: VSCodeChatTurnLike[] = [
      { prompt: "u" },
      { response: "a" },
      { prompt: "u2" },
    ];
    const out = fromVSCodeChatHistory(history, { ...PERMISSIVE, captureAssistantTurns: false });
    expect(out.map((t) => t.role)).toEqual(["user", "user"]);
  });

  it("strips injected blocks from each turn before storing", () => {
    const history: VSCodeChatTurnLike[] = [
      { prompt: "<openviking-context>recall</openviking-context>\nReal prompt" },
      { response: "Real reply\n<system-reminder>internal</system-reminder>" },
    ];
    const out = fromVSCodeChatHistory(history, PERMISSIVE);
    expect(out[0]!.content).toContain("Real prompt");
    expect(out[0]!.content).not.toContain("openviking-context");
    expect(out[1]!.content).toContain("Real reply");
    expect(out[1]!.content).not.toContain("system-reminder");
  });
});

describe("fromCaptureToolArgs", () => {
  it("produces a single user turn when only `user` is supplied", () => {
    const out = fromCaptureToolArgs({ user: "hello" }, PERMISSIVE);
    expect(out).toEqual([{ role: "user", content: "hello" }]);
  });

  it("produces user + assistant turns when both are supplied", () => {
    const out = fromCaptureToolArgs({ user: "u", assistant: "a" }, PERMISSIVE);
    expect(out).toEqual([
      { role: "user", content: "u" },
      { role: "assistant", content: "a" },
    ]);
  });

  it("drops the assistant turn when captureAssistantTurns=false", () => {
    const out = fromCaptureToolArgs(
      { user: "u", assistant: "a" },
      { ...PERMISSIVE, captureAssistantTurns: false },
    );
    expect(out).toEqual([{ role: "user", content: "u" }]);
  });

  it("drops empty assistant when supplied as empty string", () => {
    const out = fromCaptureToolArgs({ user: "u", assistant: "" }, PERMISSIVE);
    expect(out).toEqual([{ role: "user", content: "u" }]);
  });

  it("strips injected blocks from both fields", () => {
    const out = fromCaptureToolArgs(
      {
        user: "<openviking-context>recall</openviking-context>real user",
        assistant: "real reply <system-reminder>x</system-reminder>",
      },
      PERMISSIVE,
    );
    expect(out[0]!.content).not.toContain("openviking-context");
    expect(out[0]!.content).toContain("real user");
    expect(out[1]!.content).not.toContain("system-reminder");
    expect(out[1]!.content).toContain("real reply");
  });
});
