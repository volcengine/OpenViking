import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'
import { startMockServer } from './mock-server.mjs'

const packageDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const screenshots = path.resolve(packageDir, '../../docs/console')
await mkdir(screenshots, { recursive: true })
const server = await startMockServer()
let browser
try {
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, deviceScaleFactor: 1 })
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  const origin = `http://127.0.0.1:${server.address().port}`
  await page.goto(`${origin}/?demo=1`)
  await expect(page.getByRole('heading', { name: 'Instance overview' })).toBeVisible()
  await expect(page.getByText('Interactive mock demo').first()).toBeVisible()

  await page.getByRole('link', { name: 'Runtime', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Runtime', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Apply Runtime transition' })).toBeEnabled()
  await page.locator('select').nth(3).selectOption('high')
  await page.getByRole('button', { name: 'Set effort' }).click()
  await expect(page.getByText('Effort transition returned a new effective snapshot.')).toBeVisible()
  await page.screenshot({ path: path.join(screenshots, 'demo-runtime.png'), fullPage: true, animations: 'disabled' })

  await page.getByRole('link', { name: 'Configuration', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Configuration', exact: true })).toBeVisible()
  await page.locator('select').nth(3).selectOption('high')
  await page.getByRole('button', { name: 'Save with If-Match' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.getByRole('button', { name: 'Validate & apply' }).click()
  await expect(page.getByText('Configuration apply Operation created.')).toBeVisible()
  await expect(page.getByText('true', { exact: true }).first()).toBeVisible()
  await page.screenshot({ path: path.join(screenshots, 'demo-configuration.png'), fullPage: true, animations: 'disabled' })

  await page.getByRole('link', { name: 'Providers', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Model providers' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'ark-main', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Rotate credential' }).first().click()
  await page.getByLabel('New API key').fill('browser-secret-not-persisted')
  await page.getByRole('button', { name: 'Write mock credential' }).click()
  await expect(page.getByText('Credential reference updated.')).toBeVisible()
  await expect(page.getByText('browser-secret-not-persisted')).toHaveCount(0)
  await page.screenshot({ path: path.join(screenshots, 'demo-providers.png'), fullPage: true, animations: 'disabled' })

  await page.getByRole('link', { name: 'Diagnostics', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Logs & diagnostics' })).toBeVisible()
  await expect(page.getByText('Email connector authentication expired; reconnect from the CLI')).toBeVisible()
  await page.screenshot({ path: path.join(screenshots, 'demo-diagnostics.png'), fullPage: true, animations: 'disabled' })

  await page.getByRole('link', { name: 'Tasks', exact: true }).click()
  await page.getByRole('link', { name: /Implement the OVSA console frontend/ }).click()
  await page.getByRole('button', { name: 'Cancel task' }).click()
  await expect(page.getByText('External effects are not undone')).toBeVisible()
  await page.getByRole('button', { name: 'Request cancellation' }).click()
  await expect(page.getByText('Stop was requested.')).toBeVisible()
  await expect(page.getByText('stopped', { exact: true }).first()).toBeVisible()
  await page.screenshot({ path: path.join(screenshots, 'demo-task-cancel.png'), fullPage: true, animations: 'disabled' })

  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: 'Open navigation' }).click()
  await page.getByRole('link', { name: 'Configuration', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Configuration', exact: true })).toBeVisible()
  await page.screenshot({ path: path.join(screenshots, 'demo-mobile-configuration.png'), fullPage: true, animations: 'disabled' })
  assert.deepEqual(errors, [])
  console.log(`Mock demo browser checks passed; screenshots: ${screenshots}`)
} finally {
  await browser?.close()
  await new Promise((resolve) => { server.close(resolve); server.closeAllConnections?.() })
}
