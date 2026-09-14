import { chromium } from '../../frontend/node_modules/playwright/index.mjs'

const base = 'http://127.0.0.1:54173'
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
page.setDefaultTimeout(20000)
const results = []
const errors = []
const sockets = []
page.on('pageerror', error => errors.push(error.message))
page.on('websocket', socket => sockets.push(socket.url()))
const record = (test, pass, actual, expected) => results.push({ test, status: pass ? 'PASS' : 'SUSPICIOUS', actual, expected })

const email = `remaining-${Date.now()}@example.com`
const password = 'Synthetic-remaining-2026'
const signup = await context.request.post(base + '/api/auth/signup', {
  headers: { Origin: base },
  data: { name: 'Remaining QA', email, password },
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

async function send(text) {
  await composer.fill(text)
  await composer.press('Enter')
  await page.getByRole('button', { name: 'Stop', exact: true }).waitFor()
  await page.getByRole('button', { name: 'Stop', exact: true }).waitFor({ state: 'hidden', timeout: 20000 })
}

await send('QA-MARKDOWN')
record('ST-06 markdown/code streaming',
  (await page.locator('body').innerText()).includes("print('safe')"),
  { codeVisible: (await page.locator('body').innerText()).includes("print('safe')") },
  'markdown and code remain readable after incremental rendering',
)

await send('QA-MALFORMED')
record('ST-07 malformed stream event',
  (await page.locator('body').innerText()).includes('Malformed event was ignored safely.'),
  { pageErrors: errors.length },
  'unknown provider event is ignored without crashing or corrupting the transcript',
)

await composer.fill('QA-ERROR')
await composer.press('Enter')
await page.getByText('The assistant failed to respond.', { exact: true }).waitFor()
record('CT-07 failed response recovery', true, 'controlled error rendered', 'clear failure state')
const repliesBeforeRetry = (await page.locator('body').innerText()).split('QA retry succeeded').length - 1
await send('Retry after controlled failure')
const retryBody = await page.locator('body').innerText()
const repliesAfterRetry = retryBody.split('QA retry succeeded').length - 1
record('CT-07 retry after failure',
  repliesAfterRetry > repliesBeforeRetry,
  { repliesBeforeRetry, repliesAfterRetry, tail: retryBody.slice(-1200) },
  'a later turn succeeds after a failed generation',
)

const beforeNodes = await page.locator('body *').count()
for (let i = 1; i <= 30; i += 1) await send(`Stress transcript ${i}`)
const afterNodes = await page.locator('body *').count()
const scroll = await page.evaluate(() => {
  const element = document.querySelector('.overflow-y-auto')
  return element ? { scrollHeight: element.scrollHeight, clientHeight: element.clientHeight } : null
})
record('SM-08 30-turn transcript/resource check',
  Boolean(scroll && scroll.scrollHeight > scroll.clientHeight && afterNodes > beforeNodes),
  { beforeNodes, afterNodes, scroll, websocketCount: sockets.length, pageErrors: errors },
  'larger transcript remains renderable without page errors or unexpected socket multiplication',
)

const cdp = await context.newCDPSession(page)
await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 })
await cdp.send('Network.enable')
await cdp.send('Network.emulateNetworkConditions', {
  offline: false,
  latency: 150,
  downloadThroughput: 750 * 1024,
  uploadThroughput: 250 * 1024,
})
const frames = page.evaluate(async () => {
  const values = []
  const started = performance.now()
  let previous = started
  await new Promise(resolve => {
    const tick = now => {
      values.push(now - previous)
      previous = now
      if (now - started < 2000) requestAnimationFrame(tick)
      else resolve()
    }
    requestAnimationFrame(tick)
  })
  return values
})
const inputStart = await page.evaluate(() => performance.now())
await composer.fill('CPU and network throttled responsiveness')
await composer.press('Enter')
const inputElapsed = await page.evaluate(start => performance.now() - start, inputStart)
await page.getByRole('button', { name: 'Stop', exact: true }).waitFor({ timeout: 30000 })
await page.getByRole('button', { name: 'Stop', exact: true }).waitFor({ state: 'hidden', timeout: 30000 })
const throttledFrames = await frames
record('SM throttled CPU/network responsiveness',
  throttledFrames.filter(value => value > 50).length < 10 && errors.length === 0,
  { estimatedFps: throttledFrames.length / (throttledFrames.reduce((a, b) => a + b, 0) / 1000), worstFrameMs: Math.max(...throttledFrames), framesOver50ms: throttledFrames.filter(value => value > 50).length, inputElapsedMs: inputElapsed, pageErrors: errors },
  'UI remains usable with bounded jank under 4x CPU and throttled network',
)

console.log(JSON.stringify({ results }, null, 2))
await browser.close()
