import type {
  RegionByComponent,
  ComponentName,
  ComponentRef,
  Source,
} from './source.js';
export type Spec<
  C extends ComponentName,
  P,
  R extends RegionByComponent[C] = RegionByComponent[C],
> = {
  readonly source: Source<C> & {
    readonly component: ComponentRef<C> & { readonly region: R };
  };
  readonly params: P;
};
