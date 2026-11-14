import { createContext, useContext, useMemo } from 'react';
import type { ReactNode } from 'react';
import { LocaleProvider } from '../shared/locale-context.js';
import type { Locale } from '../shared/locale.js';
import { AnalyticsProvider } from '../shared/analytics/context.js';
import type { OpenVikingAnalyticsOptions } from './analytics/types.js';

import { createOvClient } from './client.js';
import { mergeProviderConfig, type OvProviderBase } from './options.js';
import type { OvClient, OvClientOptions, OvRequestOptions } from './types.js';

/** Provider 已解析的配置，供嵌套 Provider 继续继承。 */
interface OvClientScope {
  client: OvClient;
  base: OvProviderBase;
}

const OvClientContext = createContext<OvClientScope | null>(null);

/**
 * {@link OpenVikingProvider} 的属性。请求字段与 {@link OvClientOptions} 同形，另可设置界面语言：
 * 服务地址、身份与作用域、Cookie 策略，都按字段传值。
 * Props of {@link OpenVikingProvider}: request fields follow {@link OvClientOptions},
 * with an optional interface locale.
 * The service address, identity and scope, and the cookie policy are all passed
 * as fields.
 */
export interface OpenVikingProviderProps extends OvClientOptions {
  /** Optional product analytics receiver. Omitted at the root disables collection. */
  analytics?: OpenVikingAnalyticsOptions | false;
  /** 界面语言；省略时继承上层，无配置时默认英文。Subtree locale; inherits or defaults to en-US. */
  locale?: Locale;
  children?: ReactNode;
}

/**
 * 向子树下发请求配置与默认语言。模块通过内部的 `useOvClient` 取用，宿主只需在应用根部
 * 配置一次服务地址与身份字段，不必自己组装请求头。
 *
 * 开源版传 `apiKey`、`accountId`、`userId`；商业版由浏览器 Cookie 鉴权，只传
 * `accountId`、`userId`、`resourceId`。`credentials` 默认 `include`，无需显式传入。
 *
 * 嵌套时内层只覆盖自己声明的字段，其余继承外层：外层配好服务地址与凭证后，
 * 内层可以只改 `userId`。显式传空字符串表示清除继承到的值。
 *
 * 不传 `baseURL` 时按浏览器同源请求。挂载本身不发请求，也不读取浏览器里保存
 * 的凭证。
 *
 * Provides request configuration and a default language to a subtree. Modules read it through the
 * internal `useOvClient`, so a host configures the service address and identity
 * fields once at the application root and never assembles headers itself.
 *
 * The open-source edition passes `apiKey`, `accountId` and `userId`; the
 * commercial edition authenticates with a browser cookie, so it passes
 * `accountId`, `userId` and `resourceId`. `credentials` defaults to `include`
 * and can be omitted.
 *
 * Nesting overrides only the fields the inner Provider declares and inherits the
 * rest, so an outer Provider can own the service address and credential while an
 * inner one changes only `userId`. An explicit empty string clears an inherited
 * value.
 *
 * Without `baseURL` it targets the browser's own origin. Mounting it sends no
 * request and reads no credential stored in the browser.
 */
export function OpenVikingProvider({
  baseURL,
  apiKey,
  accountId,
  userId,
  resourceId,
  credentials,
  signal,
  fetch,
  analytics,
  locale,
  children,
}: OpenVikingProviderProps) {
  const parent = useContext(OvClientContext);

  const value = useMemo<OvClientScope>(() => {
    const base = mergeProviderConfig(parent?.base, {
      baseURL,
      apiKey,
      accountId,
      userId,
      resourceId,
      credentials,
      signal,
      fetch,
    });

    return {
      base,
      client: createOvClient({
        ...base.identity,
        baseURL: base.baseURL,
        credentials: base.credentials,
        signal: base.signal,
        // 内层没声明 fetch 时用继承来的那一份，否则内层子树会退回全局 fetch。
        fetch: base.fetch,
      }),
    };
  }, [
    parent,
    baseURL,
    apiKey,
    accountId,
    userId,
    resourceId,
    credentials,
    signal,
    fetch,
  ]);

  return (
    <OvClientContext.Provider value={value}>
      <AnalyticsProvider analytics={analytics}>
        <LocaleProvider locale={locale}>{children}</LocaleProvider>
      </AnalyticsProvider>
    </OvClientContext.Provider>
  );
}

/**
 * 读取最近的 Provider 提供的客户端。仅供模块内部使用，不从包入口导出：
 * 调用方不需要知道该发什么请求，也就不会直接用它。
 *
 * Reads the client from the nearest Provider. Internal to modules and not
 * exported from the package: a caller does not need to know which requests to
 * send, so it has no reason to use this directly.
 */
export function useOvClient(): OvClient | null {
  return useContext(OvClientContext)?.client ?? null;
}

/** Internal: modules compose their request lifetime with the inherited signal. */
export function useOvSignal(): AbortSignal | undefined {
  return useContext(OvClientContext)?.base.signal;
}

/** Internal identity inheritance for account-scoped principal directories. */
export function useOvIdentity(options?: OvRequestOptions) {
  const base = useContext(OvClientContext)?.base.identity;
  return {
    accountId: options?.accountId ?? base?.accountId,
    userId: options?.userId ?? base?.userId,
  };
}
