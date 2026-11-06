export class MessageInbox {
  constructor({
    submit, isTurnInProgress, activeInput = () => 'steer',
    setTimer = setTimeout, clearTimer = clearTimeout, onRead = () => {},
  }) {
    Object.assign(this, { submit, isTurnInProgress, activeInput, setTimer, clearTimer, onRead });
    this.entries = [];
    this.timer = null;
    this.stopped = false;
  }

  get pending() { return this.entries.length; }

  route(item) {
    if (this.stopped) return false;
    this.entries.push({ item, notified: false, peeked: false });
    if (!this.isTurnInProgress()) this.onAvailable();
    else if (!this.timer) {
      this.timer = this.setTimer(() => {
        this.timer = null;
        this.notify();
      }, 3000);
      this.timer?.unref?.();
    }
    return true;
  }

  // A peek put this body in front of the model once. Silence may settle on
  // that much, since it sends nothing; a reply may not, since the body could
  // already be out of context by then.
  wasPeeked(seq) {
    return this.entries.some((entry) => entry.item.seq === seq && entry.peeked);
  }

  // A decision taken without reading the body (silence) still settles the
  // entry, or the backlog would keep announcing an input already decided on.
  settle(seq) {
    const before = this.entries.length;
    this.entries = this.entries.filter((entry) => entry.item.seq !== seq);
    if (this.entries.length === before) return false;
    if (this.pending === 0 && this.timer) {
      this.clearTimer(this.timer);
      this.timer = null;
    }
    return true;
  }

  read({ scopeKey = null, limit = 100, peek = false } = {}) {
    const selected = [];
    const remaining = this.entries.filter((entry) => {
      if (selected.length >= limit || (scopeKey !== null && entry.item.scopeKey !== scopeKey)) return true;
      if (peek && entry.peeked) return true;
      selected.push(entry.item);
      // A peek shows the body without settling it: the entry keeps its
      // `notified` state so the backlog stays announced, and a later peek
      // skips it so the model never re-reads what it already saw.
      if (peek) { entry.peeked = true; return true; }
      return false;
    });
    if (!peek) {
      this.onRead(selected);
      this.entries = remaining;
      if (this.pending === 0 && this.timer) {
        this.clearTimer(this.timer);
        this.timer = null;
      }
    }
    return { items: selected, pending: this.pending };
  }

  notify({ idle = false } = {}) {
    if (this.stopped || this.pending === 0) return false;
    if (!idle && this.isTurnInProgress() && this.activeInput() !== 'steer') return false;
    // Every scope reports its whole unread backlog, not just what is new here:
    // the model reads this line to decide whether it is behind, so a count
    // that only covered fresh arrivals would understate the backlog.
    const scopes = new Map();
    let peeked = 0;
    for (const entry of this.entries) {
      const { item } = entry;
      if (entry.peeked) peeked += 1;
      let scope = scopes.get(item.scopeKey);
      if (!scope) {
        scope = { scope_key: item.scopeKey, source: item.source, chat: item.chatId ?? null,
          pending: 0, first_event: item.eventId, latest_event: item.eventId, latest_from: null };
        scopes.set(item.scopeKey, scope);
      }
      scope.pending += 1;
      scope.latest_event = item.eventId;
      scope.latest_from = item.senderName ?? item.senderId ?? null;
    }
    // A notice is already in the conversation, so re-submitting it at every
    // idle edge adds nothing but a fresh turn: the turn ends unread, calls
    // onAvailable, and the pair repeats forever.
    const fresh = this.entries.filter((entry) => !entry.notified);
    if (fresh.length === 0) return false;
    const text = [
      `[Vika inbox notice] pending=${this.pending} scopes=${scopes.size}${peeked > 0 ? ` peeked=${peeked}` : ''}`,
      ...[...scopes.values()].map((scope) => JSON.stringify(scope)),
      `These messages have not been read${peeked > 0 ? `; ${peeked} of them were only peeked and still need a read` : ''}. Choose when to read them: message_check with scope_key reads one scope; message_check without scope_key reads all pending scopes, up to the requested limit.`,
    ].join('\n');
    const accepted = this.submit([{ text, seq: null }], {
      admission: idle ? 'claim-if-idle' : 'enqueue',
    })?.accepted === true;
    if (accepted) for (const entry of fresh) entry.notified = true;
    return accepted;
  }

  onAvailable() {
    if (this.isTurnInProgress()) return false;
    const accepted = this.notify({ idle: true });
    if (accepted && this.timer) {
      this.clearTimer(this.timer);
      this.timer = null;
    }
    return accepted;
  }

  stop() {
    this.stopped = true;
    if (this.timer) this.clearTimer(this.timer);
    this.timer = null;
    this.entries = [];
  }
}
