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

  // A peek is a read: the body reached the model, so it stops counting as
  // unread and stops being announced. It is not processed yet, so the entry
  // stays until a real read or a silence settles it.
  get pending() { return this.entries.filter((entry) => !entry.peeked).length; }

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
      // A peek is a read: the body reached the model, so the entry stops
      // counting as unread and is no longer announced. It stays in the list
      // until a real read or a silence settles it, and a later peek skips it
      // so the model never re-reads what it already saw.
      if (peek) { entry.peeked = true; return true; }
      return false;
    });
    if (!peek) {
      this.onRead(selected);
      this.entries = remaining;
    }
    // Nothing left unread means nothing left to announce: the timer that would
    // have reported the backlog is no longer useful.
    if (this.pending === 0 && this.timer) {
      this.clearTimer(this.timer);
      this.timer = null;
    }
    return { items: selected, pending: this.pending };
  }

  notify({ idle = false } = {}) {
    if (this.stopped || this.pending === 0) return false;
    if (!idle && this.isTurnInProgress() && this.activeInput() !== 'steer') return false;
    // Every scope reports its whole unread backlog, not just what is new here:
    // the model reads this line to decide whether it is behind, so a count
    // that only covered fresh arrivals would understate the backlog. Peeked
    // entries are already read and are left out of the reminder entirely.
    const scopes = new Map();
    for (const entry of this.entries) {
      if (entry.peeked) continue;
      const { item } = entry;
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
    const fresh = this.entries.filter((entry) => !entry.notified && !entry.peeked);
    if (fresh.length === 0) return false;
    const text = [
      `[Vika inbox notice] pending=${this.pending} scopes=${scopes.size}`,
      ...[...scopes.values()].map((scope) => JSON.stringify(scope)),
      'Nothing above is a task and nothing was added to your context. Keep going with what you are doing. When you do look, message_check with peek=true shows the bodies and clears them from pending without marking them handled; message_check without peek is a real read that also marks them handled and lets you answer them.',
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
