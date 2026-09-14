import { expect, test, type Page } from '@playwright/test'

const assetId = '0123456789abcdef'
const file = {
  asset_id: assetId, filename: 'Weekly nutrition report.pdf', media_type: 'application/pdf',
  size_bytes: 1024, created_at: '2026-09-05T00:00:00Z', kind: 'generated',
}

async function setup(page: Page, available = true) {
  await page.route('**/api/auth/me', route => route.fulfill({ json: {
    id: '00000000-0000-0000-0000-000000000001', name: 'Browser Test',
    email: 'test@example.com', created_at: '2026-01-01T00:00:00Z',
  } }))
  await page.route('**/api/chat/conversations', route => route.fulfill({ json: [] }))
  await page.route(`**/api/assets/${assetId}`, route => route.fulfill(
    available ? { json: file } : { status: 404, json: { detail: 'Asset not found' } },
  ))
  await page.route(`**/api/assets/${assetId}/raw*`, route => route.fulfill({
    contentType: 'application/pdf', body: '%PDF-1.4\n%%EOF',
    headers: { 'Content-Disposition': route.request().url().includes('preview=true')
      ? 'inline; filename="report.pdf"' : 'attachment; filename="report.pdf"' },
  }))
  await page.routeWebSocket('**/api/chat/ws', ws => ws.onMessage(raw => {
    if (typeof raw !== 'string' || JSON.parse(raw).type !== 'run') return
    ws.send(JSON.stringify({ type: 'meta', conversation_id: '10000000-0000-0000-0000-000000000001' }))
    ws.send(JSON.stringify({ type: 'token', content: `Your report is ready. [Download report](/api/assets/${assetId}/raw)` }))
    ws.send(JSON.stringify({ type: 'done' }))
  }))
  await page.goto('/chat')
  await expect(page.getByText('Live')).toBeVisible()
  const composer = page.getByPlaceholder('Ask about nutrition…')
  await composer.fill('Create a weekly nutrition report PDF')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
}

test('generated PDF has a preview and a working download', async ({ page }) => {
  await setup(page)
  await expect(page.getByText(file.filename, { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByTitle(`Preview of ${file.filename}`)).toHaveAttribute('src', `/api/assets/${assetId}/raw?preview=true`)
  await expect(page.getByTitle(`Preview of ${file.filename}`)).toBeVisible()
  const download = page.waitForEvent('download')
  await page.getByRole('link', { name: 'Download', exact: true }).click()
  expect((await download).suggestedFilename()).toMatch(/\.pdf$/)
  await page.getByRole('button', { name: 'Close preview' }).click()
  await expect(page.getByTitle(`Preview of ${file.filename}`)).toHaveCount(0)
})

test('unavailable files show an error without offering a preview', async ({ page }) => {
  await setup(page, false)
  await expect(page.getByText('This file is unavailable or belongs to another account.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Preview', exact: true })).toHaveCount(0)
})
