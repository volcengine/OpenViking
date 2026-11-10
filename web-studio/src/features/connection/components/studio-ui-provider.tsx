import { OpenVikingProvider } from '@openviking/react/client'
import type { ReactNode } from 'react'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useAppConnection } from '../hooks/use-app-connection'
import { getOvClient, createOvClient } from '#/shared/api/ov-client'

/** Keep the shared modules on Studio's data-plane transport and credential policy. */
export function StudioUiProvider({ children }: { children: ReactNode }) {
  const { identityScopeKey } = useAppConnection()
  const { i18n } = useTranslation()
  const locale = (i18n.resolvedLanguage ?? i18n.language).startsWith('zh')
    ? 'zh-CN'
    : 'en-US'
  const transport = useMemo(() => {
    const adapter = getOvClient()
    const connection = adapter.getConnection()
    const options = adapter.getOptions()
    // A dedicated adapter freezes this identity; later global switches cannot redirect a request.
    const scoped = createOvClient({
      ...options,
      credentialScope: 'data',
      apiKeyStorageKey: '',
      bindSdkClient: false,
    })
    scoped.setConnection(connection)
    return { adapter: scoped, connection, options, identityScopeKey }
  }, [identityScopeKey])
  const fetchAdapter = useMemo(
    () => async (input: RequestInfo | URL, init?: RequestInit) => {
      const adapter = transport.adapter
      const request = new Request(input, init)
      const response = await adapter.instance.request({
        url: request.url,
        method: request.method,
        data: ['GET', 'HEAD'].includes(request.method)
          ? undefined
          : await request.text(),
        headers: Object.fromEntries(request.headers.entries()),
        signal: request.signal,
        responseType: 'arraybuffer',
        transformResponse: (value) => value,
        validateStatus: () => true,
      })
      return new Response(
        response.status === 204 ||
          response.status === 205 ||
          request.method === 'HEAD'
          ? null
          : response.data,
        {
          status: response.status,
          headers: Object.fromEntries(
            Object.entries(response.headers).filter(
              (entry): entry is [string, string] =>
                typeof entry[1] === 'string',
            ),
          ),
        },
      )
    },
    [transport],
  )
  return (
    <OpenVikingProvider
      key={identityScopeKey}
      locale={locale}
      baseURL={transport.options.baseUrl}
      fetch={fetchAdapter}
    >
      {children}
    </OpenVikingProvider>
  )
}
