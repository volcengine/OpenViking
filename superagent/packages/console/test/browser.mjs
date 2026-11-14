import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'
import { ActivityWebServer } from '../../web/src/server.mjs'
import { startFixtureDaemon } from './fixture-server.mjs'

const packageDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const screenshots = path.resolve(packageDir, '../../docs/console')
await mkdir(screenshots, { recursive: true })
const daemon = await startFixtureDaemon()
const web = new ActivityWebServer({
  host: '127.0.0.1', port: 0,
  instances: [
    { id: 'primary', label: 'Primary Agent', host: '127.0.0.1', port: daemon.address().port },
    { id: 'research', label: 'Research Agent', host: '127.0.0.1', port: daemon.address().port },
  ],
  defaultInstance: 'primary', publicDir: path.resolve(packageDir, '../web/public'),
  upstreamTimeoutMs: 1000, shutdownTimeoutMs: 1000,
})
let browser
try {
  await web.start()
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, deviceScaleFactor: 1 })
  const errors = []
  const methods = new Set()
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('request', (request) => methods.add(request.method()))
  const origin = `http://127.0.0.1:${web.address().port}`

  await page.goto(origin)
  await expect(page.getByRole('heading', { name: 'Instance overview' })).toBeVisible()
  await expect(page.getByText('Email connector needs attention')).toBeVisible()
  await expect(page.getByText('Accepting work')).toBeVisible()
  await expect(page.getByText('degraded', { exact: true })).toBeVisible()
  await page.screenshot({ path: path.join(screenshots, 'overview.png'), fullPage: true, animations: 'disabled' })

  await page.getByRole('link', { name: 'Tasks', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Tasks', exact: true })).toBeVisible()
  await expect(page.getByText('Implement the OVSA console frontend')).toBeVisible()
  await page.screenshot({ path: path.join(screenshots, 'tasks.png'), fullPage: true, animations: 'disabled' })

  await page.getByRole('link', { name: /Design the console read API/ }).click()
  await expect(page.getByRole('heading', { name: 'Design the console read API' })).toBeVisible()
  await expect(page.getByText('Final IM delivery')).toBeVisible()
  await expect(page.getByText('inbox_928')).toBeVisible()
  await page.screenshot({ path: path.join(screenshots, 'task-detail.png'), fullPage: true, animations: 'disabled' })

  await page.getByRole('tab', { name: /Reports/ }).click()
  await expect(page.getByText('Final delivery is uncertain')).toBeVisible()
  await expect(page.getByText(/Check the IM manually before retrying/)).toBeVisible()
  await page.screenshot({ path: path.join(screenshots, 'delivery-states.png'), fullPage: true, animations: 'disabled' })

  await page.getByRole('link', { name: 'Capabilities', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Capabilities', exact: true })).toBeVisible()
  await expect(page.getByText('Not supported on this platform', { exact: true })).toBeVisible()
  await page.screenshot({ path: path.join(screenshots, 'capabilities.png'), fullPage: true, animations: 'disabled' })

  // Current daemons have no v1 API: the page must say so, not render fixture tasks.
  await page.route('**/api/v1/capabilities?*', (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"not_found"}' }))
  await page.route('**/api/v1/instances/*/overview?*', (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"not_found"}' }))
  await page.goto(`${origin}/#/overview`)
  await page.reload()
  await expect(page.getByText('Legacy observation mode')).toBeVisible()
  await expect(page.getByText('Task observation unavailable')).toBeVisible()
  await page.screenshot({ path: path.join(screenshots, 'legacy-boundary.png'), fullPage: true, animations: 'disabled' })

  await page.unrouteAll()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`${origin}/#/tasks`)
  await expect(page.getByRole('heading', { name: 'Tasks', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Open navigation' }).click()
  await expect(page.getByRole('link', { name: 'Overview', exact: true })).toBeVisible()
  await page.screenshot({ path: path.join(screenshots, 'mobile-navigation.png'), fullPage: true, animations: 'disabled' })

  assert.deepEqual(errors, [])
  assert.deepEqual([...methods], ['GET'], 'The read-only browser flow must not issue mutations')
  console.log(`Browser checks passed; screenshots: ${screenshots}`)
} finally {
  await browser?.close()
  await web.stop()
  await new Promise((resolve) => { daemon.close(resolve); daemon.closeAllConnections?.() })
}
