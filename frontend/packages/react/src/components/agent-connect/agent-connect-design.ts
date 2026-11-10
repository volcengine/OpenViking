import CodexIcon from '../../../../icons/svg/agent-connect/codex.svg?react';
import ClaudeCodeIcon from '../../../../icons/svg/agent-connect/claude-code.svg?react';
import DoubaoIcon from '../../../../icons/svg/agent-connect/doubao.svg?react';
import WorkbuddyIcon from '../../../../icons/svg/agent-connect/workbuddy.svg?react';
import TraeIcon from '../../../../icons/svg/agent-connect/trae.svg?react';
import CursorIcon from '../../../../icons/svg/agent-connect/cursor.svg?react';
import OpenclawIcon from '../../../../icons/svg/agent-connect/openclaw.svg?react';
import HermesIcon from '../../../../icons/svg/agent-connect/hermes.svg?react';
import OpencodeIcon from '../../../../icons/svg/agent-connect/opencode.svg?react';
import DeepseekIcon from '../../../../icons/svg/agent-connect/deepseek-harness.svg?react';
import DeerflowIcon from '../../../../icons/svg/agent-connect/deerflow.svg?react';
import PiIcon from '../../../../icons/svg/agent-connect/pi.svg?react';
import ZcodeIcon from '../../../../icons/svg/agent-connect/zcode.svg?react';
import KimiCodeIcon from '../../../../icons/svg/agent-connect/kimi-code.svg?react';
import McpIcon from '../../../../icons/svg/agent-connect/mcp.svg?react';
import CliIcon from '../../../../icons/svg/agent-connect/cli.svg?react';
import ApiIcon from '../../../../icons/svg/agent-connect/api.svg?react';
import SdkIcon from '../../../../icons/svg/agent-connect/sdk.svg?react';
import { resolveAgentConnectLabels } from './agent-connect-copy.js';
import { getAgentConnectContent } from './agent-connect-instructions.js';
import type { AgentConnectLocale } from './types.js';

const mainstream = [
  { id: 'codex', Icon: CodexIcon },
  { id: 'claude-code', Icon: ClaudeCodeIcon },
  { id: 'doubao', Icon: DoubaoIcon },
  { id: 'workbuddy', Icon: WorkbuddyIcon },
  { id: 'trae', Icon: TraeIcon },
  { id: 'cursor', Icon: CursorIcon },
  { id: 'openclaw', Icon: OpenclawIcon },
  { id: 'hermes', Icon: HermesIcon },
  { id: 'opencode', Icon: OpencodeIcon },
  { id: 'deepseek-harness', Icon: DeepseekIcon },
  { id: 'deerflow', Icon: DeerflowIcon },
  { id: 'pi', Icon: PiIcon },
  { id: 'zcode', Icon: ZcodeIcon },
  { id: 'kimi-code', Icon: KimiCodeIcon },
] as const;

const overseasAgents = new Set([
  'codex',
  'claude-code',
  'cursor',
  'openclaw',
  'hermes',
  'opencode',
  'pi',
]);

const custom = [
  { id: 'mcp', Icon: McpIcon },
  { id: 'cli', Icon: CliIcon },
  { id: 'api', Icon: ApiIcon },
  { id: 'sdk', Icon: SdkIcon },
] as const;

export function getAgentConnectDesign(
  locale: AgentConnectLocale,
  showOverseasTag = false,
) {
  const content = getAgentConnectContent(locale);
  const labels = resolveAgentConnectLabels(locale);
  return {
    tabs: [
      { id: 'mainstream', label: labels.mainstream },
      { id: 'custom', label: labels.custom },
    ] as const,
    mainstream: mainstream.map((item) => ({
      ...item,
      ...content[item.id],
      tags: [
        ...content[item.id].tags,
        ...(showOverseasTag && overseasAgents.has(item.id)
          ? [labels.overseas]
          : []),
      ],
    })),
    custom: custom.map((item) => ({ ...item, ...content[item.id] })),
  };
}

export type AgentConnectEntry =
  | ReturnType<typeof getAgentConnectDesign>['mainstream'][number]
  | ReturnType<typeof getAgentConnectDesign>['custom'][number];
