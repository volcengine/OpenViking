import { createContext, useContext, type ReactNode } from 'react';

const ViewActivityContext = createContext(true);

/** Portaled controls follow the visibility of their owning view. */
export function ViewActivity({
  active,
  children,
}: {
  active: boolean;
  children: ReactNode;
}) {
  const parentActive = useViewActive();
  return (
    <ViewActivityContext.Provider value={active && parentActive}>
      {children}
    </ViewActivityContext.Provider>
  );
}

export const useViewActive = () => useContext(ViewActivityContext);
