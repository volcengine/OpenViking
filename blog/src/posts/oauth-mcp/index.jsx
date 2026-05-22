import React from 'react';
import {
  Article, Lead, P, H2, H3, Pre, Quote, Pull, Callout, Hr,
  Cols, Col, Ol, Li, Ul, Table, A, InlineCode, Strong, Tag, Mark,
} from '../../blog-components';

function TokenBadge({ prefix, ttl, color }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      padding: '3px 10px', borderRadius: 99,
      border: `1px solid ${color}`, color,
      fontFamily: 'var(--th-font-mono)', fontSize: 11,
      lineHeight: 1,
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%',
        background: color,
      }} />
      {prefix} · {ttl}
    </span>
  );
}

function FlowStep({ n, label, detail }) {
  return (
    <div style={{
      display: 'flex', gap: 16, alignItems: 'flex-start',
      padding: '16px 0',
      borderBottom: '1px solid var(--th-line)',
    }}>
      <div style={{
        width: 32, height: 32, borderRadius: '50%',
        background: 'var(--th-accent)', color: 'var(--th-bg)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: 'var(--th-font-mono)', fontWeight: 700, fontSize: 14,
        flexShrink: 0,
      }}>{n}</div>
      <div>
        <div style={{
          fontFamily: 'var(--th-font-display)', fontWeight: 600,
          fontSize: 16, marginBottom: 4,
        }}>{label}</div>
        <div style={{ color: 'var(--th-mute)', fontSize: 14 }}>{detail}</div>
      </div>
    </div>
  );
}

function DecisionRow({ decision, conclusion, reason }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1fr 1fr 2fr',
      gap: 12, padding: '12px 0',
      borderBottom: '1px solid var(--th-line)',
      fontSize: 14,
    }}>
      <div style={{ fontWeight: 600 }}>{decision}</div>
      <div style={{ fontFamily: 'var(--th-font-mono)', fontSize: 12 }}>{conclusion}</div>
      <div style={{ color: 'var(--th-mute)' }}>{reason}</div>
    </div>
  );
}

const OAuthMcp = ({ t }) => {
  const T = t;

  return (
    <Article>
      <Lead>{T({
        en: 'Claude.ai, ChatGPT, and Cursor — they all speak OAuth, not API keys. We built native OAuth 2.1 into OpenViking so these clients can connect directly, without a proxy.',
        zh: 'Claude.ai、ChatGPT 和 Cursor — 它们都采用 OAuth 标准，而非传统 API Key。因此，我们在 OpenViking 中原生实现了 OAuth 2.1，让这些客户端无需代理即可直连。',
      })}</Lead>

      <P dropCap>{T({
        en: 'Before this change, connecting an MCP client to OpenViking required a Cloudflare Worker proxy. You had to deploy a Worker, configure two KV namespaces, and bridge OAuth tokens back to API keys. It worked, but it meant trusting a third party with your credentials, paying for another service, and debugging across two systems when things break.',
        zh: '在此之前，将 MCP 客户端连接到 OpenViking 需要搭建 Cloudflare Worker 代理。你需要部署 Worker，配置两个 KV 命名空间，并将 OAuth token 桥接回 API Key。这套方案虽然可用，但也意味着你需要将凭证托付给第三方、为额外的服务买单，并在出问题时于两个系统间来回排查。',
      })}</P>

      <H2>{T({ en: 'What the user sees', zh: '用户视角的体验' })}</H2>

      <P>{T({
        en: 'The entire flow, from the user\'s perspective:',
        zh: '从用户侧来看，完整的交互流程如下：',
      })}</P>

      <FlowStep n="1"
        label={T({ en: 'Client requests MCP', zh: '客户端请求 MCP' })}
        detail={T({ en: 'POST /mcp → 401 + WWW-Authenticate header. The client auto-discovers OAuth endpoints.', zh: 'POST /mcp → 401 + WWW-Authenticate 头。客户端借此自动发现 OAuth 授权端点。' })}
      />
      <FlowStep n="2"
        label={T({ en: 'Browser opens', zh: '浏览器弹出' })}
        detail={T({ en: 'The authorization page displays a 6-character code. Human-readable, free of ambiguous characters.', zh: '授权页面弹出一个 6 位验证码。具备人类可读性，且剔除了易混淆字符。' })}
      />
      <FlowStep n="3"
        label={T({ en: 'User confirms', zh: '用户确认' })}
        detail={T({ en: 'If already logged into the Console in the same browser, it\'s a single click. Otherwise, the user enters the code manually.', zh: '若当前浏览器已登录 Console，一键即可授权。否则，只需在 Console 中手动输入该验证码。' })}
      />
      <FlowStep n="4"
        label={T({ en: 'Done', zh: '完成' })}
        detail={T({ en: 'The client receives an access token and a refresh token. All subsequent MCP calls are authenticated via Bearer token.', zh: '客户端获取 access token 与 refresh token。后续所有 MCP 调用均通过 Bearer token 认证。' })}
      />

      <P>{T({
        en: 'No copying and pasting API keys. No proxies to deploy. No extra infrastructure to maintain.',
        zh: '全程无需复制粘贴 API Key，无需部署代理，更没有额外的基础设施需要维护。',
      })}</P>

      <Hr ornament />

      <H2>{T({ en: 'Design decisions', zh: '设计决策' })}</H2>

      <P>{T({
        en: 'Six key decisions shaped this implementation. Each represents a deliberate tradeoff:',
        zh: '整个实现基于以下六个关键决策。每一次选择都经过了精心权衡：',
      })}</P>

      <Table
        headers={[
          T({ en: 'Decision', zh: '决策' }),
          T({ en: 'Choice', zh: '选择' }),
          T({ en: 'Why', zh: '原因' }),
        ]}
        rows={[
          [
            T({ en: 'Protocol', zh: '协议实现' }),
            <InlineCode>mcp.server.auth SDK</InlineCode>,
            T({ en: 'Zero cryptographic code on our end. DCR, PKCE, and redirect validation are all handled upstream.', zh: '服务端零密码学代码处理。DCR、PKCE 和 redirect 校验全交由上游处理。' }),
          ],
          [
            T({ en: 'Token format', zh: 'Token 形态' }),
            T({ en: 'Opaque + SQLite', zh: '不透明随机串 + SQLite' }),
            T({ en: 'No signing keys to manage. Revocation is a simple UPDATE. SHA-256 hash indexing ensures microsecond lookups.', zh: '无需管理签名密钥。撤销只需一条 UPDATE 语句。SHA-256 哈希索引保障微秒级查询。' }),
          ],
          [
            T({ en: 'Auth flow', zh: '授权方式' }),
            T({ en: 'Device-flow style', zh: 'Device-flow 风格' }),
            T({ en: '"I approve that request from a place I trust." Users confirm in an already-authenticated browser session.', zh: '「我在安全的环境下批准该请求。」用户在已认证登录的浏览器中完成确认。' }),
          ],
          [
            T({ en: 'Quick auth', zh: '一键授权' }),
            T({ en: 'localStorage + explicit click', zh: 'localStorage + 显式点击' }),
            T({ en: 'Convenient, yet intentional. Users always know exactly what they are approving.', zh: '兼顾便捷与透明。确保用户始终清楚自己正在授权什么。' }),
          ],
          [
            T({ en: 'Redirect whitelist', zh: '重定向白名单' }),
            T({ en: 'None (DCR accepts any URI)', zh: '不设（DCR 接受任意 URI）' }),
            T({ en: 'Aligns with MCP ecosystem conventions. The SDK handles strict-equality validation internally.', zh: '遵循 MCP 生态惯例。SDK 内部会进行严格的全等校验。' }),
          ],
          [
            T({ en: 'Revocation scope', zh: '撤销粒度' }),
            T({ en: 'Per (account, user)', zh: '按 (account, user) 维度' }),
            T({ en: 'Rotating an API key instantly revokes all associated OAuth states for that user. A clean break.', zh: '轮换 API Key 会联动撤销该用户所有的 OAuth 状态。干净利落。' }),
          ],
        ]}
      />

      <H2>{T({ en: 'Tokens', zh: 'Token 体系' })}</H2>

      <P>{T({
        en: 'We introduced four token types, each using a distinct prefix for fail-closed routing:',
        zh: '我们引入了四种 Token 类型，每种自带独立前缀，用于实现 fail-closed（默认拒绝）的安全路由：',
      })}</P>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, margin: '16px 0 24px' }}>
        <TokenBadge prefix="ovat_" ttl="1h" color="var(--th-accent)" />
        <TokenBadge prefix="ovrt_" ttl="30d" color="var(--th-accent-2)" />
        <TokenBadge prefix="ovac_" ttl="5min" color="var(--th-mute)" />
        <TokenBadge prefix="6-char" ttl="10min" color="var(--th-accent)" />
      </div>

      <Callout type="note">
        <P>{T({
          en: 'Refresh tokens are single-use and rotate upon every exchange. If a refresh token is replayed, the entire token chain is immediately revoked. This catches stolen tokens fast.',
          zh: 'Refresh token 为一次性使用，并在每次交换时轮转。一旦检测到 refresh token 被重放，整条授权链将被立即撤销。这套机制能极速熔断被盗 Token。',
        })}</P>
      </Callout>

      <Pull>{T({
        en: 'An OAuth token is strictly equivalent to the API key that authorized it — same account, same user, same role. This is privilege delegation, not privilege escalation.',
        zh: 'OAuth token 的权限严格等效于签发它的 API Key — 同 Account、同 User、同 Role。这是权限委派，而非提权。',
      })}</Pull>

      <H2>{T({ en: 'Deployment', zh: '部署架构' })}</H2>

      <P>{T({
        en: 'This update introduces a Caddy reverse proxy to unify two ports under a single origin:',
        zh: '本次更新引入了 Caddy 反向代理，将两个端口统一至单一入口：',
      })}</P>

      <Pre lang="js" filename="Caddyfile">{`:1934 {
  handle /console/* {
    reverse_proxy :8020
  }
  handle {
    reverse_proxy :1933
  }
}`}</Pre>

      <Cols count={2}>
        <Col>
          <H3>{T({ en: 'Why merge?', zh: '为何合并？' })}</H3>
          <P>{T({
            en: 'Same-origin guarantee. The Console and OAuth pages now share a single origin, enabling Quick Authorize to work right out of the box.',
            zh: '同源保证。Console 与 OAuth 页面共享同一个 Origin，从而让「一键授权」功能开箱即用。',
          })}</P>
        </Col>
        <Col>
          <H3>{T({ en: 'HTTPS?', zh: 'HTTPS？' })}</H3>
          <P>{T({
            en: 'OAuth 2.1 mandates HTTPS for non-localhost issuers. Simply add a domain block to your Caddyfile and uncomment three lines in docker-compose. Done.',
            zh: 'OAuth 2.1 强制要求非 localhost 的签发端点必须使用 HTTPS。只需在 Caddyfile 中添加域名配置，并取消 docker-compose 中相关的 3 行注释即可。十分简单。',
          })}</P>
        </Col>
      </Cols>

      <Hr ornament />

      <H2>{T({ en: 'Backwards compatibility', zh: '向后兼容性' })}</H2>

      <Ul>
        <Li><Strong>{T({ en: 'Off by default', zh: '默认关闭' })}</Strong> — <InlineCode>oauth.enabled: false</InlineCode> {T({ en: 'ensures zero side effects. No OAuth routes are mounted, and no Bearer token inspection occurs.', zh: '意味着零副作用。不挂载 OAuth 路由，不拦截 Bearer token。' })}</Li>
        <Li><Strong>{T({ en: 'Bearer routing', zh: 'Bearer 分流' })}</Strong> — {T({ en: 'Only tokens with the', zh: '只有带' })} <InlineCode>ovat_</InlineCode> {T({ en: 'prefix trigger an OAuth lookup. Standard bearer tokens continue down the legacy API key path.', zh: '前缀的 token 才会触发 OAuth 查询。普通的 bearer token 继续走原有的 API Key 认证链路。' })}</Li>
        <Li><Strong>{T({ en: 'Fail-closed', zh: 'Fail-closed（默认拒绝）' })}</Strong> — {T({ en: 'If a token carries the OAuth prefix but is invalid, it yields a 401. No fallback to the API key path. Zero ambiguity.', zh: '如果 token 带有 OAuth 前缀但在库中未命中，将直接返回 401。绝不回退至 API Key 链路，杜绝安全歧义。' })}</Li>
      </Ul>

      <Pre lang="js" filename="ov.conf">{`{
  "oauth": {
    "enabled": true
  }
}`}</Pre>

      <H2>{T({ en: 'Verified clients', zh: '已验证客户端' })}</H2>

      <Table
        headers={[
          T({ en: 'Client', zh: '客户端' }),
          T({ en: 'Status', zh: '状态' }),
        ]}
        rows={[
          ['ChatGPT', <Tag tone="tip">{T({ en: 'Working', zh: '已通过' })}</Tag>],
          ['Claude Desktop', <Tag tone="tip">{T({ en: 'Working', zh: '已通过' })}</Tag>],
          ['MCP Inspector', <Tag tone="tip">{T({ en: 'Working', zh: '已通过' })}</Tag>],
          ['Cursor', <Tag>{T({ en: 'Pending', zh: '待验证' })}</Tag>],
        ]}
      />

      <P>{T({
        en: '37 new integration tests cover the entire lifecycle: storage atomicity, device-flow happy paths, fail-closed bearer routing, refresh rotation, replay detection, and WWW-Authenticate header injection. Zero regressions in existing authentication tests.',
        zh: '我们新增了 37 个测试用例，覆盖完整生命周期：存储层原子性操作、Device-flow 正常链路、Bearer fail-closed 路由、Refresh 轮转、重放检测以及 WWW-Authenticate 标头注入。现有认证测试保持零回归（Zero Regressions）。',
      })}</P>
    </Article>
  );
};

export default {
  id: 'oauth-mcp',
  Component: OAuthMcp,
  meta: {
    title: { en: 'Native OAuth 2.1 for MCP Clients', zh: 'MCP 客户端的原生 OAuth 2.1 支持' },
    description: {
      en: 'How we eliminated the proxy to let ChatGPT, Claude, and Cursor connect to OpenViking directly.',
      zh: '我们如何砍掉代理层，让 ChatGPT、Claude 和 Cursor 直连 OpenViking。',
    },
    cover: '/assets/covers/oauth.png',
    publishedAt: '2026-05-09',
    readingTime: 8,
    category: { en: 'Engineering', zh: '工程' },
    tags: ['oauth', 'mcp', 'system'],
    languages: ['en', 'zh'],
    authors: [{ name: 'tosaki', github: 't0saki' }],
  },
};