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
        en: 'Claude.ai, ChatGPT, Cursor — they all speak OAuth, not API keys. We built native OAuth 2.1 into OpenViking so these clients can connect without a proxy.',
        zh: 'Claude.ai、ChatGPT、Cursor — 它们都说 OAuth，不认 API Key。我们在 OpenViking 里原生实现了 OAuth 2.1，让这些客户端不需要代理就能直连。',
      })}</Lead>

      <P dropCap>{T({
        en: 'Before this change, connecting an MCP client to OpenViking required a Cloudflare Worker proxy. You deploy a Worker, configure two KV namespaces, and bridge OAuth tokens to API keys. It works. It also means you are trusting a third party with your credentials, paying for another service, and debugging across two systems when something breaks.',
        zh: '在这个改动之前，把 MCP 客户端连到 OpenViking 需要一个 Cloudflare Worker 代理。部署一个 Worker，配两个 KV namespace，把 OAuth token 桥接到 API Key。能用。但这意味着你在把凭证交给第三方、为另一个服务付费、出问题时在两个系统之间来回调试。',
      })}</P>

      <H2>{T({ en: 'What the user sees', zh: '用户看到的' })}</H2>

      <P>{T({
        en: 'The entire flow, from the user side:',
        zh: '从用户侧看，整个流程：',
      })}</P>

      <FlowStep n="1"
        label={T({ en: 'Client requests MCP', zh: '客户端请求 MCP' })}
        detail={T({ en: 'POST /mcp → 401 + WWW-Authenticate header. Client auto-discovers OAuth endpoints.', zh: 'POST /mcp → 401 + WWW-Authenticate 头。客户端自动发现 OAuth 端点。' })}
      />
      <FlowStep n="2"
        label={T({ en: 'Browser opens', zh: '浏览器弹出' })}
        detail={T({ en: 'Authorization page shows a 6-character code. Human-readable, no ambiguous characters.', zh: '授权页面显示一个 6 位码。人类可读，没有容易混淆的字符。' })}
      />
      <FlowStep n="3"
        label={T({ en: 'User confirms', zh: '用户确认' })}
        detail={T({ en: 'If already logged into Console in the same browser, one click. Otherwise, enter the code in Console.', zh: '如果在同一浏览器已登录 Console，一键确认。否则在 Console 中输入验证码。' })}
      />
      <FlowStep n="4"
        label={T({ en: 'Done', zh: '完成' })}
        detail={T({ en: 'Client gets an access token + refresh token. All subsequent MCP calls use Bearer auth.', zh: '客户端拿到 access token + refresh token。后续所有 MCP 调用使用 Bearer 认证。' })}
      />

      <P>{T({
        en: 'No API key pasted anywhere. No proxy deployed. No extra services to maintain.',
        zh: '没有在任何地方粘贴 API Key。没有部署代理。没有额外的服务要维护。',
      })}</P>

      <Hr ornament />

      <H2>{T({ en: 'Design decisions', zh: '设计决策' })}</H2>

      <P>{T({
        en: 'Six choices shaped the implementation. Each one is a tradeoff:',
        zh: '六个选择塑造了实现。每个都是取舍：',
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
            T({ en: 'Zero crypto code on our side. DCR, PKCE, redirect validation all handled upstream.', zh: '我们这边零密码学代码。DCR、PKCE、redirect 校验全由上游处理。' }),
          ],
          [
            T({ en: 'Token format', zh: 'Token 形态' }),
            T({ en: 'Opaque + SQLite', zh: '不透明随机串 + SQLite' }),
            T({ en: 'No signing keys to manage. Revocation is one UPDATE. SHA-256 hash index, microsecond lookups.', zh: '不需要管理签名密钥。撤销就是一条 UPDATE。SHA-256 哈希索引，微秒级查询。' }),
          ],
          [
            T({ en: 'Auth flow', zh: '授权方式' }),
            T({ en: 'Device-flow style', zh: 'Device-flow 风格' }),
            T({ en: '"I approve that request from a place I trust." Users confirm in an already-authenticated browser.', zh: '「我在安全的地方批准那边的请求。」用户在已认证的浏览器中确认。' }),
          ],
          [
            T({ en: 'Quick auth', zh: '一键授权' }),
            T({ en: 'localStorage + explicit click', zh: 'localStorage + 显式点击' }),
            T({ en: 'Convenient but not automatic. User always knows what they are approving.', zh: '方便但不自动。用户始终知道自己在批准什么。' }),
          ],
          [
            T({ en: 'Redirect whitelist', zh: 'redirect_uri 白名单' }),
            T({ en: 'None (DCR accepts any URI)', zh: '不设（DCR 接受任意 URI）' }),
            T({ en: 'Matches MCP ecosystem behavior. SDK does strict-equal validation internally.', zh: '符合 MCP 生态惯例。SDK 内部做严格相等校验。' }),
          ],
          [
            T({ en: 'Revocation scope', zh: '撤销粒度' }),
            T({ en: 'Per (account, user)', zh: '按 (account, user)' }),
            T({ en: 'API key rotation revokes all OAuth state for that user. Clean break.', zh: 'API Key 轮换时撤销该用户的所有 OAuth 状态。干净利落。' }),
          ],
        ]}
      />

      <H2>{T({ en: 'Tokens', zh: 'Token 体系' })}</H2>

      <P>{T({
        en: 'Four token types, each with a prefix for fail-closed routing:',
        zh: '四种 token 类型，每种都有前缀用于 fail-closed 路由：',
      })}</P>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, margin: '16px 0 24px' }}>
        <TokenBadge prefix="ovat_" ttl="1h" color="var(--th-accent)" />
        <TokenBadge prefix="ovrt_" ttl="30d" color="var(--th-accent-2)" />
        <TokenBadge prefix="ovac_" ttl="5min" color="var(--th-mute)" />
        <TokenBadge prefix="6-char" ttl="10min" color="var(--th-accent)" />
      </div>

      <Callout type="note">
        <P>{T({
          en: 'Refresh tokens are single-use and rotate on every exchange. If a refresh token is replayed, the entire token chain gets revoked. This catches stolen tokens fast.',
          zh: 'Refresh token 一次性消费、每次交换时轮转。如果一个 refresh token 被重放，整条 token 链被撤销。这能快速发现被盗的 token。',
        })}</P>
      </Callout>

      <Pull>{T({
        en: 'An OAuth token is equivalent to the API key that signed it — same account, same user, same role. This is not privilege escalation.',
        zh: 'OAuth token 等效于签发它的 API Key — 同 account、同 user、同 role。这不是提权。',
      })}</Pull>

      <H2>{T({ en: 'Deployment', zh: '部署架构' })}</H2>

      <P>{T({
        en: 'The PR adds a Caddy reverse proxy that merges two ports into one:',
        zh: '这个 PR 加了一个 Caddy 反向代理，把两个端口合到一个：',
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
          <H3>{T({ en: 'Why merge?', zh: '为什么合并？' })}</H3>
          <P>{T({
            en: 'Same-origin guarantee. Console and OAuth pages share one origin, so Quick Authorize works out of the box.',
            zh: '同源保证。Console 和 OAuth 页面共享一个 origin，一键授权直接可用。',
          })}</P>
        </Col>
        <Col>
          <H3>{T({ en: 'HTTPS?', zh: 'HTTPS？' })}</H3>
          <P>{T({
            en: 'OAuth 2.1 requires HTTPS for non-localhost issuers. Add a domain block to Caddyfile and uncomment 3 lines in docker-compose. Done.',
            zh: 'OAuth 2.1 要求非 localhost issuer 必须 HTTPS。在 Caddyfile 加个域名块，取消 docker-compose 里 3 行注释。完事。',
          })}</P>
        </Col>
      </Cols>

      <Hr ornament />

      <H2>{T({ en: 'Backwards compatibility', zh: '向后兼容' })}</H2>

      <Ul>
        <Li><Strong>{T({ en: 'Off by default', zh: '默认关闭' })}</Strong> — <InlineCode>oauth.enabled: false</InlineCode> {T({ en: 'means zero side effects. No routes mounted, no bearer inspection.', zh: '意味着零副作用。不挂载路由，不检查 bearer。' })}</Li>
        <Li><Strong>{T({ en: 'Bearer routing', zh: 'Bearer 分流' })}</Strong> — {T({ en: 'Only tokens with', zh: '只有带' })} <InlineCode>ovat_</InlineCode> {T({ en: 'prefix go through OAuth lookup. Plain bearer tokens still hit the API key path.', zh: '前缀的 token 走 OAuth 查询。普通 bearer token 仍走 API Key 路径。' })}</Li>
        <Li><Strong>{T({ en: 'Fail-closed', zh: 'Fail-closed' })}</Strong> — {T({ en: 'If a token has the OAuth prefix but is not found, it is 401. No fallback to API key. No ambiguity.', zh: '如果一个 token 有 OAuth 前缀但查不到，就是 401。不回退到 API Key。没有歧义。' })}</Li>
      </Ul>

      <Pre lang="js" filename="ov.conf">{`{
  "oauth": {
    "enabled": true
  }
}`}</Pre>

      <H2>{T({ en: 'Verified clients', zh: '已验证的客户端' })}</H2>

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
        en: '37 new tests cover the full flow: storage atomics, device-flow happy path, bearer fail-closed, refresh rotation, replay detection, and WWW-Authenticate header injection. Zero regressions in existing auth tests.',
        zh: '37 个新测试覆盖了完整流程：存储层原子操作、device-flow 正常路径、bearer fail-closed、refresh 轮转、重放检测、WWW-Authenticate 头注入。现有认证测试零回归。',
      })}</P>
    </Article>
  );
};

export default {
  id: 'oauth-mcp',
  Component: OAuthMcp,
  meta: {
    title: { en: 'Native OAuth 2.1 for MCP Clients', zh: 'MCP 客户端的原生 OAuth 2.1' },
    description: {
      en: 'How we removed the proxy and let ChatGPT, Claude, and Cursor connect to OpenViking directly.',
      zh: '我们如何去掉代理，让 ChatGPT、Claude 和 Cursor 直连 OpenViking。',
    },
    cover: 'assets/covers/oauth.png',
    publishedAt: '2026-05-09',
    readingTime: 8,
    category: { en: 'Engineering', zh: '工程' },
    tags: ['oauth', 'mcp', 'system'],
    languages: ['en', 'zh'],
    authors: [{ name: 'Zayn', github: 'ZaynJarvis', role: { en: 'Engineer', zh: '工程师' } }],
  },
};
