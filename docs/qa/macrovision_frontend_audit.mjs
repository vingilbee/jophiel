import { chromium } from '../../frontend/node_modules/playwright/index.mjs'

const base = 'http://127.0.0.1:54173'
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
page.setDefaultTimeout(30000)
const pageErrors = []
page.on('pageerror', error => pageErrors.push(error.message))
const email = `mcp-frontend-${Date.now()}@example.com`
const password = 'Synthetic-mcp-frontend-2026'
const signup = await context.request.post(base + '/api/auth/signup', {
  headers: { Origin: base }, data: { name: 'MCP Frontend QA', email, password },
})
if (signup.status() !== 201) throw new Error(`signup failed: ${signup.status()} ${await signup.text()}`)
await page.goto(base + '/login')
await page.getByPlaceholder('you@example.com').fill(email)
await page.locator('input[type=password]').fill(password)
await page.locator('form button[type=submit]').click()
await page.waitForURL('**/chat')
await page.getByText('Live', { exact: true }).waitFor()

const image = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64',
)
await page.locator('input[type=file]').setInputFiles({ name: 'qa-food.png', mimeType: 'image/png', buffer: image })
await page.getByText('qa-food.png', { exact: true }).waitFor()
const composer = page.getByPlaceholder('Ask about nutrition…')
await composer.fill('Analyze the attached food photo and estimate its calories and macros.')
await composer.press('Enter')
await page.getByRole('button', { name: 'Stop', exact: true }).waitFor()
await page.getByRole('button', { name: 'Stop', exact: true }).waitFor({ state: 'hidden', timeout: 120000 })
const body = await page.locator('body').innerText()
const authIssue = /authorization|unauthorized|missing or expired|scope token|permission denied/i.test(body)
const mcpMention = /macrovision|calorie|macro|protein|photo|image/i.test(body)
console.log(JSON.stringify({
  status: authIssue ? 'SUSPICIOUS' : 'PASS',
  authIssue,
  mcpMention,
  pageErrors,
  tail: body.slice(-2500),
}, null, 2))
await browser.close()
