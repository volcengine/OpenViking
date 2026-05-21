const _env = globalThis["process"];
export function getEnv(key) {
    return _env.env[key];
}
