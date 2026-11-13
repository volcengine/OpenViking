import { resolveStudioManagementCapabilities } from '#/features/connection/lib/studio-permissions'
import { resolveApiKeyAuth } from '#/shared/api/auth'
import {
  classifyCapabilityError,
  classifyCapabilityResponse,
  featureForPath,
  identifyServiceProvider,
  isStudioFeatureVisible,
  resolveConfiguredServiceProvider,
} from '#/shared/lib/studio-service'
import { describe, expect, it } from 'vitest'

describe('service and permission boundaries', () => {
  it.each([
    'http://10.37.244.186:8020',
    'http://172.16.0.1:1933',
    'http://172.31.255.255:1933',
    'http://192.168.1.20:1933',
    'http://127.0.0.2:1933',
    'http://[::1]:1933',
    'http://203.0.113.1:1933',
    'https://custom.company.example/openviking',
    'https://api.vikingdb.cn-beijing.volces.com.evil.example/openviking',
  ])(
    'defaults non-hosted deployment %s to open source with header authentication',
    (url) => {
      expect(resolveConfiguredServiceProvider(url)).toBe('opensource')
      expect(resolveApiKeyAuth(url)).toBe('header')
      expect(resolveConfiguredServiceProvider(url, 'volcengine')).toBe(
        'volcengine',
      )
      expect(resolveApiKeyAuth(url, 'volcengine')).toBe('bearer')
    },
  )
  it.each(['not a URL', '', 'file:///tmp/openviking'])(
    'keeps ambiguous deployment %s unconfirmed',
    (url) => {
      expect(identifyServiceProvider(url)).toBe('unknown')
    },
  )
  it('recognizes the official hosted URL without discarding its path', () => {
    expect(
      identifyServiceProvider(
        'https://api.vikingdb.cn-beijing.volces.com/openviking',
      ),
    ).toBe('volcengine')
    expect(
      identifyServiceProvider(
        'https://api.vikingdb.cn-beijing.volces.com.evil.example/openviking',
      ),
    ).toBe('opensource')
    expect(
      identifyServiceProvider('https://custom.company.example/openviking'),
    ).toBe('opensource')
    expect(identifyServiceProvider('http://localhost:1933')).toBe('opensource')
  })
  it.each(['volcengine', 'unknown'] as const)(
    'never maps %s admin/root data identity into native management',
    (serviceProvider) => {
      for (const role of ['admin', 'root'] as const) {
        expect(
          resolveStudioManagementCapabilities({
            serviceProvider,
            hasControlCredential: true,
            isRoleLoading: false,
            role,
            serverMode: 'api_key',
          }),
        ).toEqual({ canManageAccounts: false, canManageUsers: false })
      }
    },
  )
  it('distinguishes gateway blocking, user permission and uncertain failure', () => {
    expect(
      classifyCapabilityError({
        statusCode: 403,
        responseBody: { ResponseMetadata: { Error: { Code: 'ApiBlocked' } } },
      }),
    ).toBe('unavailable')
    expect(
      classifyCapabilityError({ statusCode: 403, code: 'PERMISSION_DENIED' }),
    ).toBe('forbidden')
    expect(classifyCapabilityError({ statusCode: 401 })).toBe('unknown')
    expect(classifyCapabilityError({ statusCode: 500 })).toBe('unknown')
    expect(classifyCapabilityError({})).toBe('unknown')
  })
  it('guards nested deep links with the same domain as navigation', () => {
    expect(featureForPath('/admin/users/memory-templates')?.domain).toBe(
      'management',
    )
    expect(featureForPath('/filesystem')?.domain).toBe('workspace')
    expect(featureForPath('/directory')?.domain).toBe('workspace')
  })
})

describe('hosted feature visibility', () => {
  const monitoring = featureForPath('/monitoring')!
  it.each([undefined, 'unknown', 'unavailable', 'forbidden'] as const)(
    'hides an extension while its capability is %s',
    (state) => {
      expect(isStudioFeatureVisible('volcengine', monitoring, state)).toBe(
        false,
      )
    },
  )
  it('shows only confirmed extensions, and keeps public workspace navigation', () => {
    expect(isStudioFeatureVisible('volcengine', monitoring, 'supported')).toBe(
      true,
    )
    expect(
      isStudioFeatureVisible(
        'volcengine',
        featureForPath('/compile')!,
        'supported',
      ),
    ).toBe(false)
    expect(
      isStudioFeatureVisible('volcengine', featureForPath('/directory')!),
    ).toBe(true)
    expect(
      isStudioFeatureVisible(
        'volcengine',
        featureForPath('/admin/users')!,
        'supported',
      ),
    ).toBe(false)
    expect(isStudioFeatureVisible('unknown', monitoring)).toBe(false)
    expect(
      isStudioFeatureVisible('opensource', featureForPath('/filesystem')!),
    ).toBe(true)
  })
})

describe('capability response contracts', () => {
  it('does not enable a page for generic JSON or a successful HTTP error envelope', () => {
    expect(classifyCapabilityResponse('watches', { message: 'proxy' })).toBe(
      'unknown',
    )
    expect(
      classifyCapabilityResponse('watches', {
        status: 'error',
        error: { code: 'ApiBlocked' },
      }),
    ).toBe('unavailable')
    expect(
      classifyCapabilityResponse('watches', {
        ResponseMetadata: { Error: { Code: 'ApiBlocked' } },
      }),
    ).toBe('unavailable')
    expect(classifyCapabilityResponse('watches', { result: [] })).toBe(
      'unknown',
    )
    expect(
      classifyCapabilityResponse('requestLogs', { result: { enabled: false } }),
    ).toBe('unavailable')
  })
  it('checks the read contract each supported hosted page consumes', () => {
    expect(
      classifyCapabilityResponse('watches', {
        status: 'ok',
        result: { tasks: [] },
      }),
    ).toBe('supported')
    expect(
      classifyCapabilityResponse('requestLogs', {
        result: { items: [], total: 0 },
      }),
    ).toBe('supported')
    expect(
      classifyCapabilityResponse('monitoring', {
        result: { components: {}, is_healthy: true },
      }),
    ).toBe('supported')
    expect(
      classifyCapabilityResponse('monitoring', { result: { status: 'ok' } }),
    ).toBe('unknown')
  })
  it.each(['/home', '/vikingbot', '/compile'])(
    'keeps %s hidden until the entire hosted page is adapted',
    (path) => {
      expect(
        isStudioFeatureVisible(
          'volcengine',
          featureForPath(path)!,
          'supported',
        ),
      ).toBe(false)
      expect(isStudioFeatureVisible('opensource', featureForPath(path)!)).toBe(
        true,
      )
    },
  )
})
