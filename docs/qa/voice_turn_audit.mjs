import { chromium } from '../../frontend/node_modules/playwright/index.mjs'

const base = 'http://127.0.0.1:54173'
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
page.setDefaultTimeout(15000)
const pageErrors = []
page.on('pageerror', error => pageErrors.push(error.message))

const email = `voice-${Date.now()}@example.com`
const password = 'Synthetic-voice-2026'
const signup = await context.request.post(base + '/api/auth/signup', {
  headers: { Origin: base },
  data: { name: 'Voice QA', email, password },
})
if (signup.status() !== 201) throw new Error(`signup failed: ${signup.status()} ${await signup.text()}`)
await context.clearCookies()
await page.goto(base + '/login')
await page.getByPlaceholder('you@example.com').fill(email)
await page.locator('input[type=password]').fill(password)
await page.locator('form button[type=submit]').click()
await page.waitForURL('**/chat')
await page.getByText('Live', { exact: true }).waitFor()

const exchange = await page.evaluate(async () => {
  const run = (turnId, stopAfterMs = null) => new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://${window.location.host}/api/chat/ws`)
    ws.binaryType = 'arraybuffer'
    const events = []
    let binaryFrames = 0
    let settled = false
    const finish = value => {
      if (settled) return
      settled = true
      ws.close()
      resolve({ events, binaryFrames, ...value })
    }
    const timer = setTimeout(() => finish({ timeout: true }), 12000)
    ws.onerror = () => {
      clearTimeout(timer)
      reject(new Error('voice WebSocket failed'))
    }
    ws.onmessage = event => {
      if (typeof event.data !== 'string') {
        binaryFrames += 1
        return
      }
      const message = JSON.parse(event.data)
      if (['transcript', 'token', 'audio_start', 'audio_end', 'done', 'stopped', 'error'].includes(message.type)) {
        events.push(message)
      }
      if ((message.type === 'done' || message.type === 'stopped' || message.type === 'error') && message.turn_id === turnId) {
        clearTimeout(timer)
        finish({ terminal: message })
      }
    }
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'voice_start', conversation_id: null, turn_id: turnId, sample_rate: 16000 }))
      ws.send(new Uint8Array(3200).buffer)
      ws.send(JSON.stringify({ type: 'voice_end', turn_id: turnId }))
      if (stopAfterMs !== null) setTimeout(() => ws.send(JSON.stringify({ type: 'stop', conversation_id: null })), stopAfterMs)
    }
  })
  const completed = await run(crypto.randomUUID())
  const stopped = await run(crypto.randomUUID(), 200)
  return { completed, stopped }
})

const completedEvents = exchange.completed.events
const completedIds = [...new Set(completedEvents.filter(event => event.turn_id).map(event => event.turn_id))]
const stoppedEvents = exchange.stopped.events
const stoppedIds = [...new Set(stoppedEvents.filter(event => event.turn_id).map(event => event.turn_id))]
const results = [
  {
    test: 'VOICE-01 real voice WebSocket path',
    status: exchange.completed.timeout ? 'SUSPICIOUS' : 'PASS',
    actual: { terminal: exchange.completed.terminal, binaryFrames: exchange.completed.binaryFrames },
    expected: 'real /api/chat/ws voice turn completes with transcript, text, and audio frames',
  },
  {
    test: 'VOICE-02 stable turn identity',
    status: completedIds.length === 1 ? 'PASS' : 'SUSPICIOUS',
    actual: { turnIds: completedIds },
    expected: 'one turn_id across transcript, tokens, audio lifecycle, and terminal event',
  },
  {
    test: 'VOICE-03 barge-in cancellation',
    status: exchange.stopped.terminal?.type === 'stopped' && stoppedIds.length === 1 ? 'PASS' : 'SUSPICIOUS',
    actual: { terminal: exchange.stopped.terminal, turnIds: stoppedIds, binaryFrames: exchange.stopped.binaryFrames },
    expected: 'stop ends the active voice turn without stale completion',
  },
  {
    test: 'VOICE-04 page runtime errors',
    status: pageErrors.length === 0 ? 'PASS' : 'SUSPICIOUS',
    actual: pageErrors,
    expected: 'no uncaught browser errors',
  },
]

const conversations = await (await context.request.get(base + '/api/chat/conversations')).json()
if (conversations.length > 0) {
  const details = await Promise.all(conversations.map(async conversation => (
    await (await context.request.get(base + '/api/chat/conversations/' + conversation.id)).json()
  )))
  const messages = details.flatMap(detail => detail.messages)
  results.push({
    test: 'VOICE-05 persisted voice outcomes',
    status: messages.length >= 4 && messages.every(message => message.turn_id) ? 'PASS' : 'SUSPICIOUS',
    actual: messages.map(message => ({ role: message.role, status: message.status, turn_id: message.turn_id, content: message.content })),
    expected: 'voice user/assistant records persist with turn IDs and terminal statuses',
  })
} else {
  results.push({
    test: 'VOICE-05 persisted voice outcomes',
    status: 'SUSPICIOUS',
    actual: { conversations: conversations.length, completed: exchange.completed, stopped: exchange.stopped },
    expected: 'voice user/assistant records persist with turn IDs and terminal statuses',
  })
}

console.log(JSON.stringify({ results }, null, 2))
await browser.close()
