import { QueryClient } from '@tanstack/query-core';
import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

export const OvQueryContext = createContext<QueryClient | null>(null);
export function createQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: 1, staleTime: 5000 } },
  });
}

export function QueryRuntime({
  client,
  children,
}: {
  client: QueryClient;
  children?: ReactNode;
}) {
  // Synchronize Query Core's browser focus/online subscriptions with this Provider.
  // mount/unmount are reference counted; cleanup never clears the host's cache.
  useEffect(() => {
    client.mount();
    return () => client.unmount();
  }, [client]);
  return (
    <OvQueryContext.Provider value={client}>{children}</OvQueryContext.Provider>
  );
}

/** Internal fallback for isolated module tests and modules without a Provider. */
export function useOvQueryClient() {
  const inherited = useContext(OvQueryContext);
  const [fallback] = useState(createQueryClient);
  return inherited ?? fallback;
}
