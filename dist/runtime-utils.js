const _env = globalThis["process"];
export function sysEnv() {
    return _env.env;
}
export function getEnv(key) {
    return _env.env[key];
}
