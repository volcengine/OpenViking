#!/usr/bin/env node
// OpenViking Web Studio — auto-proxy server.
//
// Serves the built `dist/` bundle and proxies OpenViking API traffic to an
// upstream OV server, injecting `X-API-Key` (and optional account / user) so
// the browser never sees the root API key. Configured entirely via env vars
// so a single static deployment can front any OV cluster.
//
// Usage:
//   OV_STUDIO_UPSTREAM=https://ov.example.com \
//   OV_STUDIO_API_KEY=root-key \
//   node server/proxy.mjs
//
// Optional env:
//   OV_STUDIO_HOST            (default 0.0.0.0)
//   OV_STUDIO_PORT            (default 3000)
//   OV_STUDIO_DIST_DIR        (default <package>/dist)
//   OV_STUDIO_ACCOUNT_ID      forwarded as X-OpenViking-Account
//   OV_STUDIO_USER_ID         forwarded as X-OpenViking-User
//   OV_STUDIO_CORS_ORIGINS    comma-separated, default same-origin only
//   OV_STUDIO_PROXY_PATHS     comma-separated path prefixes to forward
//                             (default: /api,/bot,/health,/ready,/openapi.json)
//   OV_STUDIO_BASE_PATH       SPA mount base, default "/"
//
import { createServer } from 'node:http'
import { request as httpRequest } from 'node:http'
import { request as httpsRequest } from 'node:https'
import { existsSync, createReadStream, statSync } from 'node:fs'
import { dirname, extname, join, normalize, resolve } from 'node:path'
import { fileURLToPath, URL } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const PKG_ROOT = resolve(__dirname, '..')

const env = process.env
const HOST = env.OV_STUDIO_HOST || '0.0.0.0'
const PORT = Number.parseInt(env.OV_STUDIO_PORT || '3000', 10)
const DIST_DIR = resolve(env.OV_STUDIO_DIST_DIR || join(PKG_ROOT, 'dist'))
const UPSTREAM = (env.OV_STUDIO_UPSTREAM || '').trim().replace(/\/+$/, '')
const API_KEY = (env.OV_STUDIO_API_KEY || '').trim()
const ACCOUNT_ID = (env.OV_STUDIO_ACCOUNT_ID || '').trim()
const USER_ID = (env.OV_STUDIO_USER_ID || '').trim()
const BASE_PATH = normalizeBasePath(env.OV_STUDIO_BASE_PATH || '/')
const PROXY_PATHS = parsePaths(
  env.OV_STUDIO_PROXY_PATHS || '/api,/bot,/health,/ready,/openapi.json',
)
const CORS_ORIGINS = new Set(
  (env.OV_STUDIO_CORS_ORIGINS || '')
    .split(',')
    .map((origin) => origin.trim())
    .filter(Boolean),
)
const ALLOW_ANY_CORS = CORS_ORIGINS.has('*')

if (!UPSTREAM) {
  console.error('[ov-studio-proxy] OV_STUDIO_UPSTREAM is required')
  process.exit(1)
}
if (!API_KEY) {
  console.error('[ov-studio-proxy] OV_STUDIO_API_KEY is required')
  process.exit(1)
}
if (!existsSync(DIST_DIR)) {
  console.error(
    `[ov-studio-proxy] dist directory not found: ${DIST_DIR}. Run "npm run build" first.`,
  )
  process.exit(1)
}

const upstreamUrl = new URL(UPSTREAM)
const upstreamRequest = upstreamUrl.protocol === 'https:' ? httpsRequest : httpRequest
const STRIPPED_REQUEST_HEADERS = new Set([
  'host',
  'connection',
  'x-api-key',
  'authorization',
  'x-openviking-account',
  'x-openviking-user',
  'x-openviking-agent',
  'cookie',
])
const HOP_BY_HOP_RESPONSE_HEADERS = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailers',
  'transfer-encoding',
  'upgrade',
])

const RUNTIME_CONFIG_PATH = joinPath(BASE_PATH, '_studio/runtime-config.json')

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.mjs': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.txt': 'text/plain; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
}

const server = createServer((req, res) => {
  try {
    handle(req, res)
  } catch (error) {
    console.error('[ov-studio-proxy] unexpected error', error)
    if (!res.headersSent) {
      res.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' })
    }
    res.end('Internal Studio proxy error')
  }
})

server.listen(PORT, HOST, () => {
  console.log(
    `[ov-studio-proxy] listening on http://${HOST}:${PORT} → upstream ${UPSTREAM} (base ${BASE_PATH})`,
  )
})

function handle(req, res) {
  applyCors(req, res)
  if (req.method === 'OPTIONS') {
    res.writeHead(204).end()
    return
  }

  const pathname = safePathname(req.url)
  if (pathname === RUNTIME_CONFIG_PATH) {
    sendRuntimeConfig(res)
    return
  }

  if (matchesPrefix(pathname, PROXY_PATHS)) {
    proxyToUpstream(req, res)
    return
  }

  serveStatic(req, res, pathname)
}

function sendRuntimeConfig(res) {
  const body = JSON.stringify({
    proxyMode: true,
    baseUrl: '',
    hasManagedAccount: Boolean(ACCOUNT_ID),
    hasManagedUser: Boolean(USER_ID),
  })
  res.writeHead(200, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
  })
  res.end(body)
}

function proxyToUpstream(req, res) {
  const targetPath = (req.url || '/').replace(/^\/+/, '/')
  const headers = filterRequestHeaders(req.headers)
  headers['x-api-key'] = API_KEY
  if (ACCOUNT_ID) headers['x-openviking-account'] = ACCOUNT_ID
  if (USER_ID) headers['x-openviking-user'] = USER_ID
  headers['x-openviking-agent'] = 'web-studio-proxy'
  headers.host = upstreamUrl.host

  const upstream = upstreamRequest(
    {
      protocol: upstreamUrl.protocol,
      hostname: upstreamUrl.hostname,
      port: upstreamUrl.port || (upstreamUrl.protocol === 'https:' ? 443 : 80),
      method: req.method,
      path: targetPath,
      headers,
    },
    (upstreamRes) => {
      const responseHeaders = filterResponseHeaders(upstreamRes.headers)
      res.writeHead(upstreamRes.statusCode || 502, responseHeaders)
      upstreamRes.pipe(res)
    },
  )

  upstream.on('error', (error) => {
    console.error('[ov-studio-proxy] upstream error', error.message)
    if (!res.headersSent) {
      res.writeHead(502, { 'content-type': 'application/json; charset=utf-8' })
    }
    res.end(JSON.stringify({ status: 'error', error: { code: 'UPSTREAM_UNREACHABLE', message: error.message } }))
  })

  req.pipe(upstream)
}

function serveStatic(req, res, pathname) {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405, { 'content-type': 'text/plain; charset=utf-8' })
    res.end('Method not allowed')
    return
  }

  const relative = pathname.replace(/^\/+/, '')
  const filePath = normalize(join(DIST_DIR, relative))
  if (!filePath.startsWith(DIST_DIR)) {
    res.writeHead(403).end('Forbidden')
    return
  }

  if (existsSync(filePath) && statSync(filePath).isFile()) {
    return streamFile(res, filePath, req.method)
  }

  const fallback = join(DIST_DIR, 'index.html')
  if (!existsSync(fallback)) {
    res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' })
    res.end('Not found')
    return
  }
  streamFile(res, fallback, req.method)
}

function streamFile(res, filePath, method) {
  const ext = extname(filePath).toLowerCase()
  const mime = MIME[ext] || 'application/octet-stream'
  const stat = statSync(filePath)
  const headers = {
    'content-type': mime,
    'content-length': stat.size,
    // Don't cache index.html — runtime config / SPA bundle hashes change.
    'cache-control': ext === '.html' ? 'no-store' : 'public, max-age=300',
  }
  res.writeHead(200, headers)
  if (method === 'HEAD') {
    res.end()
    return
  }
  createReadStream(filePath).pipe(res)
}

function applyCors(req, res) {
  const origin = req.headers.origin
  if (!origin) return
  if (ALLOW_ANY_CORS) {
    res.setHeader('access-control-allow-origin', origin)
    res.setHeader('vary', 'Origin')
  } else if (CORS_ORIGINS.has(origin)) {
    res.setHeader('access-control-allow-origin', origin)
    res.setHeader('vary', 'Origin')
  } else {
    return
  }
  res.setHeader('access-control-allow-credentials', 'true')
  res.setHeader(
    'access-control-allow-headers',
    req.headers['access-control-request-headers'] ||
      'content-type,accept,x-openviking-agent',
  )
  res.setHeader(
    'access-control-allow-methods',
    'GET,POST,PUT,PATCH,DELETE,OPTIONS',
  )
}

function filterRequestHeaders(input) {
  const out = {}
  for (const [key, value] of Object.entries(input)) {
    if (STRIPPED_REQUEST_HEADERS.has(key.toLowerCase())) continue
    out[key] = value
  }
  return out
}

function filterResponseHeaders(input) {
  const out = {}
  for (const [key, value] of Object.entries(input)) {
    if (HOP_BY_HOP_RESPONSE_HEADERS.has(key.toLowerCase())) continue
    out[key] = value
  }
  return out
}

function matchesPrefix(pathname, prefixes) {
  for (const prefix of prefixes) {
    if (pathname === prefix) return true
    if (pathname.startsWith(prefix.endsWith('/') ? prefix : `${prefix}/`)) return true
    if (prefix.includes('.') && pathname === prefix) return true
  }
  return false
}

function parsePaths(raw) {
  return raw
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean)
    .map((entry) => (entry.startsWith('/') ? entry : `/${entry}`))
}

function normalizeBasePath(raw) {
  const trimmed = raw.trim()
  if (!trimmed || trimmed === '/') return '/'
  const withLeading = trimmed.startsWith('/') ? trimmed : `/${trimmed}`
  return withLeading.replace(/\/+$/, '')
}

function joinPath(base, suffix) {
  const left = base === '/' ? '' : base
  const right = suffix.startsWith('/') ? suffix : `/${suffix}`
  return `${left}${right}`
}

function safePathname(rawUrl) {
  try {
    return new URL(rawUrl || '/', 'http://placeholder').pathname
  } catch {
    return '/'
  }
}
