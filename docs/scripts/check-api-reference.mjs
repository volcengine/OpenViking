import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const routerDir = path.join(repoRoot, 'openviking/server/routers')
const locales = ['zh', 'en']
const allApiDocs = locales.flatMap((locale) =>
  fs.readdirSync(path.join(repoRoot, 'docs', locale, 'api'))
    .filter((file) => file.endsWith('.md'))
    .map((file) => path.join(repoRoot, 'docs', locale, 'api', file))
)
const apiDocs = allApiDocs.filter(
  (file) => !path.basename(file).startsWith('01-') && !path.basename(file).startsWith('99-')
)
const overviewDocs = locales.map((locale) =>
  path.join(repoRoot, 'docs', locale, 'api', '01-overview.md')
)
const httpMethods = 'GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS|PROPFIND|MKCOL|MOVE'

function normalizePath(value) {
  const pathname = value.split('?')[0].replace(/\/$/, '') || '/'
  return pathname.replace(/\{[^}:]+(?::[^}]+)?\}/g, '{}')
}

function closingDelimiter(source, openingIndex, opening = '(', closing = ')') {
  let depth = 0
  let quote = ''
  let escaped = false
  for (let index = openingIndex; index < source.length; index++) {
    const character = source[index]
    if (quote) {
      if (escaped) escaped = false
      else if (character === '\\') escaped = true
      else if (character === quote) quote = ''
      continue
    }
    if (character === '"' || character === "'" || character === '`') {
      quote = character
      continue
    }
    if (character === opening) depth++
    else if (character === closing && --depth === 0) return index
  }
  return -1
}

function splitTopLevel(source) {
  const parts = []
  let start = 0
  const stack = []
  let quote = ''
  let escaped = false
  const pairs = { '(': ')', '[': ']', '{': '}' }
  for (let index = 0; index < source.length; index++) {
    const character = source[index]
    if (quote) {
      if (escaped) escaped = false
      else if (character === '\\') escaped = true
      else if (character === quote) quote = ''
      continue
    }
    if (character === '"' || character === "'" || character === '`') quote = character
    else if (pairs[character]) stack.push(pairs[character])
    else if (character === stack.at(-1)) stack.pop()
    else if (character === ',' && stack.length === 0) {
      parts.push(source.slice(start, index).trim())
      start = index + 1
    }
  }
  const tail = source.slice(start).trim()
  if (tail) parts.push(tail)
  return parts
}

const routes = new Map()
const middlewareQueryParameters = new Set(['profile'])
const internalRouterFiles = new Set(['console.py', 'debug.py', 'stats.py', 'user_settings.py'])
for (const file of fs.readdirSync(routerDir).filter((name) => name.endsWith('.py'))) {
  if (internalRouterFiles.has(file)) continue
  const source = fs.readFileSync(path.join(routerDir, file), 'utf8')
  const routerPrefix =
    source.match(/router\s*=\s*APIRouter\([^)]*prefix=["']([^"']+)/s)?.[1] ?? ''
  const mountPrefix = file === 'bot.py' ? '/bot/v1' : ''
  const decorators = [
    ...Array.from(
      source.matchAll(/@router\.(get|post|put|patch|delete)\(\s*["']([^"']*)/g),
      (match) => ({
        index: match.index,
        methods: [match[1].toUpperCase()],
        path: match[2],
        sourceLength: match[0].length,
      })
    ),
    ...Array.from(
      source.matchAll(
        /@router\.api_route\(\s*["']([^"']*)["']\s*,\s*methods\s*=\s*\[([^\]]+)\]/g
      ),
      (match) => ({
        index: match.index,
        methods: Array.from(match[2].matchAll(/["']([A-Z]+)["']/g), (item) => item[1]),
        path: match[1],
        sourceLength: match[0].length,
      })
    ),
  ].sort((a, b) => a.index - b.index)

  for (const decorator of decorators) {
    const definition = source.indexOf('def ', decorator.index + decorator.sourceLength)
    const opening = source.indexOf('(', definition)
    const closing = closingDelimiter(source, opening)
    const signature = closing < 0 ? '' : source.slice(opening + 1, closing)
    const routePath = mountPrefix + routerPrefix + decorator.path
    const pathParameters = new Set(
      Array.from(routePath.matchAll(/\{([^}:]+)(?::[^}]+)?\}/g), (item) => item[1])
    )
    const query = new Map()
    for (const parameter of splitTopLevel(signature)) {
      const queryMatch = parameter.match(/^([A-Za-z_]\w*)\s*:[\s\S]*?=\s*Query\(([\s\S]*)\)$/)
      if (queryMatch) {
        const alias = queryMatch[2].match(/\balias\s*=\s*["']([^"']+)["']/)?.[1]
        query.set(alias ?? queryMatch[1], /^\s*\.\.\.(?:\s*,|\s*$)/.test(queryMatch[2]))
        continue
      }

      const defaultMatch = parameter.match(/^([A-Za-z_]\w*)\s*:[\s\S]*?=\s*([\s\S]+)$/)
      if (!defaultMatch || pathParameters.has(defaultMatch[1])) continue
      if (
        /^(?:Path|Depends|Body|Header|Cookie|File|Form|Security)\s*\(/.test(defaultMatch[2])
      ) continue
      query.set(defaultMatch[1], false)
    }
    for (const method of decorator.methods) {
      routes.set(`${method} ${normalizePath(routePath)}`, { query, pathParameters })
    }
  }
}

const clientSource = fs.readFileSync(path.join(repoRoot, 'sdk/typescript/src/client.ts'), 'utf8')
const clientMethods = new Map()
for (const match of clientSource.matchAll(/^  (?:async )?([A-Za-z][A-Za-z0-9]*)\s*\(/gm)) {
  const opening = clientSource.indexOf('(', match.index)
  const closing = closingDelimiter(clientSource, opening)
  if (closing < 0) continue
  const parameters = splitTopLevel(clientSource.slice(opening + 1, closing))
  const required = parameters.filter((parameter) => {
    const declaration = parameter.split(':', 1)[0]
    return !declaration.includes('?') && !parameter.includes('=') && !parameter.startsWith('...')
  }).length
  clientMethods.set(match[1], {
    required,
    maximum: parameters.some((parameter) => parameter.startsWith('...'))
      ? Infinity
      : parameters.length,
    parameters
  })
}

const errors = []
let httpExamples = 0
let typescriptCalls = 0
const curlUrlPattern = String.raw`["']?https?:\/\/[^/\s"']+(\/[A-Za-z0-9_{}<>?=&./:-]+)`
const detailCoverage = new Map(locales.map((locale) => [locale, new Set()]))

function findRoute(method, documentedPath) {
  const normalizedPath = normalizePath(documentedPath)
  const exact = routes.get(`${method} ${normalizedPath}`)
  if (exact) return exact
  for (const [candidate, candidateContract] of routes) {
    const separator = candidate.indexOf(' ')
    const candidateMethod = candidate.slice(0, separator)
    const candidatePath = candidate.slice(separator + 1)
    if (candidateMethod !== method) continue
    const placeholderPattern =
      candidatePath.startsWith('/webdav/resources/{}') ||
      candidatePath.startsWith('/api/v1/system/sync/{}')
        ? '.+'
        : '[^/]+'
    const pattern = candidatePath
      .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      .replace(/\\\{\\\}/g, placeholderPattern)
    if (new RegExp(`^${pattern}$`).test(normalizedPath)) return candidateContract
  }
  return undefined
}

function tableReferences(source) {
  const references = []
  for (const line of source.split(/\r?\n/)) {
    if (!line.startsWith('|')) continue
    const cells = line.split('|').slice(1, -1).map((cell) => cell.trim())
    if (cells.length < 2) continue
    const methodCell = cells[0].replaceAll('`', '')
    const methods = Array.from(
      methodCell.matchAll(new RegExp(`\\b(${httpMethods})\\b`, 'g')),
      (match) => match[1]
    )
    const paths = Array.from(
      cells[1].matchAll(/`(\/[A-Za-z0-9_{}./:-]+)`/g),
      (match) => match[1]
    )
    for (const method of methods) {
      for (const documentedPath of paths) references.push([method, documentedPath])
    }
  }
  return references
}

function curlReferences(source) {
  const references = []
  for (const match of source.matchAll(/\bcurl\b[^\n]*(?:\r?\n[ \t]+(?:--|-H\b)[^\n]*)*/g)) {
    const command = match[0]
    const url = command.match(new RegExp(curlUrlPattern))
    if (!url) continue

    const explicitMethod = command.match(
      new RegExp(`(?:-X|--request)\\s+(${httpMethods})\\b`)
    )?.[1]
    const method = explicitMethod ?? 'GET'
    let documentedPath = url[1]
    const queryNames = Array.from(
      command.matchAll(/--data-urlencode\s+["']?([A-Za-z_]\w*)=/g),
      (item) => item[1]
    )
    if (queryNames.length) {
      const existingNames = new Set(
        (documentedPath.split('?')[1] ?? '')
          .split('&')
          .filter(Boolean)
          .map((item) => item.split('=')[0])
      )
      const missing = queryNames.filter((name) => !existingNames.has(name))
      if (missing.length) {
        documentedPath += `${documentedPath.includes('?') ? '&' : '?'}${missing
          .map((name) => `${name}={}`)
          .join('&')}`
      }
    }
    references.push([method, documentedPath])
  }
  return references
}

for (const file of apiDocs) {
  const source = fs.readFileSync(file, 'utf8')
  const relative = path.relative(repoRoot, file)
  const locale = relative.split(path.sep)[1]
  const httpReferences = [
    ...Array.from(
      source.matchAll(
        new RegExp(
          `\\b(${httpMethods})\\s+((?:\\/api\\/|\\/webdav\\/|\\/bot\\/|\\/(?:health|ready|metrics)\\b)` +
          `[A-Za-z0-9_{}?=&./:-]*)`,
          'g'
        )
      ),
      (match) => [match[1], match[2]]
    ),
    ...curlReferences(source),
    ...tableReferences(source),
  ]
  for (const [method, documentedPath] of httpReferences) {
    const normalizedPath = normalizePath(documentedPath)
    const route = `${method} ${normalizedPath}`
    detailCoverage.get(locale).add(route)
    httpExamples++
    const contract = findRoute(method, documentedPath)
    if (!contract) {
      errors.push(`${relative}: unknown HTTP route ${route}`)
      continue
    }
    const documentedPathParameters = new Set(
      Array.from(documentedPath.split('?')[0].matchAll(/\{([^}]+)\}/g), (item) => item[1])
    )
    if (documentedPathParameters.size) {
      for (const name of documentedPathParameters) {
        if (!contract.pathParameters.has(name)) {
          errors.push(`${relative}: ${route} has unknown path parameter ${name}`)
        }
      }
      for (const name of contract.pathParameters) {
        if (!documentedPathParameters.has(name)) {
          errors.push(`${relative}: ${route} is missing path parameter ${name}`)
        }
      }
    }
    const queryNames = new Set(
      (documentedPath.split('?')[1] ?? '')
        .split('&')
        .filter(Boolean)
        .map((item) => item.split('=')[0])
    )
    for (const name of queryNames) {
      if (!contract.query.has(name) && !middlewareQueryParameters.has(name)) {
        errors.push(`${relative}: ${route} has unknown query parameter ${name}`)
      }
    }
    for (const [name, required] of contract.query) {
      if (required && !queryNames.has(name)) {
        errors.push(`${relative}: ${route} is missing required query parameter ${name}`)
      }
    }
  }
  for (const block of source.matchAll(/```(?:typescript|ts)\n([\s\S]*?)\n```/g)) {
    for (const call of block[1].matchAll(/\bclient\.([A-Za-z][A-Za-z0-9]*)\s*\(/g)) {
      typescriptCalls++
      const contract = clientMethods.get(call[1])
      if (!contract) {
        errors.push(`${relative}: unknown TypeScript SDK method client.${call[1]}()`)
        continue
      }
      const opening = call.index + call[0].lastIndexOf('(')
      const closing = closingDelimiter(block[1], opening)
      if (closing < 0) {
        errors.push(`${relative}: could not parse TypeScript SDK call client.${call[1]}()`)
        continue
      }
      const args = splitTopLevel(block[1].slice(opening + 1, closing))
      const argumentCount = args.length
      if (argumentCount < contract.required || argumentCount > contract.maximum) {
        const expected = contract.required === contract.maximum
          ? String(contract.required)
          : `${contract.required}-${contract.maximum}`
        errors.push(
          `${relative}: client.${call[1]}() has ${argumentCount} arguments; expected ${expected}`
        )
      }
      for (let index = 0; index < Math.min(args.length, contract.parameters.length); index++) {
        const type = contract.parameters[index].match(/:\s*([^=]+?)(?:\s*=|$)/)?.[1]?.trim()
        if (type === 'string' && /^[{[]/.test(args[index])) {
          errors.push(`${relative}: client.${call[1]}() argument ${index + 1} must be a string`)
        }
      }
    }
  }
}

function routeIsCovered(route, coverage) {
  if (coverage.has(route)) return true
  const separator = route.indexOf(' ')
  const method = route.slice(0, separator)
  const routePath = route.slice(separator + 1)
  const pattern = routePath
    .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    .replace(
      /\\\{\\\}/g,
      routePath.startsWith('/webdav/resources/{}') ||
        routePath.startsWith('/api/v1/system/sync/{}')
        ? '.+'
        : '[^/]+'
    )
  for (const reference of coverage) {
    const referenceSeparator = reference.indexOf(' ')
    if (reference.slice(0, referenceSeparator) !== method) continue
    if (new RegExp(`^${pattern}$`).test(reference.slice(referenceSeparator + 1))) return true
  }
  return false
}

const overviewCoverage = new Map()
for (const file of overviewDocs) {
  const source = fs.readFileSync(file, 'utf8')
  const relative = path.relative(repoRoot, file)
  const locale = relative.split(path.sep)[1]
  const coverage = new Set()
  for (const row of source.matchAll(
    new RegExp(`^\\|\\s*(${httpMethods})(?:\\s*\\/\\s*(${httpMethods}))?\\s*\\|([^\\n]+)$`, 'gm')
  )) {
    const methods = [row[1], row[2]].filter(Boolean)
    const documentedPaths = Array.from(
      row[3].matchAll(/`(\/[A-Za-z0-9_{}./:-]+)`/g),
      (match) => match[1]
    )
    for (const method of methods) {
      for (const documentedPath of documentedPaths) {
        const route = `${method} ${normalizePath(documentedPath)}`
        coverage.add(route)
        if (!findRoute(method, documentedPath)) {
          errors.push(`${relative}: overview contains unknown HTTP route ${route}`)
        }
      }
    }
  }
  overviewCoverage.set(locale, coverage)
}

for (const locale of locales) {
  const overview = overviewCoverage.get(locale)
  const details = detailCoverage.get(locale)
  for (const route of routes.keys()) {
    if (!routeIsCovered(route, overview)) {
      errors.push(`docs/${locale}/api/01-overview.md: mounted route missing from overview: ${route}`)
    }
    if (!routeIsCovered(route, details)) {
      errors.push(`docs/${locale}/api: mounted route has no detailed HTTP reference: ${route}`)
    }
  }
  for (const route of overview) {
    if (!routeIsCovered(route, details)) {
      errors.push(`docs/${locale}/api: overview route has no detailed HTTP reference: ${route}`)
    }
  }
}

for (const file of allApiDocs) {
  const source = fs.readFileSync(file, 'utf8')
  const relative = path.relative(repoRoot, file)
  for (const match of source.matchAll(
    new RegExp(`^#{1,6}\\s+(Python SDK|TypeScript SDK|JavaScript SDK|Go SDK|HTTP API|CLI)[：:]?\\s*$`, 'gm')
  )) {
    errors.push(
      `${relative}:${source.slice(0, match.index).split('\\n').length}: ` +
      `use a bold invocation label (${match[1]}) so examples render as tabs`
    )
  }
  for (const match of source.matchAll(
    /^\*\*(Python SDK|TypeScript SDK|JavaScript SDK|Go SDK|HTTP API|CLI)\*\*\s*[（(]/gm
  )) {
    errors.push(
      `${relative}:${source.slice(0, match.index).split('\n').length}: ` +
      `put the ${match[1]} qualifier inside the bold label with ASCII parentheses`
    )
  }
  for (const match of source.matchAll(
    /^\*\*(Python SDK|TypeScript SDK|JavaScript SDK|Go SDK|HTTP API|CLI)\s*（[^）]+）\*\*/gm
  )) {
    errors.push(
      `${relative}:${source.slice(0, match.index).split('\n').length}: ` +
      `use ASCII parentheses in the ${match[1]} invocation label`
    )
  }
}

if (errors.length) {
  console.error(errors.join('\n'))
  process.exitCode = 1
} else {
  console.log(
    `API reference check passed: ${httpExamples} HTTP examples with query contracts, ` +
    `${typescriptCalls} TypeScript SDK calls with signature contracts, ` +
    `${routes.size} mounted routes covered by overview and detail docs`
  )
}
