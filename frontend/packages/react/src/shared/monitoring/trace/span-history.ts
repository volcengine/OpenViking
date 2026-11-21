import { traceLink } from './link.js';
import type {
  SpanSnapshot,
  TraceSnapshot,
} from '../../../client/monitoring/trace.js';
import type { StageName } from '../../../client/monitoring/targets.js';
import type { MonitoringSpan } from './span.js';

export class SpanHistory {
  nodes: SpanSnapshot[] = [];
  pending = new Set<MonitoringSpan>();
  sequence = 0;
  counts = new Map<StageName, number>();
  total = 0;
  gaps = new Set<TraceSnapshot['gaps'][number]>();
  links?: TraceSnapshot['links'][number][];
  disposed = false;
  private releases?: Set<() => void>;
  retain(release: () => void) {
    (this.releases ??= new Set()).add(release);
    return () => this.releases?.delete(release);
  }
  add(span: MonitoringSpan) {
    this.pending.add(span);
    this.total++;
    this.counts.set(
      span.node.stage,
      (this.counts.get(span.node.stage) ?? 0) + 1,
    );
    this.nodes.push(span.node);
    if (this.nodes.length > 64) this.nodes.splice(1, 1);
  }
  link(
    parent: MonitoringSpan,
    relation: TraceSnapshot['links'][number]['relation'],
  ) {
    this.addLink(
      traceLink(parent.snapshot(false), parent.trace.spanId, relation),
    );
  }
  addLink(link: TraceSnapshot['links'][number]) {
    (this.links ??= []).push(link);
    if (this.links.length > 8) {
      this.links.shift();
      this.gaps.add('capacity');
    }
  }
  cancelAfter(parent: MonitoringSpan, reason?: SpanSnapshot['endReason']) {
    for (const span of this.pending) {
      let ancestor = span.parentSpan;
      while (ancestor && ancestor !== parent) ancestor = ancestor.parentSpan;
      if (ancestor) span.finish('cancelled', reason);
    }
  }
  cancelSource(instanceId: string) {
    for (const span of this.pending)
      if (span.node.source.component.instanceId === instanceId)
        span.finish('cancelled', 'unmount');
  }
  clearRaw() {
    this.releases?.forEach((release) => release());
    this.releases = undefined;
  }
  dispose() {
    this.disposed = true;
    this.clearRaw();
    for (const span of this.pending) span.finish('cancelled', 'scope-change');
    this.pending.clear();
    this.nodes = [];
    this.counts.clear();
    this.gaps.clear();
    this.links = undefined;
  }
}
