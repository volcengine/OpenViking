/**
 * VS Code-free helper for the `OpenViking: Set API Key` command.
 *
 * Splits the prompt + persistence flow out of `commands.ts` so we
 * can unit-test it with a duck-typed `SecretStorageLike` and a
 * scripted `InputProvider` — no VS Code runtime needed.
 */

export interface SecretStorageLike {
  get(key: string): Promise<string | undefined>;
  store(key: string, value: string): Promise<void>;
  delete(key: string): Promise<void>;
}

export interface InputPromptOptions {
  password: boolean;
  prompt: string;
  placeholder?: string;
  /** Return null on success or a user-visible error message on failure. */
  validate?: (value: string) => string | null;
}

export interface InputProvider {
  /** Resolves with the entered value, or undefined when the user cancels. */
  prompt(opts: InputPromptOptions): Promise<string | undefined>;
  /** Show a non-blocking info message. */
  showInfo(message: string): Promise<void>;
}

export interface SetApiKeyOptions {
  /** SecretStorage key (defaults to `openviking.apiKey`). */
  secretKey?: string;
}

export interface SetApiKeyResult {
  saved: boolean;
  /** When false, the reason — for testing + telemetry. */
  reason?: "cancelled" | "empty";
}

export const DEFAULT_SECRET_KEY = "openviking.apiKey" as const;

/**
 * Prompt the user for the API key (with `password: true` so the
 * input is masked) and persist it to SecretStorage. When the user
 * cancels or enters whitespace-only text, returns without storing.
 */
export async function runSetApiKeyCommand(
  secrets: SecretStorageLike,
  input: InputProvider,
  opts: SetApiKeyOptions = {},
): Promise<SetApiKeyResult> {
  const key = opts.secretKey ?? DEFAULT_SECRET_KEY;

  const value = await input.prompt({
    password: true,
    prompt: "OpenViking API Key",
    placeholder: "sk-... (stored in VS Code SecretStorage)",
    validate: (v) => (v.trim().length === 0 ? "API key cannot be empty." : null),
  });

  if (value === undefined) {
    return { saved: false, reason: "cancelled" };
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return { saved: false, reason: "empty" };
  }

  await secrets.store(key, trimmed);
  await input.showInfo(
    "OpenViking API key saved. Reload the window for the new key to take effect.",
  );
  return { saved: true };
}
