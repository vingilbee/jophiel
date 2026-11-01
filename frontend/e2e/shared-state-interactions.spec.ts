import { test, expect, type Page, type WebSocketRoute } from '@playwright/test'

const A = '10000000-0000-0000-0000-000000000001'
const B = '10000000-0000-0000-0000-000000000002'
async function setup(page: Page, onSocket: (ws: WebSocketRoute) => void) {
  // Browser transport probes only: backend/model behavior is tested separately against Postgres.
  await page.route(/^http:\/\/127\.0\.0\.1:4173\/api\//, route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/auth/me') return route.fulfill({ json: { id: '00000000-0000-0000-0000-000000000001', name: 'Step Six QA', email: 'step6@example.com', created_at: '2026-10-22T00:00:00Z' } })
    if (path === '/api/capabilities') return route.fulfill({ json: { voice_enabled: false, desktop_enabled: false } })
    if (path === '/api/mind/state') return route.fulfill({ json: {} })
    return route.fulfill({ json: [] })
  })
  await page.routeWebSocket('**/api/chat/ws*', onSocket)
  await page.goto('/chat')
  await expect(page.getByRole('textbox', { name: 'Message Columbina', exact: true })).toBeVisible()
}
function nudge(ws: WebSocketRoute, conversation: string, text: string) {
  ws.send(JSON.stringify({ type: 'nudge_start', conversation_id: conversation, reason: 'follow_up' }))
  ws.send(JSON.stringify({ type: 'nudge_token', content: text }))
  ws.send(JSON.stringify({ type: 'nudge_end' }))
}

test('autonomous text does not settle or mix into an active normal reply', async ({ page }) => {
  let socket: WebSocketRoute | undefined
  let turn: string | undefined
  await setup(page, ws => {
    socket = ws
    ws.onMessage(raw => {
      const frame = JSON.parse(String(raw))
      if (frame.type !== 'run') return
      turn = frame.turn_id
      ws.send(JSON.stringify({ type: 'meta', conversation_id: A }))
      ws.send(JSON.stringify({ type: 'token', turn_id: turn, content: 'Normal start. ' }))
    })
  })
  await page.getByRole('textbox', { name: 'Message Columbina', exact: true }).fill('Hello')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(page.getByText('Normal start.', { exact: true })).toBeVisible()
  nudge(socket!, A, 'Autonomous proposal.')
  await expect(page.getByText('Autonomous proposal.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Stop', exact: true })).toBeVisible()
  socket!.send(JSON.stringify({ type: 'token', turn_id: turn, content: 'Normal end.' }))
  socket!.send(JSON.stringify({ type: 'done', turn_id: turn }))
  await expect(page.getByText('Normal start. Normal end.', { exact: true })).toBeVisible()
  await expect(page.getByText('Autonomous proposal.', { exact: true })).toHaveCount(1)
})

test('a late autonomous reply for another conversation cannot retarget the composer', async ({ page }) => {
  let socket: WebSocketRoute | undefined
  const runs: { conversation_id: string | null; turn_id: string }[] = []
  await setup(page, ws => {
    socket = ws
    ws.onMessage(raw => {
      const frame = JSON.parse(String(raw))
      if (frame.type !== 'run') return
      runs.push(frame)
      ws.send(JSON.stringify({ type: 'meta', conversation_id: runs.length === 1 ? A : B }))
      ws.send(JSON.stringify({ type: 'token', turn_id: frame.turn_id, content: `Answer ${runs.length}` }))
      ws.send(JSON.stringify({ type: 'done', turn_id: frame.turn_id }))
    })
  })
  const composer = page.getByRole('textbox', { name: 'Message Columbina', exact: true })
  await composer.fill('Conversation A')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(page.getByText('Answer 1', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'New chat', exact: true }).click()
  await composer.fill('Conversation B')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(page.getByText('Answer 2', { exact: true })).toBeVisible()
  nudge(socket!, A, 'Late proposal for A.')
  await expect(page.getByText('Late proposal for A.', { exact: true })).toHaveCount(0)
  await composer.fill('Stay in B')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await expect.poll(() => runs.length).toBe(3)
  expect(runs[2].conversation_id).toBe(B)
})

test('a socket restart can resume an active reply from server replay', async ({ page }) => {
  let socket: WebSocketRoute | undefined
  let turn = ''
  let connections = 0
  await setup(page, ws => {
    socket = ws
    connections++
    if (connections > 1) {
      ws.send(JSON.stringify({ type: 'history', conversation_id: A, messages: [{ id: 'user1', role: 'user', content: 'Please answer', turn_id: turn, status: 'completed' }] }))
      ws.send(JSON.stringify({ type: 'meta', conversation_id: A }))
      ws.send(JSON.stringify({ type: 'token', turn_id: turn, content: 'Recovered full answer.' }))
      ws.send(JSON.stringify({ type: 'done', turn_id: turn, status: 'completed' }))
    }
    ws.onMessage(raw => {
      const frame = JSON.parse(String(raw))
      if (frame.type !== 'run') return
      turn = frame.turn_id
      ws.send(JSON.stringify({ type: 'meta', conversation_id: A }))
      ws.send(JSON.stringify({ type: 'token', turn_id: turn, content: 'Partial answer' }))
    })
  })
  await page.getByRole('textbox', { name: 'Message Columbina', exact: true }).fill('Please answer')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(page.getByText('Partial answer', { exact: true })).toBeVisible()
  socket!.close({ code: 1012, reason: 'Restart test' })
  await expect.poll(() => connections).toBeGreaterThan(1)
  await expect(page.getByText('Recovered full answer.', { exact: true })).toBeVisible()
})

test('announcement retries with the same message ID create one bubble', async ({ page }) => {
  let socket: WebSocketRoute | undefined
  await setup(page, ws => {
    socket = ws
    ws.onMessage(raw => {
      const frame = JSON.parse(String(raw))
      if (frame.type !== 'run') return
      ws.send(JSON.stringify({ type: 'meta', conversation_id: A }))
      ws.send(JSON.stringify({ type: 'token', turn_id: frame.turn_id, content: 'Normal answer' }))
      ws.send(JSON.stringify({ type: 'done', turn_id: frame.turn_id }))
    })
  })
  await page.getByRole('textbox', { name: 'Message Columbina', exact: true }).fill('Hello')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(page.getByText('Normal answer', { exact: true })).toBeVisible()
  for (let i = 0; i < 2; i++) {
    socket!.send(JSON.stringify({ type: 'nudge_start', conversation_id: A, message_id: 'same-receipt', reason: 'follow_up' }))
    socket!.send(JSON.stringify({ type: 'nudge_token', content: 'Delivered once.' }))
    socket!.send(JSON.stringify({ type: 'nudge_end' }))
  }
  await expect(page.getByText('Delivered once.', { exact: true })).toHaveCount(1)
  await expect(page.getByText('Normal answer', { exact: true })).toBeVisible()
})

test('a reply completed offline settles from history without replay or resending', async ({ page }) => {
  let socket: WebSocketRoute | undefined
  let turn = ''
  let runs = 0
  let connections = 0
  await setup(page, ws => {
    socket = ws
    connections++
    if (connections > 1) {
      ws.send(JSON.stringify({ type: 'history', conversation_id: A, messages: [
        { id: 'u1', role: 'user', content: 'Please answer', turn_id: turn, status: 'completed' },
        { id: 'a1', role: 'assistant', content: 'Finished offline.', turn_id: turn, status: 'completed' },
      ] }))
    }
    ws.onMessage(raw => {
      const frame = JSON.parse(String(raw))
      if (frame.type !== 'run') return
      runs++
      turn = frame.turn_id
      ws.send(JSON.stringify({ type: 'meta', conversation_id: A }))
      ws.send(JSON.stringify({ type: 'token', turn_id: turn, content: 'Partial' }))
    })
  })
  await page.getByRole('textbox', { name: 'Message Columbina', exact: true }).fill('Please answer')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(page.getByText('Partial', { exact: true })).toBeVisible()
  socket!.close({ code: 1012 })
  await expect(page.getByText('Finished offline.', { exact: true })).toHaveCount(1)
  await expect(page.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0)
  expect(runs).toBe(1)
})
