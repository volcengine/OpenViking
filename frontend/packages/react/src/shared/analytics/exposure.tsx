import { cloneElement } from 'react';
import type { ReactElement, Ref } from 'react';
import { useExposure } from './use-exposure.js';
import type { Params, ShowKey } from './reporter.js';

/** Private, DOM-free exposure attachment; children must forward a DOM ref and have no existing ref. */
export function Exposure<K extends ShowKey>({
  name,
  identity,
  params,
  children,
}: {
  name: K;
  identity?: unknown;
  params?: () => Params<K>;
  children: ReactElement<{ ref?: Ref<HTMLElement> }>;
}) {
  const ref = useExposure(name, identity, params);
  return cloneElement(children, { ref });
}
