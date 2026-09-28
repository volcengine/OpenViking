export type SiteLanguage = 'en' | 'zh' | 'zh-TW' | 'ja' | 'ko' | 'es';
export type LanguagePreference = SiteLanguage | 'auto';
export interface LanguagePolicy {
  key: string;
  parse(value: unknown): SiteLanguage | undefined;
  read(): LanguagePreference;
  preferred(): SiteLanguage;
  query(names?: string[]): SiteLanguage | undefined;
  resolve(explicit?: string | null, bilingual?: false): SiteLanguage;
  resolve(explicit: string | null | undefined, bilingual: true): 'en' | 'zh';
  save(value: LanguagePreference): void;
  clearQuery(names?: string[]): void;
  subscribe(listener: () => void): () => void;
  refresh(): void;
}
export function createLanguagePreference(browser?: Window): LanguagePolicy;
