import { expect, test } from '@playwright/test'

test.use({
  permissions: ['microphone'],
  launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
})

test('voice and its socket survive page navigation and stop on sign-out', async ({ page }) => {
  await page.route(/\/api\//,  (route) => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    if (path === '/api/mind/state') return route.fulfill({ status: 503, json: {} })
    const json = path === '/api/auth/me'
      ? { id: 'owner', name: 'Test owner', email: 'test@example.com', is_owner: true }
      : path === '/api/capabilities' ? { voice_enabled: true }
      : path === '/api/system/status' ? { parts: [] }
      : []
    return route.fulfill({ json })
  })
  await page.route('**/audio/jophiel.onnx', (route) => route.fulfill({ status: 404 }))
  await page.addInitScript(() => {
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices)
    const tracks: MediaStreamTrack[] = []
    Object.assign(window, { capturedTracks: tracks })
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      const stream = await original(constraints)
      tracks.push(...stream.getTracks())
      return stream
    }
  })
  let connections = 0
  let closed = 0
  await page.routeWebSocket('**/api/chat/ws', (ws) => {
    connections++
    ws.onClose(() => { closed++ })
  })
  const liveTracks = () => page.evaluate(() =>
    (window as unknown as { capturedTracks: MediaStreamTrack[] }).capturedTracks.filter((t) => t.readyState === 'live').length,
  )
  await page.goto('/chat')
  await page.getByRole('button', { name: 'Start voice', exact: true }).click()
  await expect(page.getByRole('button', { name: 'End voice', exact: true })).toBeVisible()
  await expect.poll(liveTracks).toBe(1)
  const baseline = connections
  const baselineClosed = closed

  await page.getByRole('link', { name: 'System', exact: true }).click()
  const controls = page.getByRole('region', { name: 'Voice conversation' })
  await expect(controls.getByRole('button', { name: 'End voice' })).toBeVisible()
  await expect.poll(liveTracks).toBe(1)
  await controls.getByRole('button', { name: 'Mute microphone', exact: true }).click()
  await expect(controls.getByRole('button', { name: 'Unmute mic', exact: true })).toBeVisible()

  await controls.getByRole('link', { name: 'Jophiel' }).click()
  await expect(page.getByRole('button', { name: 'Unmute microphone', exact: true })).toBeVisible()
  expect(connections).toBe(baseline)
  expect(closed).toBe(baselineClosed)

  await page.getByRole('link', { name: 'Scan Meal', exact: true }).click()
  await expect(controls.getByRole('button', { name: 'End voice' })).toBeVisible()
  await page.getByRole('button', { name: 'Sign out', exact: true }).first().click()
  await expect.poll(liveTracks).toBe(0)
  await expect(controls).toBeHidden()
})
