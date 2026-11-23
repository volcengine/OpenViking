import { createContext, useContext } from 'react';
import type { ReactNode } from 'react';

export type UserRootMode = 'all' | 'current';

const UserRootContext = createContext<UserRootMode>('all');

export function UserRootModeProvider({
  mode,
  children,
}: {
  mode?: UserRootMode;
  children?: ReactNode;
}) {
  const inherited = useContext(UserRootContext);
  return (
    <UserRootContext.Provider value={mode ?? inherited}>
      {children}
    </UserRootContext.Provider>
  );
}

export const useUserRootMode = () => useContext(UserRootContext);
