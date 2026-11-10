import { spawn } from 'node:child_process'

const children = [
  spawn(
    'npm',
    [
      '--prefix',
      '../frontend',
      'run',
      'dev',
      '--workspace',
      '@openviking/react',
    ],
    { stdio: 'inherit', detached: true },
  ),
  spawn(
    'npm',
    ['exec', '--', 'vite', '--port', '3000', ...process.argv.slice(2)],
    { stdio: 'inherit', detached: true },
  ),
]
let stopping = false
function stop(code = 0) {
  if (stopping) return
  stopping = true
  for (const child of children) {
    if (child.pid) {
      try {
        process.kill(-child.pid, 'SIGTERM')
      } catch {
        /* Already exited. */
      }
    }
  }
  process.exitCode = code
}
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => stop())
for (const child of children) {
  child.on('error', (error) => {
    console.error(error)
    stop(1)
  })
  child.on('exit', (code) => stop(code ?? 1))
}
