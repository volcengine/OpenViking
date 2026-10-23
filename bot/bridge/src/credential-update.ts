type ErrorLogger = (message: string) => void;

export function createCredentialUpdateHandler(
  saveCreds: () => Promise<void>,
  logError: ErrorLogger = console.error,
): () => Promise<void> {
  return async () => {
    try {
      await saveCreds();
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      logError(`Failed to save WhatsApp credentials: ${detail}`);
    }
  };
}
