import type { OvClient } from '../../client/types.js';
import { bindingOf } from '../../shared/monitoring/client.js';
import { now } from '../../shared/monitoring/clock.js';
import { tagModel } from '../../shared/monitoring/data.js';
import {
  beginWorkflow,
  workflowsForData,
  type Workflow,
} from '../../shared/monitoring/workflow.js';
import type { WorkflowTarget } from '../../shared/monitoring/workflows.js';

/** Each subscriber keeps its own observable lifetime when another consumer owns the shared fetch. */
export class QueryObservation<T extends WorkflowTarget> {
  private fetching = false;
  private observed?: { started: number; epoch: number };
  private workflow?: Workflow<T>;
  constructor(
    private client: OvClient | null,
    private target: T,
  ) {}
  cancel() {
    this.observed = undefined;
    this.fetching = false;
  }
  operations(query: object) {
    return this.workflow?.span.available()
      ? [this.workflow]
      : workflowsForData(this.client, [query], this.target);
  }
  observe(
    result: { isFetching: boolean; data: unknown; error: unknown },
    query: { state: { fetchStatus: string } },
  ) {
    const binding = bindingOf(this.client);
    const fetching = query.state.fetchStatus !== 'idle';
    if (
      fetching &&
      !this.fetching &&
      binding?.active() &&
      binding.controller.options
    )
      this.observed = { started: now(), epoch: binding.controller.epoch };
    this.fetching = fetching;
    if (fetching || !this.observed) return;
    const observed = this.observed;
    this.observed = undefined;
    if (
      !binding?.active() ||
      !binding.controller.options ||
      binding.controller.epoch !== observed.epoch
    )
      return;
    const known = workflowsForData(this.client, [query], this.target);
    if (result.error) {
      const cause = binding.controller.cause(result.error);
      const source = cause?.snapshot.spans.find(
        (span) => span.spanId === cause.trace.spanId,
      )?.source;
      if (source?.module?.instanceId === binding.source().module?.instanceId)
        return;
    } else {
      if (result.data === undefined) return;
      if (known.some((operation) => operation.span.node.status === 'running')) {
        this.workflow = undefined;
        return;
      }
    }
    const operation = beginWorkflow(this.client, this.target);
    if (!operation) return;
    operation.span.gap('shared-query-input');
    operation.span.timing(observed.started, now(), Date.now());
    if (result.error) operation.fail(result.error);
    else {
      this.workflow = operation;
      tagModel(binding, result.data, operation.span);
    }
  }
}
