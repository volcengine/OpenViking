import type {
  ForwardRefExoticComponent,
  PropsWithoutRef,
  RefAttributes,
} from 'react';
import type { FileBrowserProps } from './types.js';

export interface FileBrowserOpenResourceOptions {
  /** Reveal the row, expanding and refreshing directory targets. Default: true. */
  reveal?: boolean;
}

export interface FileBrowserHandle {
  /**
   * Open a preview and optionally reveal its tree row. Returns false when the row
   * is missing, inaccessible, outside the root, or navigation was superseded.
   * Listings needed to locate the row and required alias lookups reject on failure.
   * In controlled mode the host must accept onUriChange.
   * Links to .abstract.md or .overview.md open their parent at L0 or L1.
   * Directory targets expand and refresh their loaded children.
   * The root refreshes its children and scrolls to the top
   * without creating a tree row. Child refresh failures retain selection and
   * cached children, and appear in the tree's existing error row.
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
