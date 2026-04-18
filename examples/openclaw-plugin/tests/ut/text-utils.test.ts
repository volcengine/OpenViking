import { describe, expect, it } from "vitest";

import {
  sanitizeUserTextForCapture,
  getCaptureDecision,
  extractNewTurnTexts,
  extractLatestUserText,
  pickRecentUniqueTexts,
  looksLikeQuestionOnlyText,
} from "../../text-utils.js";

describe("sanitizeUserTextForCapture", () => {
  it("strips <relevant-memories> blocks", () => {
    const input = "hello <relevant-memories>some injected context</relevant-memories> world";
    const result = sanitizeUserTextForCapture(input);
    expect(result).not.toContain("relevant-memories");
    expect(result).toContain("hello");
    expect(result).toContain("world");
  });

  it("strips metadata JSON blocks with ≥3 metadata keys", () => {
    const input = 'prefix ```json\n{"session":"s1","userId":"u1","agentId":"a1","channel":"ch"}\n``` suffix';
    const result = sanitizeUserTextForCapture(input);
    expect(result).not.toContain("session");
    expect(result).not.toContain("userId");
    expect(result).toContain("prefix");
    expect(result).toContain("suffix");
  });

  it("preserves normal JSON code blocks without metadata keys", () => {
    const input = '```json\n{"name":"Alice","age":30}\n```';
    const result = sanitizeUserTextForCapture(input);
    expect(result).toContain("Alice");
    expect(result).toContain("age");
  });

  it("strips Sender metadata blocks", () => {
    const input = 'hello Sender (untrusted metadata): ```json\n{"session":"x"}\n``` world';
    const result = sanitizeUserTextForCapture(input);
    expect(result).not.toContain("Sender");
    expect(result).toContain("hello");
    expect(result).toContain("world");
  });

  it("returns empty string for empty input", () => {
    expect(sanitizeUserTextForCapture("")).toBe("");
    expect(sanitizeUserTextForCapture("   ")).toBe("");
  });

  it("strips leading timestamp prefix", () => {
    const input = "[2026-03-29T10:00:00Z] actual content here";
    const result = sanitizeUserTextForCapture(input);
    expect(result).toBe("actual content here");
  });

  it("collapses multiple whitespace into single space", () => {
    const input = "hello    world\n\n\tthere";
    const result = sanitizeUserTextForCapture(input);
    expect(result).toBe("hello world there");
  });
});

describe("getCaptureDecision", () => {
  it("semantic mode: normal text → shouldCapture=true", () => {
    const result = getCaptureDecision("我喜欢用 Python 写代码，平时也用 Go", "semantic", 24000);
    expect(result.shouldCapture).toBe(true);
    expect(result.reason).toContain("semantic");
  });

  it("keyword mode: no trigger word → shouldCapture=false", () => {
    const result = getCaptureDecision("今天天气不错啊，适合出去走走散步放松一下心情", "keyword", 24000);
    expect(result.shouldCapture).toBe(false);
  });

  it("keyword mode: '记住' trigger → shouldCapture=true", () => {
    const result = getCaptureDecision("记住我的名字叫张三，我是工程师", "keyword", 24000);
    expect(result.shouldCapture).toBe(true);
    expect(result.reason).toContain("matched_trigger");
  });

  it("question-only text → shouldCapture=false", () => {
    const result = getCaptureDecision("这是什么？", "semantic", 24000);
    expect(result.shouldCapture).toBe(false);
    expect(result.reason).toBe("question_text");
  });

  it("command text → shouldCapture=false", () => {
    const result = getCaptureDecision("/help clear all the sessions and reset everything now", "semantic", 24000);
    expect(result.shouldCapture).toBe(false);
    expect(result.reason).toBe("command_text");
  });

  it("text exceeding captureMaxLength → shouldCapture=false", () => {
    const longText = "a".repeat(25000);
    const result = getCaptureDecision(longText, "semantic", 24000);
    expect(result.shouldCapture).toBe(false);
    expect(result.reason).toBe("length_out_of_range");
  });

  it("empty text → shouldCapture=false", () => {
    const result = getCaptureDecision("", "semantic", 24000);
    expect(result.shouldCapture).toBe(false);
    expect(result.reason).toBe("empty_text");
  });

  it("very short text (below minLength) → shouldCapture=false", () => {
    const result = getCaptureDecision("ok", "semantic", 24000);
    expect(result.shouldCapture).toBe(false);
    expect(result.reason).toBe("length_out_of_range");
  });

  it("Subagent context prefix is stripped by sanitization (captured as semantic)", () => {
    // "[Subagent Context]" matches LEADING_TIMESTAMP_PREFIX_RE and is stripped during sanitization.
    // After sanitization the remaining text is a valid semantic candidate.
    const result = getCaptureDecision("[Subagent Context] some context data here for the subagent", "semantic", 24000);
    expect(result.shouldCapture).toBe(true);
    expect(result.reason).toContain("semantic");
  });

  it("non-content text (only punctuation/symbols) → shouldCapture=false", () => {
    // Needs enough punctuation chars to pass the minLength check (≥10 for non-CJK)
    const result = getCaptureDecision("!!! --- ??? *** +++ === ~~~", "semantic", 24000);
    expect(result.shouldCapture).toBe(false);
    expect(result.reason).toBe("non_content_text");
  });

  it("relevant-memories only → shouldCapture=false with injected reason", () => {
    const result = getCaptureDecision("<relevant-memories>some memory</relevant-memories>", "semantic", 24000);
    expect(result.shouldCapture).toBe(false);
    expect(result.reason).toBe("injected_memory_context_only");
  });
});

describe("extractNewTurnTexts", () => {
  it("extracts user + assistant text messages", () => {
    const messages = [
      { role: "user", content: "hi there" },
      { role: "assistant", content: [{ type: "text", text: "hello back" }] },
    ];
    const { texts, newCount } = extractNewTurnTexts(messages, 0);
    expect(newCount).toBe(2);
    expect(texts).toContain("[user]: hi there");
    expect(texts).toContain("[assistant]: hello back");
  });

  it("skips system messages", () => {
    const messages = [
      { role: "system", content: "you are a helpful assistant" },
      { role: "user", content: "hello" },
    ];
    const { texts, newCount } = extractNewTurnTexts(messages, 0);
    expect(newCount).toBe(1);
    expect(texts).toHaveLength(1);
    expect(texts[0]).toContain("[user]: hello");
  });

  it("preserves toolUse content from assistant", () => {
    const messages = [
      {
        role: "assistant",
        content: [
          { type: "text", text: "Let me search" },
          { type: "toolUse", name: "grep", input: { pattern: "TODO" } },
        ],
      },
    ];
    const { texts } = extractNewTurnTexts(messages, 0);
    expect(texts.some((t) => t.includes("[toolUse: grep]"))).toBe(true);
    expect(texts.some((t) => t.includes("TODO"))).toBe(true);
  });

  it("formats toolResult messages", () => {
    const messages = [
      {
        role: "toolResult",
        toolName: "grep",
        content: [{ type: "text", text: "found 3 matches" }],
      },
    ];
    const { texts } = extractNewTurnTexts(messages, 0);
    expect(texts).toHaveLength(1);
    expect(texts[0]).toContain("[grep result]");
    expect(texts[0]).toContain("found 3 matches");
  });

  it("respects startIndex parameter", () => {
    const messages = [
      { role: "user", content: "old message" },
      { role: "assistant", content: "old reply" },
      { role: "user", content: "new message" },
    ];
    const { texts, newCount } = extractNewTurnTexts(messages, 2);
    expect(newCount).toBe(1);
    expect(texts).toHaveLength(1);
    expect(texts[0]).toContain("new message");
  });

  it("handles assistant string content", () => {
    const messages = [
      { role: "assistant", content: "simple text reply" },
    ];
    const { texts } = extractNewTurnTexts(messages, 0);
    expect(texts).toHaveLength(1);
    expect(texts[0]).toContain("[assistant]: simple text reply");
  });

  it("handles toolResult with string content", () => {
    const messages = [
      { role: "toolResult", toolName: "bash", content: "exit code 0" },
    ];
    const { texts } = extractNewTurnTexts(messages, 0);
    expect(texts).toHaveLength(1);
    expect(texts[0]).toContain("[bash result]: exit code 0");
  });
});

describe("extractLatestUserText", () => {
  it("returns last user text from messages", () => {
    const messages = [
      { role: "user", content: "first question" },
      { role: "assistant", content: "answer" },
      { role: "user", content: "second question" },
    ];
    expect(extractLatestUserText(messages)).toBe("second question");
  });

  it("returns empty string for empty array", () => {
    expect(extractLatestUserText([])).toBe("");
  });

  it("returns empty string for undefined", () => {
    expect(extractLatestUserText(undefined)).toBe("");
  });

  it("skips sanitized-empty user messages", () => {
    const messages = [
      { role: "user", content: "real content here" },
      { role: "user", content: "<relevant-memories>only memory</relevant-memories>" },
    ];
    expect(extractLatestUserText(messages)).toBe("real content here");
  });
});

describe("pickRecentUniqueTexts", () => {
  it("deduplicates and preserves recent-first order", () => {
    const result = pickRecentUniqueTexts(["a", "b", "a", "c"], 3);
    expect(result).toEqual(["b", "a", "c"]);
  });

  it("respects limit", () => {
    const result = pickRecentUniqueTexts(["a", "b", "c", "d", "e"], 2);
    expect(result).toHaveLength(2);
  });

  it("returns empty for empty input", () => {
    expect(pickRecentUniqueTexts([], 5)).toEqual([]);
  });

  it("returns empty for limit=0", () => {
    expect(pickRecentUniqueTexts(["a", "b"], 0)).toEqual([]);
  });
});

describe("looksLikeQuestionOnlyText", () => {
  it("pure question → true", () => {
    expect(looksLikeQuestionOnlyText("what is this?")).toBe(true);
  });

  it("question with memory intent → false", () => {
    expect(looksLikeQuestionOnlyText("记住这个重要的事情，好吗？")).toBe(false);
  });

  it("long text with question mark → false (exceeds 280 chars)", () => {
    const longText = "a".repeat(300) + "?";
    expect(looksLikeQuestionOnlyText(longText)).toBe(false);
  });

  it("multi-speaker text with question → false", () => {
    const text = "Alice: what do you think?\nBob: I think it's fine";
    expect(looksLikeQuestionOnlyText(text)).toBe(false);
  });

  it("text without question cue → false", () => {
    // "is" matches QUESTION_CUE_RE, so use text without any question cue words
    expect(looksLikeQuestionOnlyText("hello world good morning")).toBe(false);
  });
});
