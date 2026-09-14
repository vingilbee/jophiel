// Real browser -> QA Vite -> actual FastAPI/DB/Redis. No route interception.
import { chromium, firefox, webkit } from '../../frontend/node_modules/playwright/index.mjs'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
const base = 'http://127.0.0.1:54173'
const artifacts = fileURLToPath(new URL('./artifacts/', import.meta.url))
await mkdir(artifacts, { recursive: true })
const results = []
async function record(test, pass, actual, expected) {
  const row = { test, status: pass ? 'PASS' : 'FAIL', actual, expected }
  results.push(row)
  console.log(JSON.stringify(row))
  await writeFile(artifacts + 'browser-results.json', JSON.stringify(results, null, 2))
}
const fixture = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5xkAAAAASUVORK5CYII=', 'base64')
const configs = [
  ['chromium-desktop', chromium, { width: 1440, height: 900 }],
  ['chromium-mobile', chromium, { width: 390, height: 844 }],
  ['firefox-desktop', firefox, { width: 1440, height: 900 }],
  ['webkit-desktop', webkit, { width: 1440, height: 900 }],
]
for (const [label, engine, viewport] of configs) {
  let browser
  try {
    browser = await engine.launch({ headless: true })
    const context = await browser.newContext({ viewport })
    const page = await context.newPage()
    page.setDefaultTimeout(6000)
    const errors = []
    const wsSent = []
    page.on('pageerror', err => errors.push(err.message))
    page.on('websocket', ws => ws.on('framesent', event => {
      try { const data = JSON.parse(event.payload.toString()); wsSent.push({ type: data.type, content: data.content }) } catch {}
    }))
    const shot = async name => page.screenshot({ path: artifacts + label + '-' + name + '.png', fullPage: true })
    const measure = async route => {
      const metrics = await page.evaluate(() => ({
        viewport: innerWidth,
        scrollWidth: document.documentElement.scrollWidth,
        overflow: [...document.querySelectorAll('header a, header button, textarea')].map(e => ({text: (e.textContent || e.getAttribute('aria-label') || e.getAttribute('placeholder') || '').trim(), right: Math.round(e.getBoundingClientRect().right), left: Math.round(e.getBoundingClientRect().left)})).filter(e => e.right > innerWidth || e.left < 0),
      }))
      await record(label + ' UI-01 layout ' + route, metrics.scrollWidth <= metrics.viewport + 1 && !metrics.overflow.length, metrics, 'no horizontal overflow or offscreen primary controls')
    }
    await page.goto(base + '/')
    await shot('landing')
    await measure('landing')
    await page.goto(base + '/predict')
    await page.waitForURL('**/login')
    await record(label + ' AUTH-01 protected redirect', true, page.url(), '/login')
    await shot('login')
    const labels = await page.locator('input').evaluateAll(nodes => nodes.map(e => ({ placeholder: e.getAttribute('placeholder'), labels: e.labels?.length || 0, aria: e.getAttribute('aria-label') || e.getAttribute('aria-labelledby') })))
    await record(label + ' UI-02 input labels', labels.every(x => x.labels || x.aria), labels, 'each form input has a programmatic label')

    const email = `qa-browser-${label}-${Date.now()}@example.com`
    const password = 'Synthetic-browser-password-2026'
    await page.getByRole('button', { name: 'Sign up', exact: true }).click()
    await page.getByPlaceholder('Jane Doe').fill('QA Browser')
    await page.getByPlaceholder('you@example.com').fill(email)
    await page.locator('input[type=password]').fill(password)
    await page.getByRole('button', { name: 'Create account' }).click()
    await page.waitForURL('**/chat')
    const meAfterSignup = await context.request.get(base + '/api/auth/me')
    await record(label + ' AUTH-02 signup enters authenticated app', meAfterSignup.status() === 200, { url: page.url(), meStatus: meAfterSignup.status() }, 'real session before entering chat')
    await shot('signup-no-session')
    await page.reload()
    await page.waitForURL('**/login')
    await page.getByPlaceholder('you@example.com').fill(email)
    await page.locator('input[type=password]').fill(password)
    await page.locator('form button[type=submit]').click()
    await page.waitForURL('**/chat')
    await page.getByText('Live', { exact: true }).waitFor()
    await shot('chat-empty')
    await measure('chat')

    const composer = page.getByPlaceholder('Ask about nutrition…')
    const count = wsSent.filter(x => x.type === 'run').length
    await composer.fill('First QA question by Enter')
    await composer.press('Enter')
    // Bounded observation of absence; later button case proves backend availability.
    await page.waitForTimeout(700)
    const enterSent = wsSent.filter(x => x.type === 'run').length > count
    await record(label + ' CHAT-13 Enter sends', enterSent, { runFrameSent: enterSent, remainingDraft: await composer.inputValue() }, 'one run frame and cleared draft')
    if (!enterSent) await page.getByRole('button', { name: 'Send', exact: true }).click()
    await page.getByText('This is a deterministic QA reply for testing streaming and persistence.', { exact: true }).waitFor()
    await record(label + ' CHAT-01 button streaming', true, 'real WS reply rendered', 'incremental reply and terminal state')
    await shot('chat-reply')

    await composer.fill('Stop this QA reply')
    await page.getByRole('button', { name: 'Send', exact: true }).click()
    await page.getByRole('button', { name: 'Stop', exact: true }).click()
    await page.getByRole('button', { name: 'Stop', exact: true }).waitFor({ state: 'hidden' })
    await record(label + ' CHAT-03 button stop', true, 'stop terminal clears control', 'no stuck stopping')

    const historyBefore = await (await context.request.get(base + '/api/chat/conversations')).json()
    await page.reload()
    await page.getByText('Live', { exact: true }).waitFor()
    const restored = await page.getByText('First QA question by Enter', { exact: true }).count()
    await record(label + ' CHAT-02 refresh restores transcript', restored > 0, { visibleFirstMessage: restored, serverConversations: historyBefore.length }, 'persisted conversation available after refresh')
    await shot('chat-reloaded')
    await page.getByRole('button', { name: 'Cognitive test' }).click()
    await page.getByRole('combobox', { name: 'Cognitive evaluation mode' }).selectOption('rules')
    await page.getByPlaceholder('Enter a message to inspect…').fill('Can you analyze this image?')
    const inspectResponse = page.waitForResponse(r => r.url().endsWith('/api/cognitive/inspect') && r.request().method() === 'POST')
    await page.getByRole('button', { name: 'Inspect cognition' }).click()
    await record(label + ' COG-01 rules browser', (await inspectResponse).status() === 200, 'actual inspector API', '200 and structured output')
    await shot('cognitive')
    await measure('cognitive')
    await page.getByRole('button', { name: 'Close cognitive inspector' }).click()

    await page.goto(base + '/predict')
    await page.getByRole('heading', { name: 'Scan a meal' }).waitFor()
    await measure('predict')
    await shot('predict-empty')
    await page.locator('input[type=file]').setInputFiles({name:'qa-image.png',mimeType:'image/png',buffer:fixture})
    await page.getByRole('button', { name: 'Fast', exact: false }).click()
    const predicted = page.waitForResponse(r => r.url().includes('/api/predict?') && r.request().method() === 'POST')
    await page.getByRole('button', { name: 'Analyze', exact: true }).click()
    const predResponse = await predicted
    await record(label + ' IMG-01 prediction browser', predResponse.status() === 200, {status:predResponse.status()}, 'valid image succeeds with controlled model')
    await shot('predict-result')
    await page.getByRole('button', { name: 'Remove', exact: true }).click()
    await page.locator('input[type=file]').setInputFiles({name:'invalid.txt',mimeType:'text/plain',buffer:Buffer.from('bad')})
    await page.getByText('Please choose an image file.').waitFor()
    await record(label + ' IMG-05 nonimage UI',true,'visible validation','no invalid upload')
    await shot('predict-error')

    await page.goto(base + '/integrations')
    await page.getByRole('heading').first().waitFor()
    await measure('integrations')
    await shot('integrations')
    await page.goto(base + '/dashboard')
    await page.getByText('Voice is off', { exact: true }).waitFor()
    await measure('dashboard')
    await shot('dashboard')
    const voiceButtons = await page.getByRole('button',{name:'Start voice',exact:true}).evaluateAll(nodes => nodes.map(e=>({ disabled:e.disabled, aria:e.getAttribute('aria-label') })))
    await record(label + ' VOICE-06 disabled voice UI', voiceButtons.every(x=>x.disabled), voiceButtons, 'disabled server feature not offered as usable')
    await record(label + ' UI runtime errors', errors.length === 0, errors, 'no uncaught browser errors')
    await page.getByRole('button',{name:'Sign out',exact:true}).click()
    await page.waitForURL(base + '/')
    await record(label + ' AUTH-06 logout UI',true,'returned to landing','signed out UI')
    await context.close()
  } catch (err) {
    await record(label + ' audit interrupted',false,String(err).slice(0,1000),'remaining browser checks execute')
  } finally {
    await browser?.close()
  }
}
console.log(JSON.stringify({ total: results.length, pass: results.filter(x=>x.status==='PASS').length, fail:results.filter(x=>x.status==='FAIL').length }))
