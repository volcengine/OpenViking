/** Observe actual viewport exposure, including foreground visibility and clipping. */
export function observeExposure(element: HTMLElement, show: () => void) {
  const document = element.ownerDocument;
  const window = document.defaultView;
  if (!window?.IntersectionObserver) return () => {};
  let intersects = false;
  let stopped = false;
  const ancestors: HTMLElement[] = [];
  const motionEvents = [
    'transitionstart',
    'transitionend',
    'animationstart',
    'animationend',
  ] as const;
  const visible = () => {
    if (
      stopped ||
      !intersects ||
      document.visibilityState !== 'visible' ||
      !element.isConnected
    )
      return;
    if (!element.getClientRects().length) return;
    for (
      let node: HTMLElement | null = element;
      node;
      node = node.parentElement
    ) {
      const style = window.getComputedStyle(node);
      if (
        node.hidden ||
        style.display === 'none' ||
        style.visibility !== 'visible' ||
        style.opacity === '0'
      )
        return;
    }
    stop();
    show();
  };
  const observer = new window.IntersectionObserver(
    (entries) => {
      const entry = entries.filter((item) => item.target === element).at(-1);
      if (!entry) return;
      intersects =
        entry.isIntersecting &&
        entry.intersectionRect.width > 0 &&
        entry.intersectionRect.height > 0;
      visible();
    },
    // Edge contact is already intersecting at ratio 0. Crossing a positive
    // threshold delivers another entry once real pixels enter the viewport.
    { threshold: [0, Number.EPSILON] },
  );
  const refresh = () => {
    if (stopped) return;
    intersects = false;
    // Reobserve instead of reusing a rectangle from before the style change.
    observer.disconnect();
    observer.takeRecords();
    observer.observe(element);
  };
  const mutations = new window.MutationObserver(refresh);
  const motion = (event: Event) => {
    if (event.target === event.currentTarget) refresh();
  };
  const stop = () => {
    stopped = true;
    observer.disconnect();
    mutations.disconnect();
    for (const ancestor of ancestors)
      for (const event of motionEvents)
        ancestor.removeEventListener(event, motion);
    document.removeEventListener('visibilitychange', visible);
  };
  for (
    let node: HTMLElement | null = element;
    node;
    node = node.parentElement
  ) {
    ancestors.push(node);
    mutations.observe(node, {
      attributes: true,
      attributeFilter: ['class', 'style', 'hidden'],
    });
    for (const event of motionEvents) node.addEventListener(event, motion);
  }
  observer.observe(element);
  document.addEventListener('visibilitychange', visible);
  return stop;
}
