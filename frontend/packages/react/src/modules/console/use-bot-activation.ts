import { useEffect, useMemo, useSyncExternalStore } from 'react';
import type {
  ConsoleBotActivation,
  ConsoleBotActivationStatus,
} from './types.js';

export type BotActivationState =
  | {
      status: ConsoleBotActivationStatus | 'disabled' | 'checking' | 'starting';
    }
  | { status: 'collection-error'; error: unknown }
  | { status: 'load-error'; error: unknown }
  | { status: 'start-error'; error: unknown };

function createBotActivation({
  activation,
  enabled,
  signal: scopeSignal,
}: {
  activation?: ConsoleBotActivation;
  enabled: boolean;
  signal?: AbortSignal;
}) {
  let state: BotActivationState = {
    status:
      !enabled || (activation && scopeSignal?.aborted)
        ? 'disabled'
        : activation
          ? 'checking'
          : 'ready',
  };
  let active = false;
  let request: AbortController | undefined;
  const listeners = new Set<() => void>();
  const update = (next: BotActivationState) => {
    state = next;
    listeners.forEach((listener) => listener());
  };

  async function run(operation: 'load' | 'start' | 'retry-start') {
    if (!active || !activation || request || scopeSignal?.aborted) return;
    const controller = new AbortController();
    request = controller;
    const signal = scopeSignal
      ? AbortSignal.any([scopeSignal, controller.signal])
      : controller.signal;
    const current = () => active && request === controller && !signal.aborted;
    let failure: 'collection-error' | 'start-error' | 'load-error' =
      'collection-error';
    const readStatus = async () => {
      failure = 'collection-error';
      const eligible = await activation.isEligible(signal);
      signal.throwIfAborted();
      failure = 'load-error';
      return eligible === true ? activation.getStatus(signal) : 'disabled';
    };
    update({ status: operation === 'load' ? 'checking' : 'starting' });
    try {
      if (operation === 'retry-start') {
        // A rejected response may still have started the bot on the backend.
        const status = await readStatus();
        if (!current()) return;
        if (status !== 'not-started') {
          update({ status });
          return;
        }
      }
      if (operation !== 'load') {
        failure = 'start-error';
        await activation.start(signal);
        if (!current()) return;
      }
      const status = await readStatus();
      if (current()) update({ status });
    } catch (error) {
      if (current()) update({ status: failure, error });
    } finally {
      if (request === controller) request = undefined;
    }
  }

  function cancel() {
    request?.abort();
    request = undefined;
  }

  return {
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
    getSnapshot: () => state,
    connect() {
      if (!enabled || !activation) return;
      active = true;
      const disable = () => {
        cancel();
        update({ status: 'disabled' });
      };
      if (scopeSignal?.aborted) disable();
      else {
        scopeSignal?.addEventListener('abort', disable, { once: true });
        void run('load');
      }
      return () => {
        active = false;
        scopeSignal?.removeEventListener('abort', disable);
        cancel();
      };
    },
    start: () => {
      if (state.status === 'not-started') void run('start');
    },
    retry: () => {
      if (state.status === 'start-error') void run('retry-start');
      else if (
        state.status === 'load-error' ||
        state.status === 'collection-error'
      )
        void run('load');
    },
  };
}

export function useBotActivation({
  activation,
  enabled,
  scopeKey,
  signal,
}: {
  activation?: ConsoleBotActivation;
  enabled: boolean;
  scopeKey?: string;
  signal?: AbortSignal;
}) {
  const lifecycle = useMemo(() => {
    // A host can change identity behind stable request callbacks.
    void scopeKey;
    return createBotActivation({ activation, enabled, signal });
  }, [activation, enabled, scopeKey, signal]);
  // Synchronize the current provider's activation status after mount and scope
  // changes, which may occur without a click. Cleanup aborts requests from the
  // previous scope so late responses cannot enable its replacement.
  useEffect(() => lifecycle.connect(), [lifecycle]);
  const state = useSyncExternalStore(
    lifecycle.subscribe,
    lifecycle.getSnapshot,
  );
  return { state, start: lifecycle.start, retry: lifecycle.retry };
}
