import { describe, expect, it, vi } from "vitest";
import {
  DEFAULT_SECRET_KEY,
  runSetApiKeyCommand,
  type InputProvider,
  type SecretStorageLike,
} from "../commands-core";

function makeSecrets(initial: Record<string, string> = {}): SecretStorageLike & {
  store: ReturnType<typeof vi.fn>;
  get: ReturnType<typeof vi.fn>;
  delete: ReturnType<typeof vi.fn>;
} {
  const map = new Map(Object.entries(initial));
  return {
    get: vi.fn(async (k: string) => map.get(k)),
    store: vi.fn(async (k: string, v: string) => { map.set(k, v); }),
    delete: vi.fn(async (k: string) => { map.delete(k); }),
  };
}

function makeInput(scriptedValue: string | undefined): InputProvider & {
  prompt: ReturnType<typeof vi.fn>;
  showInfo: ReturnType<typeof vi.fn>;
} {
  return {
    prompt: vi.fn(async () => scriptedValue),
    showInfo: vi.fn(async () => {}),
  };
}

describe("runSetApiKeyCommand — happy path", () => {
  it("prompts with password:true and stores the value in SecretStorage", async () => {
    const secrets = makeSecrets();
    const input = makeInput("sk-live-test-key");
    const res = await runSetApiKeyCommand(secrets, input);

    expect(res.saved).toBe(true);
    expect(input.prompt).toHaveBeenCalledTimes(1);
    expect(input.prompt.mock.calls[0]![0]!.password).toBe(true);
    expect(secrets.store).toHaveBeenCalledWith(DEFAULT_SECRET_KEY, "sk-live-test-key");
    expect(input.showInfo).toHaveBeenCalled();
  });

  it("trims whitespace before storing", async () => {
    const secrets = makeSecrets();
    const input = makeInput("   sk-trimmed   ");
    await runSetApiKeyCommand(secrets, input);
    expect(secrets.store).toHaveBeenCalledWith(DEFAULT_SECRET_KEY, "sk-trimmed");
  });

  it("honours a custom secretKey override", async () => {
    const secrets = makeSecrets();
    const input = makeInput("custom-key-value");
    await runSetApiKeyCommand(secrets, input, { secretKey: "custom.bucket" });
    expect(secrets.store).toHaveBeenCalledWith("custom.bucket", "custom-key-value");
  });

  it("attaches a non-empty validate function to the prompt", async () => {
    const secrets = makeSecrets();
    const input = makeInput("sk-x");
    await runSetApiKeyCommand(secrets, input);
    const opts = input.prompt.mock.calls[0]![0]!;
    expect(typeof opts.validate).toBe("function");
    expect(opts.validate!("")).not.toBeNull();
    expect(opts.validate!("ok")).toBeNull();
  });
});

describe("runSetApiKeyCommand — cancel / empty paths", () => {
  it("returns saved:false reason:cancelled when the user cancels (undefined)", async () => {
    const secrets = makeSecrets();
    const input = makeInput(undefined);
    const res = await runSetApiKeyCommand(secrets, input);
    expect(res.saved).toBe(false);
    expect(res.reason).toBe("cancelled");
    expect(secrets.store).not.toHaveBeenCalled();
    expect(input.showInfo).not.toHaveBeenCalled();
  });

  it("returns saved:false reason:empty for whitespace-only input", async () => {
    const secrets = makeSecrets();
    const input = makeInput("   \n  ");
    const res = await runSetApiKeyCommand(secrets, input);
    expect(res.saved).toBe(false);
    expect(res.reason).toBe("empty");
    expect(secrets.store).not.toHaveBeenCalled();
  });

  it("returns saved:false reason:empty for an empty string", async () => {
    const secrets = makeSecrets();
    const input = makeInput("");
    const res = await runSetApiKeyCommand(secrets, input);
    expect(res.saved).toBe(false);
    expect(res.reason).toBe("empty");
  });
});
