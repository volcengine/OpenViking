/// <reference types="@rslib/core/types" />

declare module '*.svg?react' {
  import type { FunctionComponent, SVGProps } from 'react';

  const ReactComponent: FunctionComponent<SVGProps<SVGSVGElement>>;
  export default ReactComponent;
}

/** 位图资产由构建工具转成 URL。Bitmaps are turned into URLs by the bundler. */
declare module '*.png' {
  const url: string;
  export default url;
}

declare module '*.webp' {
  const url: string;
  export default url;
}
