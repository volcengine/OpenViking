import { compileSessionPatterns, shouldBypassSession, } from "../text-utils.js";
export function createOpenVikingBypassRuntime(options) {
    const bypassSessionPatterns = compileSessionPatterns(options.cfg.bypassSessionPatterns);
    const isBypassedSession = (ctx) => shouldBypassSession(ctx ?? {}, bypassSessionPatterns);
    return {
        isBypassedSession,
    };
}
