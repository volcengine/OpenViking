import { readdirSync, readFileSync } from 'node:fs'
import { dirname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { expect, it } from 'vitest'

const sourceRoot = fileURLToPath(new URL('../src', import.meta.url))
function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name)
    return entry.isDirectory()
      ? sourceFiles(path)
      : /\.tsx?$/.test(entry.name)
        ? [path]
        : []
  })
}
const files = sourceFiles(sourceRoot).filter(
  (file) =>
    !/\.test\.[^/]+$/.test(file) &&
    !file.includes('/gen/') &&
    !file.endsWith('routeTree.gen.ts'),
)
function owner(path: string) {
  if (path.startsWith('features/')) return path.split('/').slice(0, 2).join('/')
  return path.split('/')[0]
}
it('keeps shared, features, app and routes dependencies pointing toward their owners', () => {
  const violations: string[] = []
  for (const file of files) {
    const path = relative(sourceRoot, file)
    const source = ts.createSourceFile(
      file,
      readFileSync(file, 'utf8'),
      ts.ScriptTarget.Latest,
      true,
    )
    function visit(node: ts.Node) {
      // Include imports, exports and lazy imports, so a dynamic import cannot bypass a boundary.
      const specifier =
        ts.isImportDeclaration(node) || ts.isExportDeclaration(node)
          ? node.moduleSpecifier
          : ts.isCallExpression(node) &&
              node.expression.kind === ts.SyntaxKind.ImportKeyword
            ? node.arguments[0]
            : undefined
      if (specifier && ts.isStringLiteral(specifier)) {
        const name = specifier.text
        const target =
          name.startsWith('#/') || name.startsWith('@/')
            ? name.slice(2)
            : name.startsWith('.')
              ? relative(sourceRoot, resolve(dirname(file), name))
              : undefined
        if (target) {
          const from = owner(path),
            to = owner(target)
          if (
            /^(components|hooks|lib|workbench)\//.test(target) ||
            (from === 'shared' &&
              (to === 'app' ||
                to === 'routes' ||
                to.startsWith('features/'))) ||
            (from.startsWith('features/') &&
              (to === 'app' || to === 'routes')) ||
            (from === 'app' && to === 'routes') ||
            (from === 'features/admin' && to === 'features/workbench') ||
            (from === 'features/workbench' && to === 'features/admin') ||
            (from === 'features/connection' &&
              ['features/admin', 'features/workbench'].includes(to))
          ) {
            violations.push(`${path} -> ${name}`)
          }
        }
      }
      ts.forEachChild(node, visit)
    }
    visit(source)
    if (path.startsWith('routes/')) {
      // Route declarations can assemble a page, redirect or Outlet, but page state
      // and network effects belong to a feature or an app boundary.
      if (readFileSync(file, 'utf8').split('\n').length > 60)
        violations.push(`${path}: route implementation exceeds assembly scope`)
      if (
        /\b(useQuery|useMutation|useState|useEffect|useAppConnection)\b/.test(
          readFileSync(file, 'utf8'),
        )
      )
        violations.push(`${path}: business state in route declaration`)
    }
  }
  expect(violations).toEqual([])
})
