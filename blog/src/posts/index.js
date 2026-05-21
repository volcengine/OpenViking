import { registerPost } from '../blog-components';

import agentRuntime from './agent-runtime/index.jsx';
import oauthMcp from './oauth-mcp/index.jsx';
import openvikingCodingAgent from './openviking-coding-agent/index.jsx';
import openvikingContextDatabaseArchitecture from './openviking-context-database-architecture/index.jsx';
import openvikingContextDatabase from './openviking-context-database/index.jsx';

[agentRuntime, oauthMcp, openvikingCodingAgent, openvikingContextDatabaseArchitecture, openvikingContextDatabase].forEach(registerPost);
