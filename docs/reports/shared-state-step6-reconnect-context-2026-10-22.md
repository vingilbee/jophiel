# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: shared-state-interactions.spec.ts >> a socket restart can resume an active reply from server replay
- Location: e2e/shared-state-interactions.spec.ts:78:1

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText('Recovered full answer.', { exact: true })
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByText('Recovered full answer.', { exact: true })

```

```yaml
- complementary:
  - link "✦ Columbina":
    - /url: /dashboard
  - paragraph: Your nutrition companion
  - navigation "Main navigation":
    - link "Chat":
      - /url: /chat
    - link "Scan Meal":
      - /url: /predict
    - link "Integrations":
      - /url: /integrations
  - paragraph: Profile
  - img "Vincent profile"
  - paragraph: Vincent
  - button "Cognitive test"
  - button "New chat"
- main:
  - heading "Columbina" [level=1]
  - paragraph: Your quiet companion · chat, talk, or play
  - text: here with you Please answer
  - paragraph: Connection closed.
  - 'button "Eyes: Closed"'
  - 'button "Ears: Closed"'
  - button "Attach an image or file":
    - img
  - textbox "Message Columbina":
    - /placeholder: Message Columbina…
  - button "Start voice" [disabled]: Voice
  - button "Send" [disabled]:
    - img
  - paragraph: Enter to send · Shift+Enter for newline · Attach images, documents, or 3D assets
```

# Test source

```ts
  4   | const B = '10000000-0000-0000-0000-000000000002'
  5   | async function setup(page: Page, onSocket: (ws: WebSocketRoute) => void) {
  6   |   // Browser transport probes only: backend/model behavior is tested separately against Postgres.
  7   |   await page.route(/^http:\/\/127\.0\.0\.1:4173\/api\//, route => {
  8   |     const path = new URL(route.request().url()).pathname
  9   |     if (path === '/api/auth/me') return route.fulfill({ json: { id: '00000000-0000-0000-0000-000000000001', name: 'Step Six QA', email: 'step6@example.com', created_at: '2026-10-22T00:00:00Z' } })
  10  |     if (path === '/api/capabilities') return route.fulfill({ json: { voice_enabled: false, desktop_enabled: false } })
  11  |     if (path === '/api/mind/state') return route.fulfill({ json: {} })
  12  |     return route.fulfill({ json: [] })
  13  |   })
  14  |   await page.routeWebSocket('**/api/chat/ws', onSocket)
  15  |   await page.goto('/chat')
  16  |   await expect(page.getByRole('textbox', { name: 'Message Columbina', exact: true })).toBeVisible()
  17  | }
  18  | function nudge(ws: WebSocketRoute, conversation: string, text: string) {
  19  |   ws.send(JSON.stringify({ type: 'nudge_start', conversation_id: conversation, reason: 'follow_up' }))
  20  |   ws.send(JSON.stringify({ type: 'nudge_token', content: text }))
  21  |   ws.send(JSON.stringify({ type: 'nudge_end' }))
  22  | }
  23  | 
  24  | test('autonomous text does not settle or mix into an active normal reply', async ({ page }) => {
  25  |   let socket: WebSocketRoute | undefined
  26  |   let turn: string | undefined
  27  |   await setup(page, ws => {
  28  |     socket = ws
  29  |     ws.onMessage(raw => {
  30  |       const frame = JSON.parse(String(raw))
  31  |       if (frame.type !== 'run') return
  32  |       turn = frame.turn_id
  33  |       ws.send(JSON.stringify({ type: 'meta', conversation_id: A }))
  34  |       ws.send(JSON.stringify({ type: 'token', turn_id: turn, content: 'Normal start. ' }))
  35  |     })
  36  |   })
  37  |   await page.getByRole('textbox', { name: 'Message Columbina', exact: true }).fill('Hello')
  38  |   await page.getByRole('button', { name: 'Send', exact: true }).click()
  39  |   await expect(page.getByText('Normal start.', { exact: true })).toBeVisible()
  40  |   nudge(socket!, A, 'Autonomous proposal.')
  41  |   await expect(page.getByText('Autonomous proposal.', { exact: true })).toBeVisible()
  42  |   await expect(page.getByRole('button', { name: 'Stop', exact: true })).toBeVisible()
  43  |   socket!.send(JSON.stringify({ type: 'token', turn_id: turn, content: 'Normal end.' }))
  44  |   socket!.send(JSON.stringify({ type: 'done', turn_id: turn }))
  45  |   await expect(page.getByText('Normal start. Normal end.', { exact: true })).toBeVisible()
  46  |   await expect(page.getByText('Autonomous proposal.', { exact: true })).toHaveCount(1)
  47  | })
  48  | 
  49  | test('a late autonomous reply for another conversation cannot retarget the composer', async ({ page }) => {
  50  |   let socket: WebSocketRoute | undefined
  51  |   const runs: { conversation_id: string | null; turn_id: string }[] = []
  52  |   await setup(page, ws => {
  53  |     socket = ws
  54  |     ws.onMessage(raw => {
  55  |       const frame = JSON.parse(String(raw))
  56  |       if (frame.type !== 'run') return
  57  |       runs.push(frame)
  58  |       ws.send(JSON.stringify({ type: 'meta', conversation_id: runs.length === 1 ? A : B }))
  59  |       ws.send(JSON.stringify({ type: 'token', turn_id: frame.turn_id, content: `Answer ${runs.length}` }))
  60  |       ws.send(JSON.stringify({ type: 'done', turn_id: frame.turn_id }))
  61  |     })
  62  |   })
  63  |   const composer = page.getByRole('textbox', { name: 'Message Columbina', exact: true })
  64  |   await composer.fill('Conversation A')
  65  |   await page.getByRole('button', { name: 'Send', exact: true }).click()
  66  |   await expect(page.getByText('Answer 1', { exact: true })).toBeVisible()
  67  |   await page.getByRole('button', { name: 'New chat', exact: true }).click()
  68  |   await composer.fill('Conversation B')
  69  |   await page.getByRole('button', { name: 'Send', exact: true }).click()
  70  |   await expect(page.getByText('Answer 2', { exact: true })).toBeVisible()
  71  |   nudge(socket!, A, 'Late proposal for A.')
  72  |   await composer.fill('Stay in B')
  73  |   await page.getByRole('button', { name: 'Send', exact: true }).click()
  74  |   await expect.poll(() => runs.length).toBe(3)
  75  |   expect(runs[2].conversation_id).toBe(B)
  76  | })
  77  | 
  78  | test('a socket restart can resume an active reply from server replay', async ({ page }) => {
  79  |   let socket: WebSocketRoute | undefined
  80  |   let turn = ''
  81  |   let connections = 0
  82  |   await setup(page, ws => {
  83  |     socket = ws
  84  |     connections++
  85  |     if (connections > 1) {
  86  |       ws.send(JSON.stringify({ type: 'history', conversation_id: A, messages: [{ id: 'user1', role: 'user', content: 'Please answer', turn_id: turn, status: 'completed' }] }))
  87  |       ws.send(JSON.stringify({ type: 'meta', conversation_id: A }))
  88  |       ws.send(JSON.stringify({ type: 'token', turn_id: turn, content: 'Recovered full answer.' }))
  89  |       ws.send(JSON.stringify({ type: 'done', turn_id: turn, status: 'completed' }))
  90  |     }
  91  |     ws.onMessage(raw => {
  92  |       const frame = JSON.parse(String(raw))
  93  |       if (frame.type !== 'run') return
  94  |       turn = frame.turn_id
  95  |       ws.send(JSON.stringify({ type: 'meta', conversation_id: A }))
  96  |       ws.send(JSON.stringify({ type: 'token', turn_id: turn, content: 'Partial answer' }))
  97  |     })
  98  |   })
  99  |   await page.getByRole('textbox', { name: 'Message Columbina', exact: true }).fill('Please answer')
  100 |   await page.getByRole('button', { name: 'Send', exact: true }).click()
  101 |   await expect(page.getByText('Partial answer', { exact: true })).toBeVisible()
  102 |   socket!.close({ code: 1012, reason: 'Restart test' })
  103 |   await expect.poll(() => connections).toBeGreaterThan(1)
> 104 |   await expect(page.getByText('Recovered full answer.', { exact: true })).toBeVisible()
      |                                                                           ^ Error: expect(locator).toBeVisible() failed
  105 | })
  106 | 
```