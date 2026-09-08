const APPROVAL_FINGERPRINT_RE = /(?:指纹|fingerprint)\s*[:：]?\s*[a-f0-9]{64}\b/i;
const APPROVAL_PREFIX_RE = /^(?:批准|同意|approve(?:d)?)(?:\s|[,:，：])/iu;

const ACKNOWLEDGEMENT_RE = /^(?:ok(?:ay)?|yes|yep|sure|confirmed?|approved?|sounds good|lgtm|好|好的|可以|没问题|确认|同意|批准|行|收到|就这样)[。.!！]?$/iu;
const CONTINUATION_RE = /^(?:continue|proceed|go ahead|keep going|do it|start|继续|继续执行|接着做|开始吧|执行吧|按计划继续|就按这个做|按这个做)[。.!！]?$/iu;
const STATUS_RE = /^(?:status|progress|(?:any|status) update|what(?:'s| is) the (?:current )?(?:status|progress)|is it done|are we done|进度|现在进度怎么样|进度怎么样|什么进度|现在什么进度|汇报(?:一下)?进度|完成了吗|整体完成了吗|现在整体完成了吗)[?？。.!！]?$/iu;

/**
 * Classify prompts whose meaning is entirely carried by the active turn state.
 * These prompts should not trigger external memory retrieval: the model already
 * has the approval, continuation, or status request in the conversation.
 *
 * The match is intentionally strict. Any extra task noun, constraint, or
 * implementation detail falls through to normal recall.
 */
export function classifyControlPrompt(prompt) {
  const text = String(prompt || "").normalize("NFKC").replace(/\s+/g, " ").trim();
  if (!text || text.length > 512) return null;
  if (APPROVAL_PREFIX_RE.test(text) && APPROVAL_FINGERPRINT_RE.test(text)) return "approval";
  if (ACKNOWLEDGEMENT_RE.test(text)) return "acknowledgement";
  if (CONTINUATION_RE.test(text)) return "continuation";
  if (STATUS_RE.test(text)) return "status";
  return null;
}
