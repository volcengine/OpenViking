import { createContext, useContext } from 'react';
import type { ReactNode } from 'react';
import type { Locale } from './locale.js';

const LocaleContext = createContext<Locale>('en-US');

/** A local prop overrides the nearest language scope without changing requests. */
export function useLocale(locale?: Locale): Locale {
  const inherited = useContext(LocaleContext);
  return locale ?? inherited;
}

export function LocaleProvider({
  locale,
  children,
}: {
  locale?: Locale;
  children?: ReactNode;
}) {
  const value = useLocale(locale);
  return (
    <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
  );
}
