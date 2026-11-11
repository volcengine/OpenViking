// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * The master memory switch.
 *
 * `enabled` is the one knob that means "this conversation does not participate
 * in memory at all": no profile block, no recall, no capture, no commit, and
 * no traffic to the server. It is also the only switch a workspace can set, so
 * a repository whose content must never leave the machine turns memory off for
 * every session running inside it without touching anyone else's sessions.
 *
 * `recall.enabled` and `capture.enabled` stay the narrower switches: they turn
 * off one capability while the other keeps working. `false` anywhere in the
 * chain wins over any switch that is merely unset, which is why the capability
 * checks (`isRecallEnabled`, `isCaptureEnabled`) already read `enabled` too —
 * this helper exists for the gates those two do not cover, namely startup
 * profile injection and session initialization.
 */

/**
 * `false` in any layer turns memory off. A settings object that never mentions
 * the knob means on, matching the knob's declared default.
 */
export function isMemoryEnabled(cfg = {}) {
  for (const value of [cfg.enabled, cfg.memoryEnabled]) {
    if (value === false) return false;
    if (value && typeof value === "object" && !Array.isArray(value) && value.enabled === false) return false;
  }
  return true;
}
