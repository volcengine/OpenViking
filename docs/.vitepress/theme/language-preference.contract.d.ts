export function languageContract(test: (name: string, run: () => void) => unknown): void;
export function testBrowser(options?: { url?: string; languages?: string[]; language?: string; local?: Record<string, string>; cookie?: string; denied?: boolean; cookieBlocked?: boolean }): { browser: Window; local: Map<string, string>; writes: string[]; setCookie(value: string): void };
