import { registerPost } from '../blog-components';

import kitchenSink from './kitchen-sink/index.jsx';
import agentRuntime from './agent-runtime/index.jsx';
import oauthMcp from './oauth-mcp/index.jsx';

[agentRuntime, oauthMcp, kitchenSink].forEach(registerPost);
