import { useLayoutEffect } from 'react';
import type { Workflow, WorkflowPayload } from './workflow.js';
import type { WorkflowTarget } from './workflows.js';

/** Publish readiness to the host only after the corresponding state commits. */
export function useWorkflowCommit<T extends WorkflowTarget>(
  operation: Workflow<T> | undefined,
  ready: boolean,
  payload: () => WorkflowPayload<T>,
) {
  // This synchronizes committed UI readiness with the host's external receiver.
  useLayoutEffect(() => {
    if (ready && operation) operation.ready(payload());
  }, [operation, ready, payload]);
}
