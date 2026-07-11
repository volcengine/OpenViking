export function registerOpenVikingCommands(api, commands) {
    for (const command of commands) {
        api.registerCommand?.(command);
    }
}
