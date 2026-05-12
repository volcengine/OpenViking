const _env = globalThis["process"];
export function sysEnv(): NodeJS.ProcessEnv {
  return _env.env;
}

export function getEnv(key: string): string | undefined {
  return _env.env[key];
}
