// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Turn boundary: accepted input and the turn it belongs to. Admission
// (deliver/deliverBatch/tryStartBatch), the per-batch delivery state machine
// (pending -> acknowledged -> observed -> settled, or requeued/failed), the single
// settlement path that reduces a turn's deliveries to exactly one outcome, the
// journal cursor it advances, and the idle waiters a caller waits on. Cancelling
// and abandoning a turn are the other settlement paths, so they live here too.
import { cognitionHistoryPrefix, CognitionLimitError } from '../cognition.mjs';
import { safeError as safe } from '../logger.mjs';
import {
  deliveryItem, receiptIds, withReceiptIds, withTurnNotice,
} from '../runtime-input.mjs';
import { turnErrorText } from '../codex.mjs';

// session/wait rounds spent waiting for a steered message to be observed.
const UNOBSERVED_SETTLE_ROUNDS = 3;

// One steer is written as one JSON-RPC frame: a single LF-terminated line, as
// `_write` frames it. The bridge drops a line past MAX_FRAME_BYTES
// (dsh-bridge/framer.mjs) and the daemon reads with the same bound
// (dsh/transport.mjs MAX_LINE_BYTES); the three numbers are one contract, so a
// frame that would exceed it must never be written: the child would drop it
// without an answer and the batch would be retried forever. A test asserts the
// equality with the bridge's constant.
export const MAX_STEER_FRAME_BYTES = 16_000_000;

export default {
  whenIdle() {
    if (!this.turnInProgress && this.queue.length === 0) return Promise.resolve();
    return new Promise((resolve) => this.idleWaiters.push(resolve));
  },

  deliver(text, seq = null, receiptId = null) {
    return this.deliverBatch([deliveryItem({ text, seq, receiptId })]);
  },

  deliverBatch(items) {
    if (this.stopping || !Array.isArray(items) || items.length === 0) return false;
    const batch = items.map(deliveryItem);
    // The runtime cannot name the model it runs (an unconfirmed or refused
    // selection on a session whose route was never established): running the
    // input on a guess is worse than reporting that it did not run.
    if (this._selectionBlocked()) {
      this._failBlockedSelection(batch);
      return true;
    }
    // Admission is bounded: past the caps the batch is failed with a callback
    // (one terminal state, never a silent queue) and reported as accepted, so
    // the caller hands ownership over exactly once.
    if (!this._hasQueueRoom(batch)) {
      this._queueOverflow(batch);
      return true;
    }
    if (!this.connected || this.restarting || this.sessionRotation
      || (!this.turn && this._selectionPending())) {
      this.queue.push(batch);
      // A pending model/effort change is applied at the turn boundary, never by
      // restarting the process; this only starts that application.
      this._applyPendingSelection();
      if (!this.connected && this.startedOk && this.autoReconnect && !this.reconnecting) {
        void this._reconnectLoop();
      }
      return true;
    }
    this._send(batch);
    return true;
  },

  tryStartBatch(items) {
    if (!this.stopping && Array.isArray(items) && items.length > 0 && this._selectionBlocked()) {
      // Terminal, unlike a busy runtime: the batch is taken over and reported
      // once instead of being handed back to a caller that would retry forever.
      this._failBlockedSelection(items.map(deliveryItem));
      return true;
    }
    // A changed profile patch outranks the selection: it can add the route the
    // selection names, so the process is restarted before anything claims a turn.
    if (!this.turnInProgress && !this.stopping && this.connected && this._profilePatchChanged()) {
      void this.restartWhenIdle('model-settings-changed');
      return false;
    }
    if (this.stopping || !this.connected || this.turnInProgress || this.sessionRotation
      || this.queue.length > 0 || this._selectionPending()
      || !Array.isArray(items) || items.length === 0) {
      // The caller keeps ownership of the batch, so nothing is failed here; the
      // pending change is started so a later claim finds the runtime ready.
      if (!this.turnInProgress && !this.stopping) this._applyPendingSelection();
      return false;
    }
    const batch = items.map(deliveryItem);
    // Busy or over the cap: the caller keeps ownership of the batch, so nothing
    // is failed here (that would double-report a batch the caller still holds).
    if (!this._hasQueueRoom(batch)) return false;
    this._send(batch);
    return true;
  },

  _hasQueueRoom(batch) {
    const queued = this.state.queued;
    return !((this.maxQueueBatches > 0 && this.queue.length >= this.maxQueueBatches)
      || (this.maxQueuedItems > 0 && queued + batch.length > this.maxQueuedItems));
  },

  _queueOverflow(batch) {
    this.logger.warn('dsh_input_queue_full', {
      queuedBatches: this.queue.length, queuedItems: this.state.queued, incoming: batch.length,
    });
    this._failItems(batch, new Error('dsh input queue is full; the runtime is not accepting input'));
  },

  // ---------------------------------------------------------------------
  // Accepted input delivery. Every batch handed to the SDK is one delivery and
  // reaches exactly one terminal outcome; these methods are the only writers of
  // that state, so no path can drop an input between them.
  //
  //   pending --ack--> acknowledged --user/message--> observed --> settled
  //      \                  \--never observed--> failed (unknown)
  //       \--malformed ack / RPC timeout--> requeued or failed
  //       \--connection loss--> requeued (unacked) / failed (acked)
  //       \--turn replaced, stop, cancel--> abandoned / dropped
  // ---------------------------------------------------------------------
  _openDelivery(items, prepared, turnGen) {
    const delivery = {
      id: ++this.deliverySeq,
      items,
      prepared,
      turnGen,
      child: null,
      rpcId: null,
      promise: null,
      messageId: null,
      retried: false,
      wasActive: false,
      state: 'pending',
      closed: false,
    };
    this.deliveries.set(delivery.id, delivery);
    return delivery;
  },

  _closeDelivery(delivery, outcome) {
    if (!delivery || delivery.closed) return false;
    delivery.closed = true;
    delivery.outcome = outcome;
    if (delivery.rpcId) this.inflight.delete(delivery.rpcId);
    this.deliveries.delete(delivery.id);
    return true;
  },

  _releaseSteer(delivery) {
    if (!delivery) return;
    if (delivery.rpcId) this.inflight.delete(delivery.rpcId);
    delivery.rpcId = null;
  },

  // pending -> acknowledged (or straight to observed when the event for this
  // message id already arrived). The only place an acked steer feeds the turn.
  _ackDelivery(delivery, turn, messageId) {
    delivery.messageId = messageId;
    turn.items.push(...delivery.items);
    turn.submitSeq += 1;
    const observed = this.observedMessageIds.has(messageId)
      || turn.observedMessageIds.has(messageId);
    delivery.state = observed ? 'observed' : 'acknowledged';
    if (!observed) turn.pendingMessageIds.add(messageId);
    this.deliveredThroughSeq = Math.max(
      this.deliveredThroughSeq, delivery.prepared?.deliveredThroughSeq ?? 0,
    );
    this._recordAutonomousStart(turn);
    if (delivery.wasActive) {
      const input = delivery.items.map((item) => item.text).join('\n');
      this._recordActivity('agent.steered', {
        inboundSeq: delivery.prepared?.seqs.at(-1) ?? null,
        summary: input,
        metadata: { runtime: this.runtime, input, inboundSeqs: delivery.prepared?.seqs ?? [] },
      });
    }
  },

  // acknowledged -> observed, tolerant of either arrival order.
  _observeMessage(messageId) {
    if (typeof messageId !== 'string' || !messageId) return;
    this.observedMessageIds.add(messageId);
    if (this.observedMessageIds.size > 2_000) {
      this.observedMessageIds.delete(this.observedMessageIds.values().next().value);
    }
    const turn = this.turn;
    if (!turn) return;
    turn.observedMessageIds.add(messageId);
    turn.pendingMessageIds.delete(messageId);
    for (const delivery of this.deliveries.values()) {
      if (delivery.state === 'acknowledged' && delivery.messageId === messageId) {
        delivery.state = 'observed';
      }
    }
  },

  _requeueDelivery(delivery, { drain = true } = {}) {
    if (!this._closeDelivery(delivery, 'requeued')) return false;
    if (this.stopping) {
      this._failItems(delivery.items, new Error('dsh stopped before the input was delivered'));
      return false;
    }
    if (delivery.prepared?.notice) this.turnNotice ??= delivery.prepared.notice;
    this.queue.unshift(delivery.items);
    if (drain) this._drain();
    return true;
  },

  _failDelivery(delivery, error, metadata = {}) {
    if (!this._closeDelivery(delivery, 'failed')) return;
    this._failItems(delivery.items, error, metadata);
  },

  _bodyFor(items) {
    const seqs = items.map((item) => item.seq).filter((seq) => seq != null);
    const beforeSeq = seqs.length > 0 ? Math.min(...seqs) : null;
    const throughSeq = this.store.cognitionJournalBeforeInbound?.(
      beforeSeq, this.cognitionEpoch,
    ) ?? 0;
    const notice = this.turnNotice;
    // The prefix belongs to the frame that carries it: it is consumed when that
    // frame is written, never when it is acknowledged. Waiting for the ack made
    // every input prepared in between re-attach the same history (P2-6).
    const historyPrefix = Boolean(this.needsHistoryPrefix);
    let body = withTurnNotice(this, items.map((item) => item.text).join('\n'));
    let prefixThroughSeq = this.deliveredThroughSeq;
    if (historyPrefix) {
      const catchup = cognitionHistoryPrefix(this.store, this.cognitionEpoch, throughSeq, 0);
      prefixThroughSeq = catchup.throughSeq;
      if (catchup.text) body = `${catchup.text}\n${body}`;
    }
    let deliveredThroughSeq = prefixThroughSeq;
    for (const seq of seqs) {
      deliveredThroughSeq = Math.max(
        deliveredThroughSeq,
        this.store.cognitionJournalSeqForInbound?.(seq, this.cognitionEpoch) ?? 0,
      );
    }
    return { body, seqs, deliveredThroughSeq, notice, historyPrefix };
  },

  _owner() {
    return Object.freeze({
      runtime: this.runtime,
      authorityToken: this.authorityToken,
      sessionId: this.sessionId,
      epoch: this.cognitionEpoch,
    });
  },

  _openTurn({ autonomous = false } = {}) {
    const turn = {
      gen: ++this.generation,
      owner: this._owner(),
      items: [],
      startedAtMs: Date.now(),
      autonomous,
      origin: autonomous ? null : undefined,
      pendingMessageIds: new Set(),
      observedMessageIds: new Set(),
      submitSeq: 0,
      usage: null,
      lastEnd: null,
      cancelRequested: false,
      startRecorded: false,
    };
    this.turn = turn;
    this.toolStarts.clear();
    // Record autonomous starts lazily: the following user/message supplies its
    // origin, while a no-input completion still gets an `unknown` start record.
    void this._settleLoop(turn);
    return turn;
  },

  _recordAutonomousStart(turn) {
    if (this.turn !== turn || !turn.autonomous || turn.startRecorded) return;
    turn.startRecorded = true;
    turn.origin ??= 'unknown';
    this._recordActivity('agent.turn_started', {
      metadata: {
        runtime: this.runtime, model: this.appliedModel, effort: this.appliedEffort,
        autonomous: true, origin: turn.origin, inboundSeqs: [],
      },
    });
  },

  _send(items) {
    if (this.stopping || !this.connected) {
      if (this._hasQueueRoom(items)) this.queue.push(items);
      else this._queueOverflow(items);
      return;
    }
    // Not writable at this instant: a restart in flight, or stdin backpressure.
    // Both hold the batch in the bounded queue, never in the stream: writing past
    // a stream that asked to wait is what makes the queue caps meaningless
    // (nothing is queued, so nothing is bounded). The drain that follows sends it.
    if (this.restarting || this.writeBlocked) {
      if (this._hasQueueRoom(items)) this.queue.push(items);
      else this._queueOverflow(items);
      return;
    }
    // Both switches happen only before a turn opens, and a changed profile patch
    // goes first: it may be the document that makes the selected route exist.
    if (!this.turn && this.inflight.size === 0 && this._profilePatchChanged()) {
      if (this._hasQueueRoom(items)) this.queue.push(items);
      else this._queueOverflow(items);
      void this.restartWhenIdle('model-settings-changed');
      return;
    }
    if (!this.turn && this._selectionPending()) {
      if (this._hasQueueRoom(items)) this.queue.push(items);
      else this._queueOverflow(items);
      this._applyPendingSelection();
      return;
    }
    // A batch that does not fit one frame is written as several steers: the
    // child drops a longer frame without answering it.
    let batch = items;
    let split = false;
    for (;;) {
      let prepared;
      try { prepared = this._bodyFor(batch); } catch (error) {
        this._failItems(batch, error);
        this._drain();
        return;
      }
      const frameBytes = this._steerFrameBytes(prepared.body);
      if (frameBytes <= MAX_STEER_FRAME_BYTES) {
        this._emit(batch, prepared);
        if (split) this._drain();
        return;
      }
      if (batch.length === 1) {
        this._failItems(batch, new Error(
          `dsh input is larger than one ${MAX_STEER_FRAME_BYTES}-byte steer frame`,
        ), { endReason: 'input-too-large' });
        this._drain();
        return;
      }
      // One frame cannot carry the batch: write the first half and hold the rest
      // at the head of the queue, in order. `_bodyFor` consumed the one-shot
      // notice, so it goes back for the frame that will actually carry it.
      const half = Math.ceil(batch.length / 2);
      this.queue.unshift(batch.slice(half));
      this.turnNotice = prepared.notice ?? this.turnNotice;
      this.logger.warn('dsh_steer_frame_split', {
        bytes: frameBytes, frameLimit: MAX_STEER_FRAME_BYTES,
        items: half, queuedBatches: this.queue.length,
      });
      batch = batch.slice(0, half);
      split = true;
    }
  },

  // UTF-8 bytes of the frame a steer body becomes on the wire. Measured on the
  // encoded line, not on the character count: JSON escaping and multi-byte
  // characters are what can push a body past the child's frame bound.
  _steerFrameBytes(body) {
    return Buffer.byteLength(JSON.stringify({
      jsonrpc: '2.0',
      id: 'dsh-0000000000',
      method: 'session/steer',
      params: { sessionId: this.sessionId, contentBlocks: [{ type: 'text', text: body }] },
    }), 'utf8');
  },

  // Open the turn (if this is its first batch) and steer the prepared body.
  _emit(items, prepared) {
    const wasActive = Boolean(this.turn);
    const turn = this.turn ?? this._openTurn();
    const input = items.map((item) => item.text).join('\n');
    if (!wasActive) {
      turn.startRecorded = true;
      this._recordActivity('agent.turn_started', {
        inboundSeq: prepared.seqs.at(-1) ?? null,
        summary: input,
        metadata: {
          runtime: this.runtime,
          model: this.appliedModel,
          effort: this.appliedEffort,
          input,
          historyChars: Math.max(0, prepared.body.length - input.length),
          inboundSeqs: prepared.seqs,
        },
      });
    }
    this._sendPrepared({ items, prepared, turn, wasActive, retried: false });
  },

  _sendPrepared({ items, prepared, turn, wasActive, retried }) {
    if (this.turn !== turn || !this.connected || this.writeBlocked) {
      // The turn this batch was prepared for is gone, or stdin is not writable
      // right now: hold the batch for the next steer instead of writing into a
      // session (or a stream) it does not belong to.
      if (prepared?.notice) this.turnNotice ??= prepared.notice;
      this.queue.unshift(items);
      this._drain();
      return;
    }
    const delivery = this._openDelivery(items, prepared, turn.gen);
    delivery.wasActive = wasActive;
    delivery.retried = retried;
    const request = this._request('session/steer', {
      sessionId: this.sessionId,
      contentBlocks: [{ type: 'text', text: prepared.body }],
    }, this.controlTimeoutMs, { kind: 'steer' });
    // The session has now been told its history: a later frame of the same turn
    // (a split batch, input steered before this one is acknowledged) must not
    // repeat it. A rejected steer re-arms the flag on its own path.
    if (prepared.historyPrefix) this.needsHistoryPrefix = false;
    const entry = request.dshEntry;
    if (entry) {
      entry.delivery = delivery;
      delivery.rpcId = entry.id;
      delivery.child = entry.child;
    }
    delivery.promise = request;
    request.then(
      (result) => this._steerAccepted(delivery, result),
      (error) => this._steerRejected(delivery, error),
    );
  },

  _steerAccepted(delivery, result) {
    if (!delivery || delivery.closed) return;
    this._releaseSteer(delivery);
    if (typeof result?.messageId !== 'string' || !result.messageId) {
      // A response without a message id cannot be tracked to consumption; the
      // batch is retried in a fresh session like any other unconfirmed steer.
      void this._steerRejected(delivery, Object.assign(
        new Error('dsh session/steer returned no message id'), { code: 'DSH_STEER_MALFORMED' },
      ));
      return;
    }
    const turn = this.turn;
    if (!turn || turn.gen !== delivery.turnGen) {
      // DSH accepted the input for a turn that already ended, so no turn can
      // account for it any more: report it rather than settling it as success.
      this._failDelivery(delivery, new Error('dsh accepted the input after its turn ended'), {
        endReason: 'input-orphaned',
      });
      return;
    }
    this._ackDelivery(delivery, turn, result.messageId);
  },

  async _steerRejected(delivery, error) {
    if (!delivery || delivery.closed) return;
    this._releaseSteer(delivery);
    if (error?.code === 'DSH_CONNECTION_LOST') {
      // The exit path requeues pending deliveries; this is a no-op when it did.
      this._requeueDelivery(delivery);
      return;
    }
    const turn = this.turn;
    if (error?.code === -32602 && !delivery.retried && this.connected
      && turn && turn.gen === delivery.turnGen) {
      try {
        await this._request('session/working-directory/get', { sessionId: this.sessionId });
        if (!delivery.closed && this.connected && this.turn?.gen === delivery.turnGen) {
          // The retried attempt owns the batch from here; close this record
          // without a callback so a retry failure is reported exactly once.
          this._closeDelivery(delivery, 'requeued');
          this._sendPrepared({
            items: delivery.items,
            prepared: delivery.prepared,
            turn: this.turn,
            wasActive: delivery.wasActive,
            retried: true,
          });
          return;
        }
      } catch (retryError) {
        if (retryError?.code === 'DSH_CONNECTION_LOST') {
          this._requeueDelivery(delivery);
          return;
        }
        error = retryError;
      }
    }
    this.logger.warn('dsh_steer_failed_recycling', { error: safe(error) });
    // Written but never acknowledged: its fate is unknown. Recycle the process
    // (a fresh session with a history prefix absorbs the replay) and let the
    // exit path requeue the batch instead of counting it as delivered.
    this.forceFreshSession = true;
    this._markSessionUnresumable();
    this.sessionReady = false;
    this.needsHistoryPrefix = true;
    this.syncedThroughSeq = 0;
    this.deliveredThroughSeq = 0;
    if (this.child && delivery.child === this.child) this._handleExit(this.child, null, null, error);
    else this._requeueDelivery(delivery);
  },

  async _settleLoop(turn) {
    let unobservedRounds = 0;
    let idleTimeouts = 0;
    while (this.turn === turn && !this.stopping) {
      const waitSeq = turn.submitSeq;
      const unobservedAtWait = turn.pendingMessageIds.size;
      try {
        await this._request('session/wait', { sessionId: this.sessionId }, this._waitIdleTimeout());
        idleTimeouts = 0;
      } catch (error) {
        if (this.turn !== turn) return;
        if (error?.code === 'DSH_RPC_TIMEOUT') {
          // An idle bound, never a turn bound: a long turn is legal, so re-issue
          // the wait and make the stall observable instead of failing the work.
          idleTimeouts += 1;
          this.logger.warn('dsh_wait_idle_timeout', {
            sessionId: this.sessionId,
            idleTimeouts,
            idleTimeoutMs: this.waitIdleTimeoutMs,
            submitted: turn.submitSeq,
          });
          continue;
        }
        if (error?.code !== 'DSH_CONNECTION_LOST') {
          this._finishTurn(turn, this._failureOutcome(turnErrorText(error), {
            errorCode: error?.code,
          }));
        }
        return;
      }
      if (this.turn !== turn) return;
      const pendingForTurn = [...this.deliveries.values()]
        .filter((delivery) => delivery.turnGen === turn.gen
          && delivery.state === 'pending' && delivery.promise);
      if (pendingForTurn.length > 0) {
        // Capture the live set, then re-check after every settlement: later
        // batches may be accepted while an earlier steer is still in flight.
        await Promise.allSettled(pendingForTurn.map((delivery) => delivery.promise));
        if (this.turn !== turn) return;
        if ([...this.deliveries.values()].some((delivery) => delivery.turnGen === turn.gen
          && delivery.state === 'pending') || turn.submitSeq !== waitSeq) continue;
      }
      if (turn.submitSeq !== waitSeq) continue;
      const aborted = turn.lastEnd?.reason?.kind === 'aborted';
      if (turn.pendingMessageIds.size > 0 && !aborted) {
        if (turn.cancelRequested) {
          // keepInbox:false deliberately drops queued steers: expected, so this
          // never becomes a failure of the turn.
          this.logger.info('dsh_settle_cancelled_unobserved', {
            sessionId: this.sessionId,
            messageIds: [...turn.pendingMessageIds],
          });
          this._finishTurn(turn, this._turnOutcome(turn));
          return;
        }
        if (unobservedRounds < UNOBSERVED_SETTLE_ROUNDS) {
          // A user/message may already have been observed before wait resolved;
          // only wait again when the population actually changed during the RPC.
          if (turn.pendingMessageIds.size < unobservedAtWait) continue;
          unobservedRounds += 1;
          continue;
        }
        // Acknowledged but never observed means consumption is unknown: report
        // it as failed instead of advancing the acceptance cursor as delivered.
        this.logger.warn('dsh_settle_unobserved', {
          sessionId: this.sessionId,
          messageIds: [...turn.pendingMessageIds],
          rounds: unobservedRounds,
        });
        this._finishTurn(turn, this._unobservedOutcome(turn));
        return;
      }
      this._finishTurn(turn, this._turnOutcome(turn));
      return;
    }
  },

  _waitIdleTimeout() {
    const idle = Number(this.waitIdleTimeoutMs);
    return Number.isFinite(idle) && idle > 0 ? idle : Infinity;
  },

  // The turn itself may have ended normally: only the consumption of some
  // accepted input is unknown. `inputOnly` keeps that distinction, so the batches
  // the model did observe still settle on the turn's own end while the unknown
  // ones are reported for their own reason.
  _unobservedOutcome(turn) {
    const messageIds = [...turn.pendingMessageIds];
    return {
      status: 'failed',
      endReason: 'unobserved',
      errorCode: 'DSH_INPUT_UNOBSERVED',
      error: `dsh did not confirm consumption of ${messageIds.length} accepted input batch(es)`,
      unobservedMessageIds: messageIds,
      inputOnly: true,
    };
  },

  _turnOutcome(turn) {
    const reason = turn.lastEnd?.reason;
    if (!reason || reason.kind === 'completed' || reason.kind === 'max-tokens') {
      return { status: 'completed', endReason: reason?.kind ?? 'completed', error: null };
    }
    if (reason.kind === 'aborted') {
      if (turn.cancelRequested) return { status: 'interrupted', endReason: 'aborted', error: null };
      return this._failureOutcome(`aborted: ${reason.reason?.kind ?? 'unknown'}`, { endReason: 'aborted' });
    }
    if (reason.kind === 'error') {
      const failure = reason.error ?? {};
      return this._failureOutcome(turnErrorText(failure) || 'dsh turn failed', {
        endReason: 'error', errorCode: failure.code, providerStatus: failure.status,
      });
    }
    if (reason.kind === 'blocked') return this._failureOutcome('blocked', { endReason: 'blocked' });
    return this._failureOutcome(reason.kind || 'unknown', { endReason: reason.kind });
  },

  _failureOutcome(error, fields = {}) {
    return { status: 'failed', endReason: 'error', error: String(error || 'dsh turn failed'), ...fields };
  },

  _activeSeqs(turn = this.turn) {
    return [...new Set((turn?.items ?? []).map((item) => item.seq).filter((seq) => seq != null))];
  },

  // The only settlement path for a turn. Every accepted batch reaches exactly
  // one terminal state, reported once:
  //   observed                          -> succeeded (or the turn's own failure)
  //   acknowledged, never observed      -> failed as unknown consumption
  //   written, never acknowledged       -> requeued into the next turn
  //   dropped by a cancel (keepInbox)   -> dropped, not a failure
  // Input whose consumption could not be confirmed is excluded from the
  // acceptance cursor whatever ended the turn: it is reported as unknown on its
  // own, not folded into the turn's error, because the two mean different things
  // (the model consumed this and the turn failed vs. nobody knows whether it was
  // consumed at all).
  _finishTurn(turn, outcome) {
    if (this.turn !== turn) return;
    this._recordAutonomousStart(turn);
    const deliveries = [...this.deliveries.values()].filter((delivery) => delivery.turnGen === turn.gen);
    const unobserved = deliveries.filter((delivery) => delivery.state === 'acknowledged');
    // A cancelled turn (keepInbox:false) deliberately drops steers it never
    // consumed: modeled separately, so it is not a failure of the turn.
    const cancelled = Boolean(turn.cancelRequested) && turn.lastEnd?.reason?.kind === 'aborted';
    const unknown = cancelled ? [] : unobserved;
    const dropped = cancelled ? unobserved : [];
    const items = turn.items;
    const seqs = this._activeSeqs(turn);
    const unknownItems = unknown.flatMap((delivery) => delivery.items);
    const unknownSeqs = [...new Set(unknown.flatMap((delivery) => delivery.prepared?.seqs ?? []))];
    // Only observed input may advance the cursor; anything unknown stops it.
    const acceptedThroughSeq = unknown.length > 0
      ? Math.min(this.deliveredThroughSeq, this._unobservedBoundary(unknown))
      : this.deliveredThroughSeq;
    const startedAtMs = turn.startedAtMs;
    // The cursor advance is a store transaction, and a store that refuses (a
    // locked database, a full disk) must not cost the turn its settlement: the
    // caller that starts this loop does not await it, so an exception here
    // becomes an unhandled rejection and the daemon exits. A cursor that did not
    // advance is the safe direction -- the same input is re-offered after a
    // restart rather than silently marked consumed.
    try {
      this._acceptJournal(turn.owner, acceptedThroughSeq);
    } catch (error) {
      this.logger.warn('dsh_session_acceptance_failed', { error: safe(error) });
    }
    this.turn = null;
    this.modelWorking = false;
    this.deliveredThroughSeq = this.syncedThroughSeq;
    this.toolStarts.clear();
    const requeued = [];
    for (const delivery of deliveries) {
      const state = delivery.state;
      // Written but never acknowledged: not delivered, not lost either. It goes
      // back to the queue head, so it is never counted as this turn's success.
      const result = state === 'pending' ? 'requeued'
        : state === 'observed' ? 'succeeded'
          : dropped.includes(delivery) ? 'dropped' : 'failed';
      if (!this._closeDelivery(delivery, result)) continue;
      if (state === 'pending') {
        if (delivery.prepared?.notice) this.turnNotice ??= delivery.prepared.notice;
        requeued.push(delivery.items);
      }
    }
    // An outcome that exists only because input was never observed (`inputOnly`)
    // is not a failure of the turn: the batches the model did consume settle on
    // the turn's own end, which may still have failed on its own. Every other
    // outcome carries the turn's error directly.
    const turnOutcome = outcome.inputOnly ? this._turnOutcome(turn) : outcome;
    const turnError = turnOutcome.error ?? null;
    // The turn record names the reason the turn is recorded as failed: its own
    // error when it had one, otherwise the unconfirmed consumption that stopped
    // the cursor.
    const endReason = turnError ? turnOutcome.endReason
      : unknown.length > 0 ? 'unobserved' : outcome.endReason;
    const errorCode = turnError ? turnOutcome.errorCode
      : unknown.length > 0 ? 'DSH_INPUT_UNOBSERVED' : outcome.errorCode;
    const unobservedMessageIds = outcome.unobservedMessageIds
      ?? (unknown.length > 0 ? [...turn.pendingMessageIds] : []);
    this._recordActivity('agent.turn_completed', {
      inboundSeq: seqs.at(-1) ?? null,
      status: unknown.length > 0 || turnError ? 'failed' : outcome.status,
      summary: outcome.error ?? undefined,
      metadata: {
        runtime: this.runtime,
        inboundSeqs: seqs,
        durationMs: Date.now() - startedAtMs,
        endReason,
        ...(errorCode !== undefined ? { errorCode } : {}),
        ...(turnError && outcome.providerStatus !== undefined ? { status: outcome.providerStatus } : {}),
        ...(unobservedMessageIds.length > 0 ? { unobservedMessageIds } : {}),
        ...(unknownSeqs.length > 0 ? { unobservedInboundSeqs: unknownSeqs } : {}),
        ...(dropped.length > 0
          ? { droppedInboundSeqs: [...new Set(dropped.flatMap((delivery) => delivery.prepared?.seqs ?? []))] }
          : {}),
        ...(turn.autonomous ? { autonomous: true, origin: turn.origin ?? 'unknown' } : {}),
        ...(turn.usage ?? {}),
      },
    });
    const successItems = items.filter((item) => !unknownItems.includes(item));
    if (successItems.length > 0 && !turnError && this.onTurnSucceeded) {
      try {
        this.onTurnSucceeded(withReceiptIds({
          inboundSeqs: [...new Set(successItems.map((item) => item.seq).filter((seq) => seq != null))],
        }, receiptIds(successItems)));
      } catch (error) {
        this.logger.warn('turn_success_notify_failed', { error: safe(error) });
      }
    }
    // The turn's failure covers the input it consumed. The batch that was never
    // observed is reported separately, for its own reason, so a chat is never
    // told "consumed and the model errored" about input the model never saw.
    if (successItems.length > 0 && turnError) this._notifyFailed(successItems, turnError);
    if (unknownItems.length > 0) {
      this._notifyFailed(unknownItems, new Error('dsh did not confirm consumption of the accepted input'));
    }
    if (requeued.length > 0) this.queue.unshift(...requeued);
    this._drain();
    this._notifyAvailable();
  },

  // The failure notice is addressed from the last inbound of the callback, so a
  // turn that consumed input from several chats is reported once per chat:
  // otherwise only the chat of the newest input is ever told.
  _notifyFailed(items, error) {
    if (!this.onTurnFailed || items.length === 0) return;
    for (const group of this._conversationGroups(items)) {
      const groupSeqs = [...new Set(group.map((item) => item.seq).filter((seq) => seq != null))];
      try {
        this.onTurnFailed(withReceiptIds({ inboundSeqs: groupSeqs, error: safe(error) }, receiptIds(group)));
      } catch (callbackError) {
        this.logger.warn('turn_failure_notify_failed', { error: safe(callbackError) });
      }
    }
  },

  _conversationGroups(items) {
    const groups = new Map();
    for (const item of items) {
      let key = '';
      if (item.seq !== null && item.seq !== undefined) {
        try {
          const row = this.store.inboundBySeq?.(item.seq);
          key = row?.scopeKey ?? row?.conversationKey ?? '';
        } catch { key = ''; }
      }
      const group = groups.get(key);
      if (group) group.push(item);
      else groups.set(key, [item]);
    }
    return [...groups.values()];
  },

  // Journal position just before the first inbound of the unconfirmed input.
  _unobservedBoundary(deliveries) {
    const inbound = deliveries
      .flatMap((delivery) => delivery.prepared?.seqs ?? [])
      .filter((seq) => seq != null);
    if (inbound.length === 0) return this.deliveredThroughSeq;
    const before = this.store.cognitionJournalBeforeInbound?.(Math.min(...inbound), this.cognitionEpoch);
    return Number.isSafeInteger(before) ? before : 0;
  },

  _acceptJournal(owner, acceptedThroughSeq) {
    if (!owner?.sessionId || !Number.isSafeInteger(acceptedThroughSeq)) return false;
    let accepted = true;
    if (this.sessionPersistence && this.store.advanceOwnedSessionAcceptance) {
      accepted = this.store.advanceOwnedSessionAcceptance({ ...owner, acceptedThroughSeq });
    }
    const stillCurrent = owner.runtime === this.runtime
      && owner.authorityToken === this.authorityToken
      && owner.sessionId === this.sessionId
      && owner.epoch === this.cognitionEpoch;
    if (!accepted || !stillCurrent) {
      this.logger.info('dsh_session_acceptance_stale', { acceptedThroughSeq });
      return false;
    }
    this.syncedThroughSeq = Math.max(this.syncedThroughSeq, acceptedThroughSeq);
    return true;
  },

  _failItems(items, error, metadata = {}) {
    if (!Array.isArray(items) || items.length === 0) {
      this._notifyAvailable();
      return;
    }
    const seqs = [...new Set(items.map((item) => item.seq).filter((seq) => seq != null))];
    this._recordActivity('agent.turn_completed', {
      inboundSeq: seqs.at(-1) ?? null,
      status: 'failed',
      summary: safe(error),
      metadata: {
        runtime: this.runtime,
        inboundSeqs: seqs,
        terminal: error instanceof CognitionLimitError,
        ...metadata,
      },
    });
    this._notifyFailed(items, error);
    this._notifyAvailable();
  },

  _drain() {
    if (this.stopping || this.restarting || !this.connected || !this.child?.stdin?.writable) return;
    if (this.sessionRotation || this.writeBlocked) return;
    // A turn boundary is the only place settings may change: the profile patch
    // first (it can add the route the selection names), then the selection. Both
    // are checked only when input is waiting, so a drain that has nothing to do
    // never reads the settings document.
    if (!this.turn && this.queue.length > 0 && this.inflight.size === 0) {
      if (this.selectionUnresolved === true) {
        // Re-establish the truth before anything claims a turn: a select has no
        // read-back, so only a fresh handshake can tell what really runs. The
        // queued input keeps waiting for it.
        this._reestablishSelection();
        return;
      }
      if (this._selectionBlocked()) {
        this._failBlockedSelection(this.queue.splice(0).flat());
        return;
      }
      if (this._profilePatchChanged()) {
        void this.restartWhenIdle('model-settings-changed');
        return;
      }
      if (this._selectionPending()) {
        this._applyPendingSelection();
        return;
      }
    }
    if (this.queue.length === 0) return;
    if (this.turn) {
      while (this.queue.length > 0 && this.turn && this.connected && !this.writeBlocked) {
        this._send(this.queue.shift());
      }
      return;
    }
    const merged = this.queue.splice(0).flat();
    if (merged.length > 0) this._send(merged);
  },

  cancelTurn() {
    if (!this.turn || !this.connected || !this.child?.stdin?.writable) return false;
    this.turn.cancelRequested = true;
    this._request('session/cancel', { sessionId: this.sessionId, keepInbox: false }).catch((error) => {
      this.logger.warn('dsh_turn_cancel_failed', { error: safe(error) });
    });
    return true;
  },

  async interruptTurn() {
    while (this.settingsUpdate) await this.settingsUpdate;
    if (!this.turn && this.inflight.size === 0 && this.deliveries.size === 0) return;
    const turn = this.turn;
    const open = [...this.deliveries.values()]
      .filter((delivery) => !turn || delivery.turnGen === turn.gen);
    const pendingItems = open
      .filter((delivery) => delivery.state === 'pending')
      .flatMap((delivery) => delivery.items);
    const items = [...(turn?.items ?? []), ...pendingItems];
    const seqs = [...new Set(items.map((item) => item.seq).filter((seq) => seq != null))];
    const receipts = receiptIds(items);
    for (const delivery of open) this._closeDelivery(delivery, 'abandoned');
    for (const entry of this.inflight.values()) entry.abandoned = true;
    this.inflight.clear();
    this.generation += 1;
    this.turn = null;
    this.modelWorking = false;
    this.toolStarts.clear();
    this.deliveredThroughSeq = this.syncedThroughSeq;
    if (items.length > 0 && this.onTurnAbandoned) {
      try { this.onTurnAbandoned(withReceiptIds({ inboundSeqs: seqs }, receipts)); } catch (error) {
        this.logger.warn('turn_abandoned_notify_failed', { error: safe(error) });
      }
    }
    if (this.connected && this.sessionId) {
      this._request('session/cancel', { sessionId: this.sessionId, keepInbox: false }).catch(() => {});
    }
    this._resolveIdleWaiters();
  },

  _resolveIdleWaiters() {
    const waiters = this.idleWaiters.splice(0);
    for (const resolve of waiters) resolve();
  },

  _notifyAvailable() {
    if (this.turnInProgress || this.queue.length > 0 || !this.connected || this.stopping) return;
    this._resolveIdleWaiters();
    try { this.onAvailable?.(); } catch (error) {
      this.logger.warn('dsh_available_callback_failed', { error: safe(error) });
    }
  }
};
