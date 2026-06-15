import { registerPost } from '../blog-components';

import agentRuntime from './agent-runtime/index.jsx';
import oauthMcp from './oauth-mcp/index.jsx';
import openvikingUserPeerModel from './openviking-user-peer-model/index.jsx';
import openvikingCodingAgent from './openviking-coding-agent/index.jsx';
import openvikingBenchmarkResults from './openviking-benchmark-results/index.jsx';
import openvikingTooManyAgents from './openviking-too-many-agents/index.jsx';
import openvikingContextDatabaseArchitecture from './openviking-context-database-architecture/index.jsx';
import openvikingContextDatabase from './openviking-context-database/index.jsx';
import vikingbotMemoryGame from './vikingbot-memory-game/index.jsx';

[openvikingUserPeerModel, openvikingBenchmarkResults, openvikingTooManyAgents, vikingbotMemoryGame, openvikingCodingAgent, agentRuntime, oauthMcp, openvikingContextDatabaseArchitecture, openvikingContextDatabase].forEach(registerPost);
