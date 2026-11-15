/**
 * The `UserPromptSubmit` recall path.
 *
 * Three separate things happen on a user's first and later prompts, and the
 * design keeps their switches independent because users expect them to be:
 *
 *   profile + skill catalog   `noAutoInject` (and only on the first success)
 *   per-turn recall           `autoRecall`, `minQueryLength`
 *
 * `noAutoInject` must not stop recall, and `autoRecall=false` must not stop the
 * profile: both have been reported as bugs in hosts that fused the two.
 *
 * Everything here fails open. A missing server, a timeout, a malformed answer
 * or an empty result produces no text at all — never an error message in the
 * user's prompt, and never a blocked turn.
 */

import { buildProfileBlockDetailed } from "./shared/profile-inject.mjs";
import { buildRecallBlockDetailed } from "./shared/recall-core.mjs";
import { applyInputFilters, compileInputFilters } from "./shared/input-filters.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";
import { stripWrapperTags } from "./envelope.mjs";

/** The shortest window in which an identical query may reuse its answer. */
export const RECALL_CACHE_MS = 500;

/** A block larger than this is not worth storing in state. */
export const RECALL_CACHE_MAX_BYTES = 8 * 1024;

/** Two failures pause recall for this long; a third extends it. */
export const RECALL_BACKOFF_FIRST_MS = 60_000;
export const RECALL_BACKOFF_SECOND_MS = 300_000;

/** How many profile reads a session attempts before it stops asking. */
export const PROFILE_MAX_ATTEMPTS = 2;

/** How long to wait before the one retry. */
export const PROFILE_RETRY_DELAY_MS = 60_000;

/**
 * Did the server answer with "nothing relevant"?
 *
 * `buildRecallBlockDetailed` reports the path its empty block came from. These
 * two stages mean the request succeeded: the server had no matches, or the
 * score threshold dropped what it had. Every other empty stage — an error, an
 * unreachable server, an unusable answer — is a failure the breaker should
 * count.
 */
export function recallStageIsAnswer(stage) {
  return stage === "no_results" || stage === "filtered_out";
}

/**
 * The user's actual text from a ContentPart array.
 *
 * The host sends `prompt` as the parts the user submitted. Serializing the
 * array would search for JSON syntax, and taking only `[0]` would lose text a
 * paste split across parts, so every text part is joined in order. Media parts
 * carry no text and contribute nothing — this plugin does not read binaries.
 */
export function extractPromptText(prompt) {
  if (typeof prompt === "string") return prompt.trim();
  if (!Array.isArray(prompt)) return "";
  const chunks = [];
  for (const part of prompt) {
    if (typeof part === "string") {
      chunks.push(part);
      continue;
    }
    if (!part || typeof part !== "object") continue;
    if (part.type === "text" && typeof part.text === "string") chunks.push(part.text);
    // `think`, `image_url`, `audio_url`, `video_url` and tool parts are not
    // the user's question.
  }
  return chunks.join("\n").trim();
}

/** A stable hash of the query, for the short-lived reuse window. */
export function promptHash(text) {
  let hash = 2166136261;
  const value = String(text || "");
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.codePointAt(index);
    hash = Math.trunc(Math.imul(hash, 16777619));
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

/**
 * The recall circuit breaker.
 *
 * Two consecutive failures pause for a minute; a third (or any later one) for
 * five. A success resets the count, so a flaky server does not leave recall off
 * for the rest of the session.
 */
export function recallBreaker(state, now = Date.now()) {
  const until = Number(state?.recallFailureUntil) || 0;
  const failures = Number(state?.recallFailures) || 0;
  return {
    open: until > now,
    failures,
    until,
    retryInMs: until > now ? until - now : 0,
  };
}

/** The state after one recall attempt. */
export function noteRecallResult(state, ok, now = Date.now()) {
  if (ok) {
    return { ...state, recallFailures: 0, recallFailureUntil: 0 };
  }
  const failures = (Number(state?.recallFailures) || 0) + 1;
  // The design's curve: the second consecutive failure pauses for a minute, the
  // third and later for five. The first failure only counts.
  const pause = failures >= 3
    ? RECALL_BACKOFF_SECOND_MS
    : (failures === 2 ? RECALL_BACKOFF_FIRST_MS : 0);
  return {
    ...state,
    recallFailures: failures,
    recallFailureUntil: now + pause,
  };
}

/**
 * One prompt event's injection text.
 *
 * Returns `{ text, state, profileInjected, recalled, reasons }`. `text` is what
 * the entry prints — once, as plain stdout — or empty when there is nothing to
 * say. The reasons are for the log, because "no injection" has several very
 * different causes and only one of them is a bug.
 */
export async function runPromptInjection({
  cfg,
  state,
  cwd,
  promptText,
  ovSessionId,
  fetchJSON,
  log,
  deadline,
  now = Date.now(),
}) {
  const reasons = [];
  const blocks = [];
  let next = state;
  let profileInjected = state?.profileInjected === true;
  let recalled = false;

  // ---- profile and skill catalog -----------------------------------------
  if (cfg.noAutoInject) {
    reasons.push("no_auto_inject");
  } else if (profileInjected) {
    reasons.push("profile_already_injected");
  } else if (next.profileFailureUntil && next.profileFailureUntil > now) {
    reasons.push("profile_backoff");
  } else if (deadline - now > 0) {
    const peer = resolveEffectivePeerId({ cfg, cwd });
    const outcome = await buildProfileBlockDetailed(fetchJSON, cfg.profileTokenBudget, peer.peerId, cfg);
    if (outcome.block && !outcome.failed) {
      blocks.push(outcome.block);
      profileInjected = true;
      next = { ...next, profileInjected: true, profileFailures: 0, profileFailureUntil: 0 };
      log("profile", { injected: true, bytes: outcome.block.length });
    } else if (outcome.empty) {
      // The server answered and the user simply has no profile yet. That is a
      // finished attempt: retrying it on every prompt is the behaviour this
      // flag exists to prevent.
      profileInjected = true;
      next = { ...next, profileInjected: true, profileFailures: 0, profileFailureUntil: 0 };
      reasons.push("profile_empty");
      log("profile", { injected: false, empty: true });
    } else {
      // The read failed. One bounded retry, then a pause: an unreachable server
      // must not cost a request on every prompt for the rest of the session.
      const failures = (Number(next.profileFailures) || 0) + 1;
      const exhausted = failures >= PROFILE_MAX_ATTEMPTS;
      // A partial block is held back until the bounded retry is exhausted:
      // injecting it now and again on the retry would duplicate every section
      // that did arrive. On the last attempt the partial block is better than
      // nothing, so it is injected once and the session stops asking.
      const injectPartial = exhausted && Boolean(outcome.block);
      if (injectPartial) blocks.push(outcome.block);
      // Giving up is itself a completed attempt: the session stops asking.
      if (exhausted) profileInjected = true;
      next = {
        ...next,
        profileFailures: failures,
        profileInjected: exhausted,
        profileFailureUntil: exhausted ? 0 : now + PROFILE_RETRY_DELAY_MS,
      };
      reasons.push(exhausted ? "profile_failed_giving_up" : "profile_failed_retry");
      log("profile", {
        injected: injectPartial,
        failed: true,
        partial: Boolean(outcome.block),
        error: outcome.error,
        failures,
      });
    }
  }

  // ---- per-turn recall ---------------------------------------------------
  const minLength = Number(cfg.minQueryLength) || 0;
  // `recallQueryFilters` is a configured knob, so it has to have a consumer.
  // The filters run before the length gate, exactly as the other harnesses do:
  // a prompt whose only content was a stripped prefix is a short query, not a
  // search for the empty string.
  let queryText = promptText;
  let queryFiltered = false;
  const queryFilters = compileInputFilters(cfg.recallQueryFilters);
  if (queryFilters.rules.length) {
    const verdict = applyInputFilters(queryText, queryFilters.rules, { role: "user" });
    if (verdict.dropped) {
      queryFiltered = true;
      reasons.push("query_filtered");
      log("recall", { injected: false, reason: "query_filtered", rule: verdict.ruleIndex, op: verdict.op });
    } else {
      if (verdict.changed) log("query_filter", { rawLength: queryText.length, length: verdict.text.length });
      queryText = verdict.text;
    }
  }
  if (queryFilters.errors.length) log("query_filter_errors", { errors: queryFilters.errors });

  if (queryFiltered) {
    // The user's question was filtered out: no recall for this event. The
    // profile block above is unaffected, which is why this is not an early
    // return from the whole function.
  } else if (!cfg.autoRecall) {
    reasons.push("auto_recall_off");
  } else if (!queryText) {
    reasons.push("empty_prompt");
  } else if (queryText.length < minLength) {
    reasons.push("below_min_query_length");
  } else {
    const breaker = recallBreaker(next, now);
    if (breaker.open) {
      reasons.push("recall_paused");
    } else {
      const cached = reusableRecall(next.recallCache, queryText, now);
      if (cached) {
        blocks.push(cached.block);
        recalled = true;
        reasons.push("recall_cached");
      } else if (deadline - Date.now() > 0) {
        const peer = resolveEffectivePeerId({ cfg, cwd });
        const detailed = await buildRecallBlockDetailed(fetchJSON, cfg, queryText, {
          actorPeerId: peer.peerId,
          legacyPeerId: peer.legacyPeerId,
          sessionId: ovSessionId,
          excludeUris: cfg.recallExcludeUris,
          log,
        });
        if (detailed.block) {
          blocks.push(detailed.block);
          recalled = true;
          next = noteRecallResult(next, true, now);
          next = storeRecallCache(next, promptText, detailed.block, now);
          log("recall", { injected: true, bytes: detailed.block.length, stage: detailed.stage });
        } else if (!detailed.error && recallStageIsAnswer(detailed.stage)) {
          // The server answered; it simply had nothing for this query. That is
          // not a failure, and counting it as one would trip the breaker on a
          // user who asks a lot of questions with no matching memory.
          next = noteRecallResult(next, true, now);
          reasons.push("recall_no_results");
          log("recall", { injected: false, stage: detailed.stage });
        } else {
          next = noteRecallResult(next, false, now);
          reasons.push("recall_failed");
          log("recall", { injected: false, stage: detailed.stage });
        }
      } else {
        reasons.push("recall_budget");
      }
    }
  }

  const text = blocks
    .map((block) => stripWrapperTags(block))
    .filter(Boolean)
    .join("\n\n");

  return {
    text,
    state: next,
    profileInjected,
    recalled,
    reasons,
  };
}

/**
 * A cached block for an identical query inside the window.
 *
 * This is a request-saving device, not event de-duplication: the same text sent
 * twice is two questions the user asked, and both events print their own
 * answer. Only the network call is shared.
 */
export function reusableRecall(cache, promptText, now = Date.now()) {
  if (!cache || typeof cache !== "object") return null;
  if (typeof cache.at !== "number" || now - cache.at > RECALL_CACHE_MS) return null;
  if (cache.promptHash !== promptHash(promptText)) return null;
  if (typeof cache.block !== "string" || !cache.block) return null;
  return { block: cache.block, at: cache.at };
}

/**
 * Store a block for the reuse window, unless it is too large.
 *
 * The cap exists because state is read and written on every event; a 200 KB
 * block would make the state file more expensive than the request it saves.
 */
export function storeRecallCache(state, promptText, block, now = Date.now()) {
  if (typeof block !== "string" || !block) return { ...state, recallCache: undefined };
  if (Buffer.byteLength(block, "utf8") > RECALL_CACHE_MAX_BYTES) {
    return { ...state, recallCache: undefined };
  }
  return { ...state, recallCache: { promptHash: promptHash(promptText), block, at: now } };
}

/**
 * Should this payload event inject at all?
 *
 * The host only fires `UserPromptSubmit` for `origin.kind === "user"`, but a
 * steer is the same hook with `is_steer`, and a plugin that re-answers a steer
 * with the whole profile repeats itself mid-turn. Recall still runs on a steer;
 * the profile is injected once, which `profileInjected` already enforces.
 */
export function shouldInjectForEvent(input = {}) {
  return Array.isArray(input.prompt) || typeof input.prompt === "string";
}
