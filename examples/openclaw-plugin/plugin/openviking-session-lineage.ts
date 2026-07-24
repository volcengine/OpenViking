const DEFAULT_MAX_AGE_MS = 48 * 60 * 60 * 1000;
const DEFAULT_MAX_PREDECESSORS = 2;
const SEARCHABLE_RESET_REASONS = new Set(["daily", "idle", "reset"]);

export type OpenVikingSessionTransition = {
  sessionId?: string;
  sessionKey?: string;
  nextSessionId?: string;
  nextSessionKey?: string;
  reason?: string;
  transcriptArchived?: boolean;
};

type StoredTransition = {
  previousSessionId: string;
  nextSessionId: string;
  resetAt: number;
};

export class OpenVikingSessionLineageStore {
  private readonly transitionsBySessionKey = new Map<string, StoredTransition[]>();

  constructor(
    private readonly options: {
      maxAgeMs?: number;
      maxPredecessors?: number;
      now?: () => number;
    } = {},
  ) {}

  async record(event: OpenVikingSessionTransition): Promise<void> {
    const sessionKey = event.sessionKey?.trim();
    const nextSessionKey = event.nextSessionKey?.trim() || sessionKey;
    const previousSessionId = event.sessionId?.trim();
    const nextSessionId = event.nextSessionId?.trim();
    if (
      !sessionKey ||
      nextSessionKey !== sessionKey ||
      !previousSessionId ||
      !nextSessionId ||
      previousSessionId === nextSessionId ||
      event.transcriptArchived !== true ||
      !SEARCHABLE_RESET_REASONS.has(event.reason ?? "")
    ) {
      return;
    }

    const resetAt = this.now();
    const transitions = this.prune(this.transitionsBySessionKey.get(sessionKey) ?? [], resetAt)
      .filter((transition) => transition.nextSessionId !== nextSessionId);
    transitions.unshift({ previousSessionId, nextSessionId, resetAt });
    this.transitionsBySessionKey.set(sessionKey, transitions.slice(0, this.maxPredecessors()));
  }

  async getPredecessorSessionIds(
    sessionKey: string,
    currentSessionId: string,
  ): Promise<string[]> {
    const now = this.now();
    const transitions = this.prune(this.transitionsBySessionKey.get(sessionKey) ?? [], now);
    this.transitionsBySessionKey.set(sessionKey, transitions);

    const predecessors: string[] = [];
    let nextSessionId = currentSessionId;
    while (predecessors.length < this.maxPredecessors()) {
      const transition = transitions.find((candidate) => candidate.nextSessionId === nextSessionId);
      if (!transition || predecessors.includes(transition.previousSessionId)) break;
      predecessors.push(transition.previousSessionId);
      nextSessionId = transition.previousSessionId;
    }
    return predecessors;
  }

  private prune(transitions: StoredTransition[], now: number): StoredTransition[] {
    const cutoff = now - (this.options.maxAgeMs ?? DEFAULT_MAX_AGE_MS);
    return transitions.filter((transition) => transition.resetAt >= cutoff);
  }

  private maxPredecessors(): number {
    return this.options.maxPredecessors ?? DEFAULT_MAX_PREDECESSORS;
  }

  private now(): number {
    return this.options.now?.() ?? Date.now();
  }
}
