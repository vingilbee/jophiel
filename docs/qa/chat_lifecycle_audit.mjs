import { chromium } from '../../frontend/node_modules/playwright/index.mjs'

const base = 'http://127.0.0.1:54173'
const trustedOrigin = base
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
page.setDefaultTimeout(15000)
const results = []
const pageErrors = []
const warnings = []
page.on('pageerror', error => pageErrors.push(error.message))
page.on('console', message => {
  if (message.type() === 'error' || message.type() === 'warning') warnings.push(message.text())
})
const record = (test, pass, actual, expected) => results.push({ test, status: pass ? 'PASS' : 'SUSPICIOUS', actual, expected })

const email = `lifecycle-${Date.now()}@example.com`
const password = 'Synthetic-lifecycle-2026'
const signup = await context.request.post(base + '/api/auth/signup', {
  headers: { Origin: trustedOrigin },
  data: { name: 'Lifecycle QA', email, password },
})
if (signup.status() !== 201) throw new Error(`signup failed: ${signup.status()} ${await signup.text()}`)
await context.clearCookies()
await page.goto(base + '/login')
await page.getByPlaceholder('you@example.com').fill(email)
await page.locator('input[type=password]').fill(password)
await page.locator('form button[type=submit]').click()
// A WebSocket can remain open while the SPA finishes initializing, so do not
// make the login assertion wait for the browser's full load event.
await page.waitForURL('**/chat', { waitUntil: 'domcontentloaded' })
await page.getByText('Live', { exact: true }).waitFor()
const composer = page.getByPlaceholder('Ask about nutrition…')

// LC-01/02/03: disconnect and recover during a real stream.
await composer.fill('Disconnect during streaming')
await composer.press('Enter')
await page.getByRole('button', { name: 'Stop', exact: true }).waitFor()
await page.waitForTimeout(250)
await context.setOffline(true)
await page.waitForTimeout(1800)
const offlineBody = await page.locator('body').innerText()
const offlineState = ['Offline', 'Connecting', 'Failed', 'assistant'].find(value => offlineBody.includes(value)) ?? 'no visible recovery state'
await context.setOffline(false)
let reconnected = true
try { await page.getByText('Live', { exact: true }).waitFor({ timeout: 10000 }) } catch { reconnected = false }
record(
  'LC-01/02/03 disconnect and reconnect',
  reconnected,
  { visibleStateDuringDisconnect: offlineState, reconnected },
  'clear degraded state, then recover to Live without a stuck spinner',
)

// LC-04: refresh while a real response is active.
await composer.fill('Refresh during streaming')
await composer.press('Enter')
await page.getByRole('button', { name: 'Stop', exact: true }).waitFor()
await page.reload()
let refreshedLive = true
try { await page.getByText('Live', { exact: true }).waitFor({ timeout: 10000 }) } catch { refreshedLive = false }
const refreshedBody = await page.locator('body').innerText()
record(
  'LC-04 refresh during streaming',
  refreshedLive && refreshedBody.includes('Refresh during streaming'),
  { refreshedLive, userMessageRestored: refreshedBody.includes('Refresh during streaming') },
  'chat returns to usable Live state with the user turn restored exactly once',
)

// LC-05/06: navigation away and return during an active response.
await composer.fill('Navigate during streaming')
await composer.press('Enter')
await page.getByRole('button', { name: 'Stop', exact: true }).waitFor()
await page.goto(base + '/dashboard')
await page.getByRole('heading').first().waitFor()
await page.goto(base + '/chat')
let returnedLive = true
try { await page.getByText('Live', { exact: true }).waitFor({ timeout: 10000 }) } catch { returnedLive = false }
const returnedBody = await page.locator('body').innerText()
record(
  'LC-05/06 navigation lifecycle',
  returnedLive && returnedBody.includes('Navigate during streaming'),
  { returnedLive, userMessageRestored: returnedBody.includes('Navigate during streaming') },
  'navigation does not leave a stuck request and returning restores the transcript',
)

// Persistence check after two immediate follow-ups.
await page.getByText('Live', { exact: true }).waitFor()
await page.getByPlaceholder('Ask about nutrition…').fill('Persistence rapid one')
await page.getByRole('button', { name: 'Send', exact: true }).click()
await page.getByPlaceholder('Ask about nutrition…').fill('Persistence rapid two')
await page.getByRole('button', { name: 'Send', exact: true }).click()
await page.waitForTimeout(4000)
const conversations = await (await context.request.get(base + '/api/chat/conversations')).json()
const detail = await (await context.request.get(base + '/api/chat/conversations/' + conversations[0].id)).json()
const rapidUsers = detail.messages.filter(message => message.role === 'user' && message.content.startsWith('Persistence rapid'))
const rapidTurnIds = new Set(rapidUsers.map(message => message.turn_id).filter(Boolean))
const rapidOutcomes = detail.messages.filter(message => message.role === 'assistant' && rapidTurnIds.has(message.turn_id))
record(
  'LC-07 rapid follow-up persistence',
  rapidUsers.length === 2 && rapidOutcomes.length === 2 && rapidOutcomes.every(message => ['completed', 'partial', 'cancelled', 'failed'].includes(message.status)),
  { rapidUsers: rapidUsers.map(message => ({ turn_id: message.turn_id, content: message.content })), rapidOutcomes: rapidOutcomes.map(message => ({ turn_id: message.turn_id, status: message.status, content: message.content })) },
  'both user turns have exactly one explicit assistant outcome',
)

// LC-08: repeated stop/refresh cycles for stuck state and obvious accumulation.
const cycleStates = []
for (let cycle = 1; cycle <= 5; cycle += 1) {
  await page.getByText('Live', { exact: true }).waitFor()
  await page.getByPlaceholder('Ask about nutrition…').fill(`Cycle ${cycle}`)
  await page.getByPlaceholder('Ask about nutrition…').press('Enter')
  await page.getByRole('button', { name: 'Stop', exact: true }).waitFor()
  await page.getByRole('button', { name: 'Stop', exact: true }).click()
  await page.getByRole('button', { name: 'Stopping', exact: true }).waitFor({ state: 'hidden' })
  await page.reload()
  let live = true
  try {
    await page.getByText('Live', { exact: true }).waitFor({ timeout: 10000 })
  } catch {
    live = false
  }
  // Send is intentionally disabled for an empty composer. Fill a harmless
  // probe so this assertion measures connection readiness, not input validity.
  await page.getByPlaceholder('Ask about nutrition…').fill(`Cycle ${cycle} ready probe`)
  const sendEnabled = await page.getByRole('button', { name: 'Send', exact: true }).isEnabled()
  cycleStates.push({ cycle, live, sendEnabled })
}
record(
  'LC-08 repeated stop-refresh cycles',
  cycleStates.every(state => state.live && state.sendEnabled),
  cycleStates,
  'five cycles recover to usable Live/Send state without a stuck control',
)

console.log(JSON.stringify({ results, pageErrors, warnings }, null, 2))
await browser.close()
