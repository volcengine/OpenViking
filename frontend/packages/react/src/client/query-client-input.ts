/**
 * Stable public surface of a native TanStack Query v5 client. Using this input
 * shape avoids coupling host clients to a second copy's private class brand.
 * The original instance is used directly, without a proxy or changed defaults.
 */
export interface QueryClientInput {
  mount(): void;
  unmount(): void;
  getQueryCache(): unknown;
  getMutationCache(): unknown;
  getDefaultOptions(): unknown;
  defaultQueryOptions: (...args: never[]) => unknown;
  fetchQuery: (...args: never[]) => Promise<unknown>;
  fetchInfiniteQuery: (...args: never[]) => Promise<unknown>;
}
