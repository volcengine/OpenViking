import type { StageName } from '../../client/monitoring/targets.js';
import { MonitoringRoots } from './roots.js';
import type { MonitoringData } from './data.js';
import type {
  OpenVikingMonitoringEvent,
  OpenVikingMonitoringEventName,
  OpenVikingMonitoringOptions,
} from '../../client/monitoring/types.js';
import type { OpenVikingMonitoringEventMap } from '../../client/monitoring/events.js';
import type { Source } from '../../client/monitoring/source.js';
import type { TraceContext } from '../../client/monitoring/trace.js';
import { eventId, libraryVersion } from './clock.js';
import type { FaultRecord } from './trace/span.js';
import { diagnostics } from './diagnostics.js';

export class MonitoringController {
  options: OpenVikingMonitoringOptions | false = false;
  epoch = 0;
  private attempts?: Map<string, Map<StageName, number>>;
  nextAttempt(source: string, stage: StageName) {
    const scopes = (this.attempts ??= new Map<
      string,
      Map<StageName, number>
    >());
    let counts = scopes.get(source);
    if (!counts) scopes.set(source, (counts = new Map<StageName, number>()));
    const value = (counts.get(stage) ?? 0) + 1;
    counts.set(stage, value);
    return value;
  }
  data?: MonitoringData;
  roots?: MonitoringRoots;
  observeRoot(root: import('./trace/span.js').MonitoringSpan) {
    (this.roots ??= new MonitoringRoots()).add(root);
  }
  private causes?: WeakMap<object, FaultRecord>;
  cause(error: unknown) {
    return error !== null && typeof error === 'object'
      ? this.causes?.get(error)
      : undefined;
  }
  remember(error: unknown, record: FaultRecord) {
    if (this.options && error !== null && typeof error === 'object')
      (this.causes ??= new WeakMap()).set(error, record);
  }
  private releases?: Set<() => void>;
  private scopes?: Map<string, Set<() => void>>;
  private rawReleases?: Set<() => void>;

  configure(options: OpenVikingMonitoringOptions | false) {
    this.options = options && options.events?.length !== 0 ? options : false;
    if (!this.options || !this.options.diagnostics) {
      this.rawReleases?.forEach((release) => release());
      this.rawReleases = undefined;
    }
    if (!this.options) {
      this.epoch++;
      this.roots?.clear();
      this.roots = undefined;
      this.releases?.forEach((release) => release());
      this.releases = undefined;
      this.scopes = undefined;
      this.causes = undefined;
      this.data = undefined;
      this.attempts = undefined;
    }
  }

  retain(release: () => void, scope?: string) {
    (this.releases ??= new Set()).add(release);
    if (scope) {
      const scopes = (this.scopes ??= new Map<string, Set<() => void>>());
      let callbacks = scopes.get(scope);
      if (!callbacks) scopes.set(scope, (callbacks = new Set<() => void>()));
      callbacks.add(release);
    }
    return () => {
      this.releases?.delete(release);
      if (scope) {
        const callbacks = this.scopes?.get(scope);
        callbacks?.delete(release);
        if (!callbacks?.size) this.scopes?.delete(scope);
      }
    };
  }
  retainRaw(release: () => void) {
    (this.rawReleases ??= new Set()).add(release);
    return () => this.rawReleases?.delete(release);
  }
  releaseSource(instanceId: string) {
    this.roots?.releaseSource(instanceId);
    this.scopes?.get(instanceId)?.forEach((release) => release());
    this.scopes?.delete(instanceId);
    this.data?.delete(instanceId);
    this.attempts?.delete(instanceId);
  }

  emit<N extends OpenVikingMonitoringEventName>(
    name: N,
    source: Source,
    trace: TraceContext,
    params: OpenVikingMonitoringEventMap[N]['params'],
    raw?: Parameters<typeof diagnostics>[1],
    occurredAtUnixMs = Date.now(),
  ) {
    const options = this.options;
    if (!options) return;
    const id = eventId();
    if (options.events && !options.events.includes(name)) return id;
    try {
      const event = {
        schemaVersion: 1,
        libraryVersion,
        eventId: id,
        occurredAtUnixMs,
        name,
        source,
        trace,
        params,
        diagnostics: raw && diagnostics(options, raw),
      } as OpenVikingMonitoringEvent;
      if (this.options !== options) return id;
      const result = options.onEvent(
        JSON.parse(JSON.stringify(event)) as OpenVikingMonitoringEvent,
      );
      if (result) void Promise.resolve(result).catch(() => {});
      return event.eventId;
    } catch {
      // Monitoring is an external observer, never part of a business result.
      return id;
    }
  }
}
