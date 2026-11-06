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
    this.entries.push({ item, notified: false });
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

  read({ scopeKey = null, limit = 100 } = {}) {
    const selected = [];
    const remaining = this.entries.filter((entry) => {
      if (selected.length >= limit || (scopeKey !== null && entry.item.scopeKey !== scopeKey)) return true;
      selected.push(entry.item);
      return false;
    });
    this.onRead(selected);
    this.entries = remaining;
    if (this.pending === 0 && this.timer) {
      this.clearTimer(this.timer);
      this.timer = null;
    }
    return { items: selected, pending: this.pending };
  }

  notify({ idle = false } = {}) {
    if (this.stopped || this.pending === 0) return false;
    if (!idle && this.isTurnInProgress() && this.activeInput() !== 'steer') return false;
    const entries = this.entries.filter((entry) => !entry.notified);
    const scopes = new Map();
    for (const { item } of entries) {
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
    if (entries.length === 0) return false;
    const text = [
      `[Vika inbox notice] pending=${this.pending} scopes=${scopes.size}`,
      ...[...scopes.values()].map((scope) => JSON.stringify(scope)),
      'These messages have not been read. Choose when to read them: message_check with scope_key reads one scope; message_check without scope_key reads all pending scopes, up to the requested limit.',
    ].join('\n');
    const accepted = this.submit([{ text, seq: null }], {
      admission: idle ? 'claim-if-idle' : 'enqueue',
    })?.accepted === true;
    if (accepted) for (const entry of entries) entry.notified = true;
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
