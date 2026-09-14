import { chromium } from '../../frontend/node_modules/playwright/index.mjs'

const base = 'http://127.0.0.1:54173'
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
page.setDefaultTimeout(15000)
const errors = []
page.on('pageerror', error => errors.push(error.message))
page.on('console', message => console.error('browser', message.type(), message.text()))
page.on('websocket', socket => {
  console.error('websocket', socket.url())
  socket.on('close', () => console.error('websocket closed'))
})

const email = `smoothness-${Date.now()}@example.com`
const password = 'Synthetic-smoothness-2026'
const signup = await context.request.post(base + '/api/auth/signup', { headers: { Origin: base }, data: { name: 'Smoothness QA', email, password } })
console.error('signup', signup.status(), await signup.text())
await context.clearCookies()
await page.goto(base + '/login')
await page.getByPlaceholder('you@example.com').fill(email)
await page.locator('input[type=password]').fill(password)
const loginResponse = page.waitForResponse(response => response.url().includes('/api/auth/signin'))
await page.locator('form button[type=submit]').click()
const login = await loginResponse
console.error('login', login.status(), await login.text())
await page.waitForURL('**/chat')
try {
  await page.getByText('Live', { exact: true }).waitFor({ timeout: 5000 })
} catch {
  console.error('chat body', (await page.locator('body').innerText()).slice(0, 2000))
  throw new Error('real backend WebSocket did not reach Live state')
}

await page.evaluate(() => {
  const state = { longTasks: [], inputLatencies: [], frames: [], cls: 0 }
  window.__smoothness = state
  try {
    new PerformanceObserver(list => {
      for (const entry of list.getEntries()) state.longTasks.push(entry.duration)
    }).observe({ type: 'longtask', buffered: true })
  } catch {}
  try {
    new PerformanceObserver(list => {
      for (const entry of list.getEntries()) if (!entry.hadRecentInput) state.cls += entry.value
    }).observe({ type: 'layout-shift', buffered: true })
  } catch {}
  const composer = document.querySelector('textarea')
  composer?.addEventListener('input', () => {
    const started = performance.now()
    requestAnimationFrame(() => state.inputLatencies.push(performance.now() - started))
  })
})

const frameProbe = page.evaluate(async () => {
  const frames = []
  const start = performance.now()
  let previous = start
  await new Promise(resolve => {
    const tick = now => {
      frames.push(now - previous)
      previous = now
      if (now - start < 3000) requestAnimationFrame(tick)
      else resolve()
    }
    requestAnimationFrame(tick)
  })
  return frames
})

const composer = page.getByPlaceholder('Ask about nutrition…')
await composer.click()
await page.keyboard.type('Typing responsiveness baseline message.', { delay: 12 })
await composer.press('Enter')
await page.getByRole('button', { name: 'Stop', exact: true }).waitFor()
await page.getByText('This is a deterministic QA reply for testing streaming and persistence.', { exact: true }).waitFor()
const frameIntervals = await frameProbe

const metrics = await page.evaluate(() => {
  const state = window.__smoothness || { longTasks: [], inputLatencies: [], cls: 0 }
  const sorted = values => [...values].sort((a, b) => a - b)
  const percentile = (values, p) => {
    const ordered = sorted(values)
    return ordered.length ? ordered[Math.min(ordered.length - 1, Math.floor(ordered.length * p))] : 0
  }
  const frames = window.__smoothnessFrames || []
  return {
    longTaskCount: state.longTasks.length,
    maxLongTaskMs: Math.max(0, ...state.longTasks),
    p95InputLatencyMs: percentile(state.inputLatencies, 0.95),
    maxInputLatencyMs: Math.max(0, ...state.inputLatencies),
    cls: state.cls,
    documentHeight: document.documentElement.scrollHeight,
    viewportHeight: innerHeight,
    framesCaptured: frames.length,
  }
})

const frameSummary = (() => {
  const delayed = frameIntervals.filter(interval => interval > 20)
  const severe = frameIntervals.filter(interval => interval > 33)
  return {
    samples: frameIntervals.length,
    estimatedFps: frameIntervals.length / (frameIntervals.reduce((a, b) => a + b, 0) / 1000),
    framesOver20ms: delayed.length,
    framesOver33ms: severe.length,
    worstFrameMs: Math.max(...frameIntervals),
  }
})()

console.log(JSON.stringify({ metrics, frameSummary, errors }, null, 2))
await browser.close()
