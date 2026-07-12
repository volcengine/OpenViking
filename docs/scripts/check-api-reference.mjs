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

const routes = new Set()
for (const file of fs.readdirSync(routerDir).filter((name) => name.endsWith('.py'))) {
  const source = fs.readFileSync(path.join(routerDir, file), 'utf8')
  const prefix = source.match(/router\s*=\s*APIRouter\([^)]*prefix=["']([^"']+)/s)?.[1] ?? ''
  for (const match of source.matchAll(/@router\.(get|post|put|patch|delete)\(\s*["']([^"']*)/g)) {
    routes.add(`${match[1].toUpperCase()} ${normalizePath(prefix + match[2])}`)
  }
}

const clientSource = fs.readFileSync(path.join(repoRoot, 'sdk/typescript/src/client.ts'), 'utf8')
const clientMethods = new Set(
  Array.from(clientSource.matchAll(/^  (?:async )?([A-Za-z][A-Za-z0-9]*)\s*\(/gm), (match) => match[1])
)

const errors = []
let httpExamples = 0
let typescriptCalls = 0
for (const file of apiDocs) {
  const source = fs.readFileSync(file, 'utf8')
  const relative = path.relative(repoRoot, file)
  for (const match of source.matchAll(/^\s*(GET|POST|PUT|PATCH|DELETE)\s+(\/[A-Za-z0-9_{}?=&./:-]+)/gm)) {
    const route = `${match[1]} ${normalizePath(match[2])}`
    httpExamples++
    if (!routes.has(route)) errors.push(`${relative}: unknown HTTP route ${route}`)
  }
  for (const block of source.matchAll(/```(?:typescript|ts)\n([\s\S]*?)\n```/g)) {
    for (const call of block[1].matchAll(/\bclient\.([A-Za-z][A-Za-z0-9]*)\s*\(/g)) {
      typescriptCalls++
      if (!clientMethods.has(call[1])) {
        errors.push(`${relative}: unknown TypeScript SDK method client.${call[1]}()`)
      }
    }
  }
}

if (errors.length) {
  console.error(errors.join('\n'))
  process.exitCode = 1
} else {
  console.log(`API reference check passed: ${httpExamples} HTTP examples, ${typescriptCalls} TypeScript SDK calls`)
}
