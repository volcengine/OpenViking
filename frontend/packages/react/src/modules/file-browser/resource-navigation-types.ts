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
   *
   * A directory summary file (`.abstract.md` or `.overview.md`) opens its owning
   * directory on the matching level, L0 or L1, and reports the directory URI.
   */
  openResource: (
    uri: string,
    options?: FileBrowserOpenResourceOptions,
  ) => Promise<boolean>;
  /**
   * Reload the tree and the preview in place after the host changed resources,
   * keeping expanded directories and re-revealing the selected resource, which
   * may sit on a later page. Rejects if the reload or the re-reveal fails.
   */
  refresh: () => Promise<void>;
}

/** React 18 and 19 ref contract; internal loaders remain private. */
export type FileBrowserComponent = ForwardRefExoticComponent<
  PropsWithoutRef<FileBrowserProps> & RefAttributes<FileBrowserHandle>
>;
