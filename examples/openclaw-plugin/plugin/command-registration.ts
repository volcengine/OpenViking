export type PluginCommandContext = {
  args?: string;
  commandBody?: string;
  sessionKey?: string;
  sessionId?: string;
  agentId?: string;
  ovSessionId?: string;
  senderId?: string;
  requesterSenderId?: string;
};

export type CommandResult = {
  text: string;
  details?: Record<string, unknown>;
};

export type CommandDefinition = {
  name: string;
  description: string;
  acceptsArgs?: boolean;
  requireAuth?: boolean;
  handler: (ctx: PluginCommandContext) => CommandResult | Promise<CommandResult>;
};

export type OpenVikingCommandRegistrationApi = {
  registerCommand?: (command: CommandDefinition) => void;
};

export function registerOpenVikingCommands(
  api: OpenVikingCommandRegistrationApi,
  commands: CommandDefinition[],
): void {
  for (const command of commands) {
    api.registerCommand?.(command);
  }
}
