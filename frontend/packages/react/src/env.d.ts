/// <reference types="@rslib/core/types" />

declare module '*.svg?react' {
  import type { FunctionComponent, SVGProps } from 'react';

  const ReactComponent: FunctionComponent<SVGProps<SVGSVGElement>>;
  export default ReactComponent;
}
