import { spawnSync } from 'node:child_process'

const result = spawnSync(process.execPath, ['../../node_modules/vitest/vitest.mjs', 'run'], {
  stdio: 'inherit',
  env: { ...process.env, NODE_ENV: 'test' },
})
if (result.status !== 0) process.exit(result.status ?? 1)
const mock = spawnSync(process.execPath, ['--test', 'test/mock-server.test.mjs'], { stdio: 'inherit' })
process.exit(mock.status ?? 1)
