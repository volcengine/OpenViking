import type { MonitoringSpan } from './trace/span.js';

/** Finished histories are owned by their data, not kept alive by this registry. */
export class MonitoringRoots {
  private roots = new Set<WeakRef<MonitoringSpan>>();
  private count = 0;
  add(root: MonitoringSpan) {
    this.roots.add(new WeakRef(root));
    if (++this.count % 64 === 0) {
      for (const reference of this.roots)
        if (!reference.deref()) this.roots.delete(reference);
    }
  }
  clear() {
    this.roots.forEach((reference) => reference.deref()?.dispose());
    this.roots.clear();
  }
  releaseSource(instanceId: string) {
    for (const reference of this.roots) {
      const root = reference.deref();
      if (!root) this.roots.delete(reference);
      else if (root.node.source.component.instanceId === instanceId) {
        root.dispose();
        this.roots.delete(reference);
      } else root.cancelSource(instanceId);
    }
  }
}
