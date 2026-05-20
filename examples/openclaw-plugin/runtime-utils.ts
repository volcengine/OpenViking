const _env = globalThis["process"];

export function getEnv(key: string): string | undefined {
  return _env.env[key];
}
