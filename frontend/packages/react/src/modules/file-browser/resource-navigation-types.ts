import type {
  ForwardRefExoticComponent,
  PropsWithoutRef,
  RefAttributes,
} from 'react';
import type { FileBrowserProps } from './types.js';

export interface FileBrowserOpenResourceOptions {
  /** Expand ancestors, load missing pages and scroll to the selected row. Default: true. */
  reveal?: boolean;
}

export interface FileBrowserHandle {
  /**
   * Open a preview and optionally reveal its tree row. Returns false when the row
   * is missing, inaccessible, outside the root, or navigation was superseded.
   * Listing and required alias lookup failures reject. In controlled mode the
   * host must accept onUriChange.
   * Links to .abstract.md or .overview.md open their parent at L0 or L1.
   */
  openResource: (
    uri: string,
    options?: FileBrowserOpenResourceOptions,
  ) => Promise<boolean>;
}

/** React 18 and 19 ref contract; internal loaders remain private. */
export type FileBrowserComponent = ForwardRefExoticComponent<
  PropsWithoutRef<FileBrowserProps> & RefAttributes<FileBrowserHandle>
>;
