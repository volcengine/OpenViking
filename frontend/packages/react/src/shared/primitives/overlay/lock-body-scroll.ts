interface BodyScrollLock {
  owners: Set<symbol>;
  styles: { property: string; value: string; priority: string }[];
}

// Body is a shared browser resource; each overlay owns a separate release token.
const scrollLocks = new WeakMap<HTMLElement, BodyScrollLock>();

export function lockBodyScroll(body: HTMLElement) {
  const existing = scrollLocks.get(body);
  const lock = existing ?? {
    owners: new Set<symbol>(),
    styles: ['overflow-x', 'overflow-y', 'padding-right'].map((property) => ({
      property,
      value: body.style.getPropertyValue(property),
      priority: body.style.getPropertyPriority(property),
    })),
  };
  if (!existing) {
    scrollLocks.set(body, lock);
    if (body.style.overflowY !== 'hidden' && body.style.overflow !== 'hidden') {
      const doc = body.ownerDocument;
      const view = doc.defaultView;
      const scrollbar =
        (view?.innerWidth ?? 0) - doc.documentElement.clientWidth;
      if (view && scrollbar > 0) {
        const padding =
          Number.parseFloat(view.getComputedStyle(body).paddingRight) || 0;
        body.style.setProperty(
          'padding-right',
          `${padding + scrollbar}px`,
          body.style.getPropertyPriority('padding-right'),
        );
      }
    }
    // A shorthand can serialize as empty when only one axis or priority is set.
    // Modify and restore longhands independently so host declarations survive.
    for (const property of ['overflow-x', 'overflow-y']) {
      body.style.setProperty(
        property,
        'hidden',
        body.style.getPropertyPriority(property),
      );
    }
  }
  const owner = Symbol();
  lock.owners.add(owner);
  return () => {
    if (!lock.owners.delete(owner) || lock.owners.size > 0) return;
    scrollLocks.delete(body);
    for (const { property, value, priority } of lock.styles) {
      body.style.setProperty(property, value, priority);
    }
  };
}
