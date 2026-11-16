import type { MonitoringBinding } from '../../../shared/monitoring/binding.js';
import { workflowForBinding } from '../../../shared/monitoring/workflow.js';
import {
  scopeService,
  isNativeService,
} from '../../../shared/monitoring/service.js';
import type {
  VikingBotSessionService,
  VikingBotSessionsState,
} from './types.js';

export function createSessionsController(
  service: VikingBotSessionService,
  enabled = true,
  parent?: AbortSignal,
  monitoring?: MonitoringBinding,
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
    const operation = workflowForBinding(
      monitoring,
      'console_session_list_load',
      requestSignal,
    );
    update({ isLoading: true, error: undefined });
    try {
      const sessions = await scopeService(
        service,
        operation?.span,
      ).listSessions(requestSignal);
      requestSignal.throwIfAborted();
      if (active && version === revision) {
        update({ sessions });
        operation?.ready({ sessionCount: sessions.length });
      }
    } catch (error) {
      operation?.fail(
        error,
        isNativeService(service) ? 'protocol' : 'host-service',
        requestSignal,
      );
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
      const operation = workflowForBinding(
        monitoring,
        'console_session_create',
        requestSignal,
      );
      update({ error: undefined });
      try {
        const session = await scopeService(
          service,
          operation?.span,
        ).createSession(requestSignal);
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
        operation?.ready({});
        return session;
      } catch (error) {
        operation?.fail(
          error,
          isNativeService(service) ? 'protocol' : 'host-service',
          requestSignal,
        );
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
      const operation = workflowForBinding(
        monitoring,
        'console_session_delete',
        requestSignal,
      );
      update({ error: undefined });
      try {
        await scopeService(service, operation?.span).removeSession(
          id,
          requestSignal,
        );
        if (!active || requestSignal.aborted) return false;
        revision++;
        loading?.abort();
        update({
          sessions: state.sessions.filter((item) => item.id !== id),
          isLoading: false,
        });
        operation?.ready({});
        return true;
      } catch (error) {
        operation?.fail(
          error,
          isNativeService(service) ? 'protocol' : 'host-service',
          requestSignal,
        );
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
