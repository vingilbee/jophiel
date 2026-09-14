import { chromium } from '../../frontend/node_modules/playwright/index.mjs'

const base = 'http://127.0.0.1:54173'
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
page.setDefaultTimeout(15000)
const events = []
const consoleWarnings = []
const pageErrors = []
page.on('pageerror', error => pageErrors.push(error.message))
page.on('console', message => {
  if (message.type() === 'error' || message.type() === 'warning') consoleWarnings.push(message.text())
})
page.on('websocket', socket => {
  socket.on('framesent', payload => {
    try { events.push({ direction: 'sent', data: JSON.parse(String(payload)) }) } catch {}
  })
  socket.on('framereceived', payload => {
    try { events.push({ direction: 'received', data: JSON.parse(String(payload)) }) } catch {}
  })
})

const email = `interrupt-${Date.now()}@example.com`
const password = 'Synthetic-interrupt-2026'
const signup = await context.request.post(base + '/api/auth/signup', {
  headers: { Origin: base },
  data: { name: 'Interruption QA', email, password },
})
if (signup.status() !== 201) throw new Error(`signup failed: ${signup.status()} ${await signup.text()}`)

await context.clearCookies()
await page.goto(base + '/login')
await page.getByPlaceholder('you@example.com').fill(email)
await page.locator('input[type=password]').fill(password)
await page.locator('form button[type=submit]').click()
await page.waitForURL('**/chat')
await page.getByText('Live', { exact: true }).waitFor()
const composer = page.getByPlaceholder('Ask about nutrition…')

const firstText = 'This is a deterministic QA reply for testing streaming and persistence.'
const results = []
const record = (test, pass, actual, expected) => {
  results.push({ test, status: pass ? 'PASS' : 'SUSPICIOUS', actual, expected })
}

// CT-02: stop partway through a real deterministic stream.
await composer.fill('Stop this response halfway')
await composer.press('Enter')
await page.getByRole('button', { name: 'Stop', exact: true }).waitFor()
await page.waitForTimeout(650)
const partialBeforeStop = await page.locator('body').innerText()
const receivedBeforeStop = events.filter(event => event.direction === 'received' && event.data.type === 'token').length
await page.getByRole('button', { name: 'Stop', exact: true }).click()
const stoppingVisible = await page.getByRole('button', { name: 'Stopping', exact: true }).count()
await page.getByRole('button', { name: 'Stopping', exact: true }).waitFor({ state: 'hidden' })
const receivedAtStop = events.filter(event => event.direction === 'received' && event.data.type === 'token').length
await page.waitForTimeout(500)
const receivedAfterStop = events.filter(event => event.direction === 'received' && event.data.type === 'token').length
const partialAfterStop = await page.locator('body').innerText()
record(
  'CT-02 stop halfway',
  stoppingVisible === 1 && receivedAfterStop === receivedAtStop && partialBeforeStop.includes('This is'),
  { stoppingVisible, tokensBeforeStop: receivedBeforeStop, tokensAtStop: receivedAtStop, tokens500msLater: receivedAfterStop, partialVisibleBeforeStop: partialBeforeStop.includes('This is') },
  'Stop acknowledges, preserves partial output, and receives zero later tokens',
)

// CT-03/08: follow-up immediately after stop; old stream must not contaminate it.
await composer.fill('Follow-up after stopping')
await composer.press('Enter')
await page.getByText(firstText, { exact: true }).waitFor()
await page.getByRole('button', { name: 'Stop', exact: true }).waitFor({ state: 'hidden' })
const followupBody = await page.locator('body').innerText()
const followupOccurrences = followupBody.split(firstText).length - 1
record(
  'CT-03 follow-up after stop',
  followupOccurrences === 1,
  { fullReplyOccurrences: followupOccurrences },
  'one clean follow-up reply with no stale-token contamination',
)

// CT-05: rapid submissions must not duplicate a user message or response.
await composer.fill('Rapid message one')
await page.getByRole('button', { name: 'Send', exact: true }).click()
await composer.fill('Rapid message two')
await page.getByRole('button', { name: 'Send', exact: true }).click()
await page.getByText('Rapid message one', { exact: true }).waitFor()
await page.getByText('Rapid message two', { exact: true }).waitFor()
await page.getByRole('button', { name: 'Stop', exact: true }).waitFor({ state: 'hidden', timeout: 20000 })
await page.waitForTimeout(1500)
const rapidOneCount = await page.getByText('Rapid message one', { exact: true }).count()
const rapidTwoCount = await page.getByText('Rapid message two', { exact: true }).count()
record(
  'CT-05 rapid follow-up sends',
  rapidOneCount === 1 && rapidTwoCount === 1,
  { rapidOneCount, rapidTwoCount },
  'one rendered user message for each rapid submission',
)

const conversationsResponse = await context.request.get(base + '/api/chat/conversations')
const conversations = await conversationsResponse.json()
const detailResponse = await context.request.get(base + '/api/chat/conversations/' + conversations[0].id)
const detail = await detailResponse.json()
const roles = detail.messages.map(message => message.role)
record(
  'CT-09 persisted ordering',
  roles.every((role, index) => index === 0 || role !== roles[index - 1]) && roles.filter(role => role === 'user').length === 4,
  { roles, messageCount: detail.messages.length, contents: detail.messages.map(message => ({ role: message.role, content: message.content })) },
  'four user turns and ordered assistant messages without duplicate turn records',
)

console.log(JSON.stringify({ results, pageErrors, consoleWarnings }, null, 2))
await browser.close()
