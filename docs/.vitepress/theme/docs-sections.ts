// Editorial ownership is independent of file location. Existing URLs stay stable.
// This manifest owns grouping and order; article labels come from Markdown H1.
// Top navigation, sidebars, breadcrumbs, the homepage and llms.txt share this structure.
export type Locale = 'en' | 'zh'
export type DocPage = { path: string }
export type DocGroup = { en: string; zh: string; pages: DocPage[] }
export type DocSection = {
  id: string; en: string; zh: string; enNote: string; zhNote: string
  entry: string; aliases: string[]; groups: DocGroup[]
  nextSteps?: DocGroup[]
}
const p = (path: string): DocPage => ({ path })
const g = (en: string, zh: string, pages: DocPage[]): DocGroup => ({ en, zh, pages })

export const sections: DocSection[] = [
  {
    id: 'getting-started', en: 'Quickstart', zh: '开始使用',
    enNote: 'Get a first result, evaluate it on your tasks, and choose what to connect.', zhNote: '完成首次体验，用自己的任务验证效果，再选择接入路径。',
    entry: 'getting-started/01-introduction', aliases: [],
    groups: [
      g('Get your first result', '先完成一次体验', [
        p('getting-started/01-introduction'),
        p('getting-started/02-quickstart'),
        p('getting-started/05-cli-setup')
      ]),
      g('Evaluate the result', '验证实际效果', [
        p('getting-started/06-evaluate')
      ])
    ],
    // Cross-section exits aid onboarding without changing article ownership.
    nextSteps: [
      g('Use it in your work', '接入你的工作', [
        p('agent-integrations/01-overview'),
        p('workflows/01-overview'),
        p('context-compilation/01-overview'),
        p('guides/17-vikingbot')
      ]),
      g('Learn, deploy and troubleshoot', '深入与排障', [
        p('concepts/00-overview'),
        p('guides/00-overview'),
        p('guides/05-observability')
      ])
    ]
  },
  {
    id: 'concepts', en: 'Concepts', zh: '概念与原理',
    enNote: 'Learn the context model, processing flow, architecture and governance.', zhNote: '从上下文模型到处理机制、系统架构与治理。',
    entry: 'concepts/00-overview', aliases: [],
    groups: [
      g('Context model', '基础模型', [
        p('concepts/00-overview'),
        p('concepts/02-context-types'),
        p('concepts/04-viking-uri'),
        p('concepts/03-context-layers')
      ]),
      g('Processing and memory', '处理与记忆', [
        p('concepts/06-extraction'),
        p('concepts/07-retrieval'),
        p('concepts/08-session')
      ]),
      g('Architecture and reliability', '架构与可靠性', [
        p('concepts/01-architecture'),
        p('concepts/05-storage'),
        p('concepts/16-queue-lifecycle'),
        p('concepts/09-transaction'),
        p('concepts/14-multi-write-storage'),
        p('concepts/12-metrics')
      ]),
      g('Identity and governance', '身份与治理', [
        p('concepts/11-multi-tenant'),
        p('concepts/15-acl'),
        p('concepts/10-encryption'),
        p('concepts/13-privacy')
      ]),
      g('Application example', '应用示例', [
        p('concepts/15-vikingbot')
      ])
    ]
  },
  {
    id: 'agent-integrations', en: 'Agents', zh: '接入 Agent',
    enNote: 'Choose an integration, connect it, and verify memory behavior.', zhNote: '选择集成，完成接入，验证记忆行为。',
    entry: 'agent-integrations/01-overview', aliases: [],
    groups: [
      g('Choose an integration', '选择接入方式', [
        p('agent-integrations/01-overview'),
        p('agent-integrations/16-capability-reference'),
        p('agent-integrations/14-openviking-helper')
      ]),
      g('Coding tools', '编程工具', [
        p('agent-integrations/02-claude-code'),
        p('agent-integrations/04-codex'),
        p('agent-integrations/12-cursor'),
        p('agent-integrations/20-grok-build'),
        p('agent-integrations/13-trae'),
        p('agent-integrations/10-opencode'),
        p('agent-integrations/17-dsh')
      ]),
      g('Agents and frameworks', 'Agent 与框架', [
        p('agent-integrations/03-openclaw'),
        p('agent-integrations/05-hermes'),
        p('agent-integrations/11-pi'),
        p('agent-integrations/07-langchain-langgraph'),
        p('agent-integrations/08-community-plugins')
      ]),
      g('Shared setup and tuning', '通用接入与调优', [
        p('agent-integrations/06-mcp-clients'),
        p('agent-integrations/15-agent-plugins'),
        p('agent-integrations/09-log-ingestion'),
        p('agent-integrations/19-recall-tuning'),
        p('agent-integrations/18-plugin-development'),
        p('guides/working-memory-default-off')
      ]),
      g('OpenViking Gateway', 'OpenViking 网关', [
        p('guides/15-gateway'),
        p('guides/22-gateway-operations')
      ]),
      g('VikingBot', 'VikingBot', [
        p('guides/17-vikingbot'),
        p('guides/12-vikingbot-metrics-validation')
      ])
    ]
  },
  {
    id: 'build', en: 'Develop', zh: '开发应用',
    enNote: 'Turn resources and sessions into reusable context and outputs.', zhNote: '把资源和会话用于检索、记忆与内容产出。',
    entry: 'workflows/01-overview', aliases: ['context-compilation'],
    groups: [
      g('Choose a workflow', '选择开发任务', [
        p('workflows/01-overview')
      ]),
      g('Manage context', '组织与维护上下文', [
        p('guides/18-openviking-assets'),
        p('guides/09-ovpack'),
        p('guides/15-snapshot'),
        p('guides/10-prompt-guide')
      ]),
      g('Compile context into outputs', '编译上下文', [
        p('context-compilation/01-overview'),
        p('context-compilation/02-llm-wiki'),
        p('context-compilation/03-knowledge-graph'),
        p('context-compilation/04-daily-report'),
        p('context-compilation/05-knowledge-distillation'),
        p('context-compilation/06-memory-consolidation')
      ])
    ]
  },
  {
    id: 'operate', en: 'Deploy', zh: '部署运维',
    enNote: 'Deploy a service, control access, monitor it, and upgrade it.', zhNote: '部署服务，配置访问控制，监控与升级。',
    entry: 'guides/00-overview', aliases: ['guides', 'faq'],
    groups: [
      g('Deploy a service', '部署服务', [
        p('guides/00-overview'),
        p('guides/03-deployment'),
        p('getting-started/04-setup-for-agent'),
        p('guides/01-configuration'),
        p('guides/02-volcengine-purchase-guide')
      ]),
      g('Enterprise deployment', '企业私有化部署', [
        p('guides/19-deployment-checklist'),
        p('guides/20-private-deployment'),
        p('guides/21-private-operations')
      ]),
      g('Secure access', '访问与安全', [
        p('guides/04-authentication'),
        p('guides/12-public-access'),
        p('guides/11-oauth'),
        p('guides/08-encryption')
      ]),
      g('Observe and troubleshoot', '观测与排障', [
        p('guides/05-observability'),
        p('guides/07-operation-telemetry'),
        p('guides/11-grafana-prometheus'),
        p('faq/faq')
      ]),
      g('Storage and performance', '存储与性能', [
        p('guides/13-multi-write-storage'),
        p('guides/14-ragfs-cache'),
        p('guides/16-cuvs')
      ])
    ]
  },
  {
    id: 'reference', en: 'Reference', zh: '参考',
    enNote: 'Look up API contracts, protocol details and configuration fields.', zhNote: '查接口约定、协议和配置字段。',
    entry: 'reference/01-overview', aliases: ['api', 'configuration'],
    groups: [
      g('Reference and configuration', '参考入口与配置', [
        p('reference/01-overview'),
        p('api/01-overview'),
        p('configuration/01-server'),
        p('configuration/02-client'),
        p('guides/06-mcp-integration')
      ]),
      g('Resources and retrieval APIs', '资源与检索 API', [
        p('api/02-resources'),
        p('api/12-content'),
        p('api/03-filesystem'),
        p('api/06-retrieval'),
        p('api/15-watches')
      ]),
      g('Sessions, memory and skills APIs', '会话、记忆与技能 API', [
        p('api/05-sessions'),
        p('api/16-memory'),
        p('api/04-skills'),
        p('api/19-agent-evolution')
      ]),
      g('Data and extension APIs', '数据与扩展 API', [
        p('api/11-snapshot'),
        p('api/14-ovpack'),
        p('api/22-openviking-assets'),
        p('api/20-webdav'),
        p('api/23-agent-runtime'),
        p('api/24-vikingbot')
      ]),
      g('Administration and operations APIs', '管理与运维 API', [
        p('api/08-admin'),
        p('api/25-gateway'),
        p('api/12-acl'),
        p('api/10-privacy'),
        p('api/07-system'),
        p('api/17-tasks'),
        p('api/18-observer'),
        p('api/09-metrics')
      ])

    ]
  },
  {
    id: 'project', en: 'Community', zh: '社区',
    enNote: 'Follow releases and contribute to the project.', zhNote: '了解版本变化，参与项目与文档维护。',
    entry: 'about/01-about-us', aliases: ['about'],
    groups: [
      g('Community and releases', '社区与版本', [
        p('about/01-about-us'),
        p('about/02-changelog'),
        p('about/03-roadmap')
      ]),
      g('Contribute documentation', '参与文档维护', [
        p('api/99-api-doc-writing-guide')
      ])
    ]
  }
]

// This retired page is kept for inbound links, but is not a second tutorial.
export const legacyPages: Record<string, string> = {
  'getting-started/03-quickstart-server': 'getting-started'
}

export function sectionForPage(relativePath: string): DocSection | undefined {
  const path = relativePath.replace(/^(en|zh)\//, '').replace(/\.md$/, '')
  return sections.find(section => section.groups.some(group => group.pages.some(page => page.path === path)))
    || sections.find(section => section.id === legacyPages[path])
}
