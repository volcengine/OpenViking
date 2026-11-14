import { traceLink } from './trace/link.js';
import type { StageName } from '../../client/monitoring/targets.js';
import type { TraceSnapshot } from '../../client/monitoring/trace.js';
import type {
  ComponentName,
  RegionByComponent,
  Source,
} from '../../client/monitoring/source.js';
import { scopeBinding, type MonitoringBinding } from './binding.js';
import { eventId } from './clock.js';
import { startSpan, type MonitoringSpan } from './trace/span.js';
import type { OpenVikingMonitoringEventName } from '../../client/monitoring/types.js';

const blocks = new Set<ComponentName>([
  'FileBrowser',
  'Console',
  'ScopeCascade',
]);
export class MonitoringFrame implements MonitoringBinding {
  live = false;
  private observed?: boolean;
  private lateEnabled = false;
  observeContext(
    enabled: boolean,
    controller?: MonitoringBinding['controller'],
  ) {
    if (
      enabled &&
      this.observed !== undefined &&
      (!this.observed || controller !== this.controller)
    )
      this.lateEnabled = true;
    this.observed = enabled;
  }
  contextGap = (): 'disabled-scope' | undefined =>
    blocks.has(this.name)
      ? undefined
      : this.ancestor && this.ancestor.controller !== this.controller
        ? 'disabled-scope'
        : this.ancestor?.contextGap?.();
  inputs?: () => readonly MonitoringSpan[];
  related?: readonly MonitoringSpan[];
  parent?: MonitoringSpan;
  private id?: string;
  private retries?: Map<StageName, TraceSnapshot['links'][number]>;
  private retryRelease?: () => void;
  retryLink(stage: StageName) {
    return this.retries?.get(stage);
  }
  recordRetry(stage: StageName, link?: TraceSnapshot['links'][number]) {
    if (link) {
      if (!this.controller.options || !this.active()) return;
      (this.retries ??= new Map<
        StageName,
        TraceSnapshot['links'][number]
      >()).set(stage, link);
      this.retryRelease ??= this.controller.retain(() => {
        this.retries = undefined;
        this.retryRelease?.();
        this.retryRelease = undefined;
      }, this.id);
    } else {
      this.retries?.delete(stage);
      if (!this.retries?.size) {
        this.retries = undefined;
        this.retryRelease?.();
        this.retryRelease = undefined;
      }
    }
  }
  constructor(
    readonly name: ComponentName,
    public controller: MonitoringBinding['controller'],
    public ancestor?: MonitoringBinding,
  ) {}
  active = () => this.live;
  dataScope = (): string | undefined =>
    blocks.has(this.name) ? this.id : (this.ancestor?.dataScope?.() ?? this.id);
  leaveScope() {
    if (this.id) {
      this.controller.releaseSource(this.id);
    }
  }
  dispose() {
    this.live = false;
    this.leaveScope();
  }
  source = (region: RegionByComponent[ComponentName] = 'root'): Source => {
    const ancestor = this.ancestor?.source();
    const component = {
      name: this.name,
      instanceId: (this.id ??= eventId()),
      region,
    };
    return {
      component,
      module: blocks.has(this.name)
        ? { name: this.name, instanceId: component.instanceId }
        : (ancestor?.module ?? null),
      componentPath: [...(ancestor?.componentPath ?? []), component],
      affectedComponents: [],
    } as Source;
  };
  renderError(
    error: unknown,
    componentStack?: string | null,
    region: RegionByComponent[ComponentName] = 'root',
    flow?: { spans: readonly MonitoringSpan[]; operation?: unknown },
  ) {
    if (!this.active() || !this.controller.options) return;
    const data = this.inputs?.() ?? [];
    const inputs = flow?.operation
      ? flow.spans
      : data.length
        ? data
        : (flow?.spans ?? this.related ?? []);
    const previous = this.controller.cause(error);
    const parent =
      inputs.length === 1 ? inputs[0] : inputs.length ? null : this.parent;
    const repeated = previous?.rendered.includes(this.id ?? '');
    const retry = previous && repeated;
    const sameOwner = previous?.source.componentPath.some(
      (part) => part.instanceId === this.id,
    );
    const separateCause =
      sameOwner && previous?.trace.traceId !== parent?.trace.traceId;
    const binding = scopeBinding(
      this,
      region,
      retry || separateCause ? null : parent,
      inputs,
    );
    const span = startSpan(
      binding,
      'render',
      retry
        ? 'retry'
        : separateCause
          ? (previous?.trace.trigger ?? 'render')
          : 'render',
      retry
        ? traceLink(previous.snapshot, previous.trace.spanId, 'retry-of')
        : undefined,
    );
    if (!span) return;
    if (
      this.lateEnabled &&
      !inputs.some((input) => input.available()) &&
      !previous
    )
      span.gap('started-before-enabled');
    const name =
      this.name.replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase() +
      '_render_error';
    span.fail(
      name as Extract<OpenVikingMonitoringEventName, `${string}_render_error`>,
      error,
      {},
      'render',
      undefined,
      { componentStack: componentStack ?? undefined },
    );
  }
}
