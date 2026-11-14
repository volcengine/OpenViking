/** Model IO threshold crossings; style and mutation delivery use the real DOM. */
export function exposureObserver() {
  const observers = [];
  const originalObserver = window.IntersectionObserver;
  const originalRects = HTMLElement.prototype.getClientRects;
  HTMLElement.prototype.getClientRects = () => [{ width: 100, height: 100 }];
  window.IntersectionObserver = class {
    targets = new Map();
    constructor(callback, { threshold = 0 } = {}) {
      this.callback = callback;
      this.thresholds = [threshold].flat().sort((a, b) => a - b);
      observers.push(this);
    }
    observe(target) {
      this.targets.set(target, { index: -1, intersects: false });
    }
    disconnect() {
      this.targets.clear();
    }
    takeRecords() {
      return [];
    }
    intersect(ratio, intersects) {
      for (const [target, previous] of [...this.targets]) {
        const index = this.thresholds.filter((value) => value <= ratio).length;
        if (index === previous.index && intersects === previous.intersects)
          continue;
        this.targets.set(target, { index, intersects });
        this.callback([
          {
            target,
            isIntersecting: intersects,
            intersectionRatio: ratio,
            intersectionRect: { width: 100, height: ratio * 100 },
          },
        ]);
      }
    }
  };
  return {
    expose(ratio = 1, intersects = true) {
      for (const observer of observers) observer.intersect(ratio, intersects);
    },
    get active() {
      return observers.reduce(
        (count, observer) => count + observer.targets.size,
        0,
      );
    },
    restore() {
      window.IntersectionObserver = originalObserver;
      HTMLElement.prototype.getClientRects = originalRects;
    },
  };
}
