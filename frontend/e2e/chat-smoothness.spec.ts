import { test, expect, type Page } from '@playwright/test'

async function mockChat(page: Page) {
  await page.route('**/api/auth/me', route => route.fulfill({ json: {
    id: '00000000-0000-0000-0000-000000000001',
    name: 'Smoothness QA',
    email: 'smoothness@example.com',
    created_at: '2026-01-01T00:00:00Z',
  } }))
  await page.route('**/api/chat/conversations', route => route.fulfill({ json: [] }))
  await page.routeWebSocket('**/api/chat/ws', ws => {
    ws.onMessage(raw => {
      const message = JSON.parse(String(raw))
      if (message.type !== 'run') return
      ws.send(JSON.stringify({ type: 'meta', conversation_id: '10000000-0000-0000-0000-000000000001' }))
      const tokens = Array.from({ length: 120 }, (_, index) => `word${index} `)
      let index = 0
      const timer = setInterval(() => {
        if (index >= tokens.length) {
          clearInterval(timer)
          ws.send(JSON.stringify({ type: 'done' }))
          return
        }
        ws.send(JSON.stringify({ type: 'token', content: tokens[index++] }))
      }, 12)
    })
  })
}

test('chat typing and streaming responsiveness baseline', async ({ page }) => {
  await mockChat(page)
  await page.goto('/chat')
  await expect(page.getByText('Live')).toBeVisible()

  await page.evaluate(() => {
    const state = { longTasks: [] as number[], inputLatencies: [], cls: 0 }
    ;(window as Window & { __smoothness?: typeof state }).__smoothness = state
    try {
      new PerformanceObserver(list => {
        for (const entry of list.getEntries()) state.longTasks.push(entry.duration)
      }).observe({ type: 'longtask', buffered: true })
    } catch {}
    try {
      new PerformanceObserver(list => {
        for (const entry of list.getEntries()) {
          if (!('hadRecentInput' in entry) || !(entry as PerformanceEntry & { hadRecentInput: boolean }).hadRecentInput) {
            state.cls += (entry as PerformanceEntry & { value: number }).value
          }
        }
      }).observe({ type: 'layout-shift', buffered: true })
    } catch {}
    document.querySelector('textarea')?.addEventListener('input', () => {
      const start = performance.now()
      requestAnimationFrame(() => state.inputLatencies.push(performance.now() - start))
    })
  })

  const frames = page.evaluate(async () => {
    const samples: number[] = []
    const started = performance.now()
    let previous = started
    await new Promise<void>(resolve => {
      const tick = (now: number) => {
        samples.push(now - previous)
        previous = now
        if (now - started < 3000) requestAnimationFrame(tick)
        else resolve()
      }
      requestAnimationFrame(tick)
    })
    return samples
  })

  const composer = page.getByPlaceholder('Ask about nutrition…')
  await composer.click()
  await page.keyboard.type('Typing responsiveness baseline message.', { delay: 12 })
  await composer.press('Enter')
  await expect(page.getByRole('button', { name: 'Stop' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden({ timeout: 10000 })
  const frameIntervals = await frames
  const observed = await page.evaluate(() => (window as Window & { __smoothness?: {
    longTasks: number[]; inputLatencies: number[]; cls: number
  } }).__smoothness)

  const sorted = [...(observed?.inputLatencies ?? [])].sort((a, b) => a - b)
  const p95InputLatency = sorted[Math.max(0, Math.floor(sorted.length * 0.95) - 1)] ?? 0
  const totalFrameMs = frameIntervals.reduce((sum, value) => sum + value, 0)
  const metrics = {
    estimatedFps: frameIntervals.length / (totalFrameMs / 1000),
    framesOver20ms: frameIntervals.filter(value => value > 20).length,
    framesOver33ms: frameIntervals.filter(value => value > 33).length,
    worstFrameMs: Math.max(...frameIntervals),
    longTaskCount: observed?.longTasks.length ?? 0,
    maxLongTaskMs: Math.max(0, ...(observed?.longTasks ?? [])),
    p95InputLatencyMs: p95InputLatency,
    maxInputLatencyMs: Math.max(0, ...(observed?.inputLatencies ?? [])),
    cls: observed?.cls ?? 0,
  }
  console.log(`SMOOTHNESS_METRICS ${JSON.stringify(metrics)}`)
  expect(metrics.longTaskCount, 'no main-thread long tasks').toBe(0)
  expect(metrics.p95InputLatencyMs, 'typing p95 latency').toBeLessThan(50)
  expect(metrics.framesOver33ms, 'severe frame drops').toBeLessThan(4)
})
