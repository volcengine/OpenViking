export declare function defaultLedgerDir(): string;
export declare function ledgerKey(messageIdentity: string, originalContent: string): string;
export declare class RecallLedger {
  constructor(dir?: string);
  open(sessionId: string): void;
  get isOpen(): boolean;
  get(key: string): string | null;
  record(key: string, block: string): void;
  flush(): void;
}
