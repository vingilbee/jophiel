import { chromium } from '../../frontend/node_modules/playwright/index.mjs'

const base = 'http://127.0.0.1:54173'
const browser = await chromium.launch({ headless: true })
const results = []
const record = (test, pass, actual, expected) => {
  const row = { test, status: pass ? 'PASS' : 'FAIL', actual, expected }
  results.push(row)
  console.log(JSON.stringify(row))
}

async function signedIn(label) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  page.setDefaultTimeout(15000)
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const email = `live-chat-${label}-${Date.now()}@example.com`
  const password = 'Synthetic-live-chat-2026'
  const signup = await context.request.post(base + '/api/auth/signup', {
    headers: { Origin: base }, data: { name: `Live chat ${label}`, email, password },
  })
  if (signup.status() !== 201) throw new Error(`signup failed: ${signup.status()}`)
  await context.clearCookies()
  await page.goto(base + '/login')
  await page.getByPlaceholder('you@example.com').fill(email)
  await page.locator('input[type=password]').fill(password)
  await page.locator('form').getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.waitForURL('**/chat')
  await page.getByText('Live', { exact: true }).waitFor()
  return { context, page, composer: page.getByPlaceholder('Ask about nutrition…'), errors }
}

async function waitStopReady(page, timeout = 15000) {
  const stop = page.getByRole('button', { name: 'Stop', exact: true })
  await stop.waitFor()
  await page.waitForFunction(() => {
    const button = [...document.querySelectorAll('button')].find(node => node.getAttribute('aria-label') === 'Stop')
    return Boolean(button && !button.disabled)
  }, undefined, { timeout })
  return stop
}

async function waitTurnSettled(page, timeout = 30000) {
  await page.getByRole('button', { name: 'Stop', exact: true }).waitFor({ state: 'hidden', timeout })
}

// Stop, then follow up on the same real socket.
{
  const { context, page, composer, errors } = await signedIn('stop')
  const sockets = []
  page.on('websocket', socket => sockets.push(socket))
  await composer.fill('Real live stop smoothness probe')
  await composer.press('Enter')
  let stopReady = true
  try { await waitStopReady(page) } catch { stopReady = false }
  const beforeStop = await page.locator('body').innerText()
  if (stopReady) await page.getByRole('button', { name: 'Stop', exact: true }).click()
  let settled = true
  try { await waitTurnSettled(page, 20000) } catch { settled = false }
  const afterStop = await page.locator('body').innerText()
  record('SM-STOP real stop control', stopReady && settled, { stopReady, settled, errors }, 'Stop becomes enabled and returns to idle')
  await composer.fill('Real live follow-up after stop')
  await composer.press('Enter')
  let followup = true
  try { await waitTurnSettled(page, 30000) } catch { followup = false }
  const body = await page.locator('body').innerText()
  record('SM-STOP follow-up recovery', followup && body.includes('Real live follow-up after stop'), { followup, userVisible: body.includes('Real live follow-up after stop'), errors }, 'follow-up completes without a stuck state')
  await context.close()
}

// Rapid sends and persisted user/assistant turn accounting.
{
  const { context, page, composer, errors } = await signedIn('rapid')
  await composer.fill('Real rapid message one')
  await composer.press('Enter')
  await page.waitForTimeout(250)
  await composer.fill('Real rapid message two')
  await composer.press('Enter')
  let settled = true
  try { await waitTurnSettled(page, 45000) } catch { settled = false }
  try { await waitTurnSettled(page, 45000) } catch { settled = false }
  const detail = await page.evaluate(async () => {
    const list = await (await fetch('/api/chat/conversations')).json()
    return list.length ? await (await fetch(`/api/chat/conversations/${list[0].id}`)).json() : null
  })
  const users = detail?.messages.filter(message => message.content?.startsWith('Real rapid message')) ?? []
  const turnIds = new Set(users.map(message => message.turn_id).filter(Boolean))
  const assistants = detail?.messages.filter(message => message.role === 'assistant' && turnIds.has(message.turn_id)) ?? []
  record('SM-RAPID real rapid sends', settled && users.length === 2 && assistants.length === 2, { settled, users: users.length, assistants: assistants.length, errors }, 'two user turns and two explicit assistant outcomes')
  await context.close()
}

// Refresh and navigation while a real response is active.
{
  const { context, page, composer, errors } = await signedIn('lifecycle')
  await composer.fill('Real refresh during streaming probe')
  await composer.press('Enter')
  let started = true
  try { await waitStopReady(page) } catch { started = false }
  await page.reload()
  let refreshed = true
  try { await page.getByText('Live', { exact: true }).waitFor({ timeout: 15000 }) } catch { refreshed = false }
  const afterRefresh = await page.locator('body').innerText()
  record('SM-LIFECYCLE refresh during stream', started && refreshed && afterRefresh.includes('Real refresh during streaming probe'), { started, refreshed, userRestored: afterRefresh.includes('Real refresh during streaming probe'), errors }, 'refresh returns to Live and restores the user turn')

  await page.getByPlaceholder('Ask about nutrition…').fill('Real navigation during streaming probe')
  await page.getByPlaceholder('Ask about nutrition…').press('Enter')
  let navStarted = true
  try { await waitStopReady(page) } catch { navStarted = false }
  await page.goto(base + '/dashboard')
  await page.goto(base + '/chat')
  let returned = true
  try { await page.getByText('Live', { exact: true }).waitFor({ timeout: 15000 }) } catch { returned = false }
  const afterNav = await page.locator('body').innerText()
  record('SM-LIFECYCLE navigation during stream', navStarted && returned && afterNav.includes('Real navigation during streaming probe'), { navStarted, returned, userRestored: afterNav.includes('Real navigation during streaming probe'), errors }, 'navigation returns to usable Live chat with transcript')
  await context.close()
}

console.log(JSON.stringify({ total: results.length, passed: results.filter(row => row.status === 'PASS').length, failed: results.filter(row => row.status === 'FAIL').length }))
await browser.close()
