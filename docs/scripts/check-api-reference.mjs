import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const routerDir = path.join(repoRoot, 'openviking/server/routers')
const apiDocs = ['zh', 'en'].flatMap((locale) =>
  fs.readdirSync(path.join(repoRoot, 'docs', locale, 'api'))
    .filter((file) => file.endsWith('.md') && !file.startsWith('01-') && !file.startsWith('99-'))
    .map((file) => path.join(repoRoot, 'docs', locale, 'api', file))
)

function normalizePath(value) {
  const pathname = value.split('?')[0].replace(/\/$/, '') || '/'
  return pathname.replace(/\{[^}]+\}/g, '{}')
}

function consumeQuotedCharacter(character, state) {
  if (state.quote) {
    if (state.escaped) state.escaped = false
    else if (character === '\\') state.escaped = true
    else if (character === state.quote) state.quote = ''
    return true
  }
  if (character === '"' || character === "'" || character === '`') {
    state.quote = character
    return true
  }
  return false
}

function closingDelimiter(source, openingIndex, opening = '(', closing = ')') {
  let depth = 0
  const quoteState = { quote: '', escaped: false }
  for (let index = openingIndex; index < source.length; index++) {
    const character = source[index]
    if (consumeQuotedCharacter(character, quoteState)) continue
    if (character === opening) depth++
    else if (character === closing && --depth === 0) return index
  }
  return -1
}

function splitTopLevel(source) {
  const parts = []
  let start = 0
  const stack = []
  const quoteState = { quote: '', escaped: false }
  const pairs = { '(': ')', '[': ']', '{': '}' }
  for (let index = 0; index < source.length; index++) {
    const character = source[index]
    if (consumeQuotedCharacter(character, quoteState)) continue
    if (pairs[character]) stack.push(pairs[character])
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
for (const file of fs.readdirSync(routerDir).filter((name) => name.endsWith('.py'))) {
  const source = fs.readFileSync(path.join(routerDir, file), 'utf8')
  const prefix = source.match(/router\s*=\s*APIRouter\([^)]*prefix=["']([^"']+)/s)?.[1] ?? ''
  for (const match of source.matchAll(/@router\.(get|post|put|patch|delete)\(\s*["']([^"']*)/g)) {
    const definition = source.indexOf('def ', match.index + match[0].length)
    const opening = source.indexOf('(', definition)
    const closing = closingDelimiter(source, opening)
    const signature = closing < 0 ? '' : source.slice(opening + 1, closing)
    const routePath = prefix + match[2]
    const pathParameters = new Set(
      Array.from(routePath.matchAll(/\{([^}]+)\}/g), (item) => item[1])
    )
    const query = new Map()
    for (const parameter of splitTopLevel(signature)) {
      const queryMatch = parameter.match(/^([A-Za-z_]\w*)\s*:[\s\S]*?=\s*Query\(([\s\S]*)\)$/)
      if (queryMatch) {
        query.set(queryMatch[1], /^\s*\.\.\.(?:\s*,|\s*$)/.test(queryMatch[2]))
        continue
      }

      const defaultMatch = parameter.match(/^([A-Za-z_]\w*)\s*:[\s\S]*?=\s*([\s\S]+)$/)
      if (!defaultMatch || pathParameters.has(defaultMatch[1])) continue
      if (
        /^(?:Path|Depends|Body|Header|Cookie|File|Form|Security)\s*\(/.test(defaultMatch[2])
      ) continue
      query.set(defaultMatch[1], false)
    }
    routes.set(`${match[1].toUpperCase()} ${normalizePath(routePath)}`, { query, pathParameters })
  }
}

const clientSource = fs.readFileSync(path.join(repoRoot, 'sdk/typescript/src/client.ts'), 'utf8')
const typeSource = fs.readFileSync(path.join(repoRoot, 'sdk/typescript/src/types.ts'), 'utf8')
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

const interfaceDefinitions = new Map()
const interfacePattern = /export interface\s+([A-Za-z]\w*)(?:\s+extends\s+([^\{]+))?\s*\{/g
for (const match of typeSource.matchAll(interfacePattern)) {
  const opening = typeSource.indexOf('{', match.index)
  const closing = closingDelimiter(typeSource, opening, '{', '}')
  if (closing < 0) continue
  const properties = new Set(
    Array.from(
      typeSource.slice(opening + 1, closing).matchAll(/^\s*([A-Za-z]\w*)\??\s*:/gm),
      (property) => property[1]
    )
  )
  const parents = (match[2] ?? '')
    .split(',')
    .map((parent) => parent.trim())
    .filter(Boolean)
  interfaceDefinitions.set(match[1], { parents, properties })
}

function interfaceProperties(name, seen = new Set()) {
  if (seen.has(name)) return new Set()
  seen.add(name)
  const definition = interfaceDefinitions.get(name)
  if (!definition) return new Set()
  const properties = new Set(definition.properties)
  for (const parent of definition.parents) {
    for (const property of interfaceProperties(parent, seen)) properties.add(property)
  }
  return properties
}

function methodSource(name) {
  const declaration = new RegExp(
    `^  (?:(?:private|public|protected)\\s+)?(?:async\\s+)?${name}\\s*\\(`,
    'm'
  ).exec(clientSource)
  if (!declaration) return ''
  const signatureOpening = clientSource.indexOf('(', declaration.index)
  const signatureClosing = closingDelimiter(clientSource, signatureOpening)
  if (signatureClosing < 0) return ''
  const bodyOpening = clientSource.indexOf('{', signatureClosing)
  const bodyClosing = closingDelimiter(clientSource, bodyOpening, '{', '}')
  return bodyClosing < 0 ? '' : clientSource.slice(declaration.index, bodyClosing + 1)
}

const sdkContractFixtures = [
  {
    method: 'addResource',
    required: [
      'this.request("POST", "/api/v1/resources"',
      'create_parent: options.createParent'
    ]
  },
  {
    method: 'searchRequest',
    required: [
      'this.request("POST", `/api/v1/search/${kind}`',
      'include_provenance: options.includeProvenance'
    ]
  },
  {
    method: 'tree',
    required: [
      'this.request("GET", "/api/v1/fs/tree"',
      'level_limit: options.levelLimit'
    ]
  },
  {
    method: 'getSystemStatus',
    required: ['this.request("GET", "/api/v1/system/status"']
  }
]

const crossSdkSources = {
  python: fs.readFileSync(
    path.join(repoRoot, 'sdk/python/openviking_sdk/client.py'),
    'utf8'
  ),
  goResources: fs.readFileSync(path.join(repoRoot, 'sdk/go/resources.go'), 'utf8'),
  goRetrieval: fs.readFileSync(path.join(repoRoot, 'sdk/go/retrieval.go'), 'utf8'),
  goFilesystem: fs.readFileSync(path.join(repoRoot, 'sdk/go/filesystem.go'), 'utf8'),
  goSystem: fs.readFileSync(path.join(repoRoot, 'sdk/go/system.go'), 'utf8')
}

const crossSdkContractFixtures = [
  {
    label: 'Python SDK',
    source: crossSdkSources.python,
    required: [
      '"create_parent": create_parent',
      '"include_provenance": True if include_provenance else None',
      'params["level_limit"] = level_limit',
      'json={"action": action}',
      'self._request("GET", "/api/v1/system/status")'
    ]
  },
  {
    label: 'Go resource SDK',
    source: crossSdkSources.goResources,
    required: ['"create_parent":         opts.CreateParent']
  },
  {
    label: 'Go retrieval SDK',
    source: crossSdkSources.goRetrieval,
    required: ['payload["tags"] = opts.Tags', 'payload["include_provenance"]']
  },
  {
    label: 'Go filesystem SDK',
    source: crossSdkSources.goFilesystem,
    required: ['queryInt(query, "level_limit", *opts.LevelLimit)']
  },
  {
    label: 'Go system SDK',
    source: crossSdkSources.goSystem,
    required: ['c.doJSON(ctx, http.MethodGet, "/api/v1/system/status"']
  }
]

const errors = []
for (const fixture of sdkContractFixtures) {
  const source = methodSource(fixture.method)
  if (!source) {
    errors.push(`sdk/typescript/src/client.ts: missing contract method ${fixture.method}()`)
    continue
  }
  for (const required of fixture.required) {
    if (!source.includes(required)) {
      errors.push(
        `sdk/typescript/src/client.ts: ${fixture.method}() is missing contract mapping ${required}`
      )
    }
  }
}
for (const fixture of crossSdkContractFixtures) {
  for (const required of fixture.required) {
    if (!fixture.source.includes(required)) {
      errors.push(`${fixture.label}: missing contract mapping ${required}`)
    }
  }
}
let httpExamples = 0
let typescriptCalls = 0
const curlUrlPattern = String.raw`["']?https?:\/\/[^/\s"']+(\/[A-Za-z0-9_{}<>?=&./:-]+)`
const explicitCurlPattern = new RegExp(
  String.raw`\bcurl\b[^\n]*?(?:-X|--request)\s+(GET|POST|PUT|PATCH|DELETE)\s+` +
    curlUrlPattern,
  'g'
)
const implicitGetCurlPattern = new RegExp(
  String.raw`\bcurl\b(?![^\n]*(?:-X|--request))[^\n]*?` + curlUrlPattern,
  'g'
)
for (const file of apiDocs) {
  const source = fs.readFileSync(file, 'utf8')
  const relative = path.relative(repoRoot, file)
  const httpReferences = [
    ...Array.from(
      source.matchAll(/^\s*(GET|POST|PUT|PATCH|DELETE)\s+(\/[A-Za-z0-9_{}?=&./:-]+)/gm),
      (match) => [match[1], match[2]]
    ),
    ...Array.from(
      source.matchAll(explicitCurlPattern),
      (match) => [match[1], match[2]]
    ),
    ...Array.from(
      source.matchAll(implicitGetCurlPattern),
      (match) => ['GET', match[1]]
    )
  ]
  for (const [method, documentedPath] of httpReferences) {
    const normalizedPath = normalizePath(documentedPath)
    const route = `${method} ${normalizedPath}`
    httpExamples++
    let contract = routes.get(route)
    if (!contract) {
      for (const [candidate, candidateContract] of routes) {
        const [candidateMethod, candidatePath] = candidate.split(' ', 2)
        if (candidateMethod !== method) continue
        const pattern = candidatePath
          .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
          .replace(/\\\{\\\}/g, '[^/]+')
        if (new RegExp(`^${pattern}$`).test(normalizedPath)) {
          contract = candidateContract
          break
        }
      }
    }
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
      if (!contract.query.has(name)) {
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
        if (!args[index].startsWith('{') || !args[index].endsWith('}')) continue
        const typeMatches = type?.matchAll(/\b([A-Z][A-Za-z0-9]*)\b/g) ?? []
        const typeNames = Array.from(typeMatches, (item) => item[1])
        const allowedProperties = new Set()
        for (const typeName of typeNames) {
          for (const property of interfaceProperties(typeName)) allowedProperties.add(property)
        }
        if (!allowedProperties.size) continue
        for (const property of splitTopLevel(args[index].slice(1, -1))) {
          if (property.startsWith('...')) continue
          const propertyName = property.match(/^([A-Za-z_$][A-Za-z0-9_$]*)\s*(?::|$)/)?.[1]
          if (propertyName && !allowedProperties.has(propertyName)) {
            errors.push(
              `${relative}: client.${call[1]}() argument ${index + 1} ` +
              `has unknown option ${propertyName}`
            )
          }
        }
      }
    }
  }
}

if (errors.length) {
  console.error(errors.join('\n'))
  process.exitCode = 1
} else {
  console.log(
    `API reference check passed: ${httpExamples} HTTP examples with query contracts, ` +
    `${typescriptCalls} TypeScript SDK calls with signature/option contracts, ` +
    `${sdkContractFixtures.length} TypeScript and ` +
    `${crossSdkContractFixtures.length} cross-SDK request fixtures`
  )
}
