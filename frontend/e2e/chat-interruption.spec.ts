import { expect, test, type Page, type WebSocketRoute } from '@playwright/test'

async function mockAuthenticatedChat(page: Page) {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: {
        id: '00000000-0000-0000-0000-000000000001',
        name: 'Browser Test',
        email: 'test@example.com',
        created_at: '2026-01-01T00:00:00Z',
      },
    }),
  )
  await page.route('**/api/chat/conversations', (route) =>
    route.fulfill({ json: [] }),
  )
}

test('a follow-up appears immediately and interrupts the active turn', async ({ page }) => {
  await mockAuthenticatedChat(page)

  let socket: WebSocketRoute | undefined
  let socketReady: (() => void) | undefined
  const socketConnected = new Promise<void>((resolve) => { socketReady = resolve })
  let runCount = 0
  await page.routeWebSocket('**/api/chat/ws', (ws) => {
    socket = ws
    socketReady?.()
    ws.onMessage((raw) => {
      if (typeof raw !== 'string') return
      const message = JSON.parse(raw)
      if (message.type === 'run') {
        runCount += 1
        ws.send(JSON.stringify({ type: 'meta', conversation_id: '10000000-0000-0000-0000-000000000001' }))
        if (runCount === 1) {
          ws.send(JSON.stringify({ type: 'token', content: 'First partial answer' }))
        } else {
          ws.send(JSON.stringify({ type: 'token', content: 'Second answer' }))
          ws.send(JSON.stringify({ type: 'done' }))
        }
      }
      // Deliberately hold `stopped`: the test verifies optimistic rendering
      // before the backend has acknowledged the interruption.
    })
  })

  await page.goto('/chat')
  await expect(page.getByText('Live')).toBeVisible()
  await socketConnected

  const composer = page.getByPlaceholder('Ask about nutrition…')
  await composer.fill('First question')
  await composer.press('Enter')
  await expect(page.getByText('First partial answer')).toBeVisible()

  await composer.fill('Actually, answer this instead')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText('Actually, answer this instead')).toBeVisible()
  expect(runCount).toBe(1)

  socket!.send(JSON.stringify({ type: 'stopped' }))
  await expect(page.getByText('Second answer')).toBeVisible()
  expect(runCount).toBe(2)
})

test('Stop enters stopping state and clears when backend acknowledges', async ({ page }) => {
  await mockAuthenticatedChat(page)

  let socket: WebSocketRoute | undefined
  let socketReady: (() => void) | undefined
  const socketConnected = new Promise<void>((resolve) => { socketReady = resolve })
  await page.routeWebSocket('**/api/chat/ws', (ws) => {
    socket = ws
    socketReady?.()
    ws.onMessage((raw) => {
      if (typeof raw !== 'string') return
      const message = JSON.parse(raw)
      if (message.type === 'run') {
        ws.send(JSON.stringify({ type: 'meta', conversation_id: '10000000-0000-0000-0000-000000000001' }))
        ws.send(JSON.stringify({ type: 'token', content: 'Still generating' }))
      }
    })
  })

  await page.goto('/chat')
  await expect(page.getByText('Live')).toBeVisible()
  await socketConnected
  const composer = page.getByPlaceholder('Ask about nutrition…')
  await composer.fill('Long question')
  await composer.press('Enter')

  await expect(page.getByText('Still generating')).toBeVisible()
  await page.getByRole('button', { name: 'Stop' }).click()
  await expect(page.getByRole('button', { name: 'Stopping' })).toBeVisible()
  socket!.send(JSON.stringify({ type: 'stopped' }))
  await expect(page.getByRole('button', { name: 'Stopping' })).toBeHidden()
  await expect(page.getByText('Still generating')).toBeVisible()
})
