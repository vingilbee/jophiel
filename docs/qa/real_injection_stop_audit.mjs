import { chromium } from '../../frontend/node_modules/playwright/index.mjs'

const base = 'http://localhost:5173'
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
page.setDefaultTimeout(30000)
const pageErrors = []
page.on('pageerror', error => pageErrors.push(error.message))
const email = `real-injection-${Date.now()}@example.com`
const password = 'Synthetic-real-injection-2026'
const signup = await context.request.post(base + '/api/auth/signup', {
  headers: { Origin: base }, data: { name: 'Real Injection QA', email, password },
})
if (signup.status() !== 201) throw new Error(`signup failed: ${signup.status()} ${await signup.text()}`)
// Signup establishes the real session cookie; enter the protected UI directly.
await page.goto(base + '/chat')
if (!page.url().includes('/chat')) throw new Error(`protected route redirected to ${page.url()}`)
await page.getByText('Live', { exact: true }).waitFor()
const composer = page.getByPlaceholder('Ask about nutrition…')
const stop = page.getByRole('button', { name: 'Stop', exact: true })
const stopping = page.getByRole('button', { name: 'Stopping', exact: true })

await composer.fill('Give me a detailed explanation of how protein supports muscle recovery, with examples.')
await composer.press('Enter')
await stop.waitFor()
const firstStopVisible = await stop.isVisible()
const firstText = await page.locator('body').innerText()

await composer.fill('Actually, interrupt that and explain hydration instead.')
const injectedBeforeSend = {
  stopVisible: await stop.isVisible().catch(() => false),
  sendButtons: await page.getByRole('button', { name: 'Send', exact: true }).count(),
}
await composer.press('Enter')
const afterSendAt0 = {
  stopVisible: await stop.isVisible().catch(() => false),
  stoppingVisible: await stopping.isVisible().catch(() => false),
  sendButtons: await page.getByRole('button', { name: 'Send', exact: true }).count(),
}
await page.waitForTimeout(300)
const afterSendAt300 = {
  stopVisible: await stop.isVisible().catch(() => false),
  stoppingVisible: await stopping.isVisible().catch(() => false),
  sendButtons: await page.getByRole('button', { name: 'Send', exact: true }).count(),
}
await page.waitForTimeout(1200)
const afterSendAt1500 = {
  stopVisible: await stop.isVisible().catch(() => false),
  stoppingVisible: await stopping.isVisible().catch(() => false),
  sendButtons: await page.getByRole('button', { name: 'Send', exact: true }).count(),
}
let stopWorked = false
if (await stop.isVisible().catch(() => false)) {
  await stop.click()
  stopWorked = await stopping.isVisible().catch(() => false)
}
await page.waitForTimeout(2500)
const finalBody = await page.locator('body').innerText()
console.log(JSON.stringify({
  firstStopVisible,
  firstResponseStarted: firstText.includes('protein') || firstText.includes('Protein'),
  injectedBeforeSend,
  afterSendAt0,
  afterSendAt300,
  afterSendAt1500,
  stopWorked,
  pageErrors,
  tail: finalBody.slice(-1800),
}, null, 2))
await browser.close()
