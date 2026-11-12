import type {
  VikingBotSessionService,
  VikingBotSessionsState,
} from './types.js';

export function createSessionsController(
  service: VikingBotSessionService,
  enabled = true,
  parent?: AbortSignal,
) {
  let state: VikingBotSessionsState = { sessions: [], isLoading: false };
  const listeners = new Set<() => void>();
  let active = false;
  let owner = new AbortController();
  let revision = 0;
  let mutation = false;
  let loading: AbortController | undefined;
  const update = (patch: Partial<VikingBotSessionsState>) => {
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };
  const signal = () =>
    parent ? AbortSignal.any([owner.signal, parent]) : owner.signal;
  const refresh = async () => {
    if (!active) return;
    loading?.abort();
    const controller = new AbortController();
    loading = controller;
    const version = ++revision;
    const requestSignal = AbortSignal.any([signal(), controller.signal]);
    update({ isLoading: true, error: undefined });
    try {
      const sessions = await service.listSessions(requestSignal);
      requestSignal.throwIfAborted();
      if (active && version === revision) update({ sessions });
    } catch (error) {
      if (active && version === revision && !requestSignal.aborted)
        update({
          error: error instanceof Error ? error : new Error(String(error)),
        });
    } finally {
      if (active && version === revision) update({ isLoading: false });
    }
  };
  return {
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getSnapshot: () => state,
    start() {
      active = enabled;
      owner = new AbortController();
      void refresh();
      return () => {
        active = false;
        revision++;
        owner.abort();
        loading?.abort();
      };
    },
    refresh,
    create: async () => {
      if (!active || mutation) return;
      mutation = true;
      const requestSignal = signal();
      update({ error: undefined });
      try {
        const session = await service.createSession(requestSignal);
        if (!active || requestSignal.aborted) return;
        revision++;
        loading?.abort();
        update({
          sessions: [
            session,
            ...state.sessions.filter((item) => item.id !== session.id),
          ],
          isLoading: false,
        });
        return session;
      } catch (error) {
        if (active && !requestSignal.aborted)
          update({
            error: error instanceof Error ? error : new Error(String(error)),
          });
      } finally {
        mutation = false;
      }
    },
    remove: async (id: string) => {
      if (!active || mutation) return false;
      mutation = true;
      const requestSignal = signal();
      update({ error: undefined });
      try {
        await service.removeSession(id, requestSignal);
        if (!active || requestSignal.aborted) return false;
        revision++;
        loading?.abort();
        update({
          sessions: state.sessions.filter((item) => item.id !== id),
          isLoading: false,
        });
        return true;
      } catch (error) {
        if (active && !requestSignal.aborted)
          update({
            error: error instanceof Error ? error : new Error(String(error)),
          });
        return false;
      } finally {
        mutation = false;
      }
    },
  };
}
