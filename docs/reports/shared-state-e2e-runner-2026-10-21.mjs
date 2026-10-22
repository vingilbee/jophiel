// Explicit live test: requires local API, chat worker and frontend. No mocks.
import { chromium } from '../../frontend/node_modules/@playwright/test/index.mjs';
import { writeFile } from 'node:fs/promises';
import { randomBytes } from 'node:crypto';
const root = new URL('./', import.meta.url);
const cookiePath = process.env.E2E_COOKIE_PATH || '/tmp/shared-state-e2e-cookies.json';
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const evidence = { started: new Date().toISOString(), mode: process.env.E2E_MODE || 'normal', turns: [], errors: [] };
let current;
page.on('pageerror', e => evidence.errors.push(String(e)));
page.on('websocket', ws => {
  ws.on('framesent', ({ payload }) => {
    const frame = JSON.parse(String(payload));
    if (frame.type === 'run' && current) current.sent = frame;
  });
  ws.on('framereceived', ({ payload }) => {
    if (typeof payload !== 'string') return;
    let frame; try { frame = JSON.parse(payload); } catch { return; }
    if (!current) return;
    if (frame.type === 'token') {
      current.firstTokenMs ??= Date.now() - current.startMs;
      current.reply += frame.content;
      current.tokenFrames++;
    } else if (['ack','meta','route','done','stopped','error','subagent_start','subagent_end'].includes(frame.type)) {
      current.frames.push(frame);
      if (frame.conversation_id) current.conversationId = frame.conversation_id;
      if (['done','stopped','error'].includes(frame.type)) current.terminal = frame.type;
    }
  });
});
async function turn(label, message) {
  current = { label, message, startMs: Date.now(), reply: '', tokenFrames: 0, frames: [] };
  evidence.turns.push(current);
  await page.getByRole('textbox', { name: 'Message Columbina', exact: true }).fill(message);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const deadline = Date.now() + 120000;
  while (!current.terminal && Date.now() < deadline) await page.waitForTimeout(250);
  current.totalMs = Date.now() - current.startMs;
  if (!current.terminal) throw new Error(`Timed out: ${label}`);
  await page.getByRole('button', { name: 'Send', exact: true }).waitFor({ state: 'visible' });
  current.body = await page.locator('body').innerText();
  current.renderedTextVisible = current.body.includes(current.reply.replace(/\[\[sticker:[a-z0-9-]+\]\]/g, '').trim());
  if (current.conversationId) {
    const response = await context.request.get(`http://127.0.0.1:5173/api/chat/conversations/${current.conversationId}`);
    current.persisted = await response.json();
  }
  const assistants = current.persisted?.messages.filter(m => m.turn_id === current.sent.turn_id && m.role === 'assistant') || [];
  if (current.terminal !== 'done' || !current.renderedTextVisible || assistants.length !== 1 || assistants[0].content !== current.reply || !current.frames.some(f => f.type === 'route')) {
    throw new Error(`Frontend/stream/persistence check failed: ${label}`);
  }
  console.log(JSON.stringify({ label, reply: current.reply, terminal: current.terminal, frames: current.frames, firstTokenMs: current.firstTokenMs, totalMs: current.totalMs }));
  await writeFile(new URL(`shared-state-e2e-${evidence.mode}-2026-10-21.json`, root), JSON.stringify(evidence, null, 2));
  current = undefined;
}
try {
  if (process.env.E2E_MODE) {
    await context.addCookies(JSON.parse(await (await import('node:fs/promises')).readFile(cookiePath, 'utf8')));
    await page.goto('http://127.0.0.1:5173/chat');
  } else {
    await page.goto('http://127.0.0.1:5173/login?mode=signup');
    await page.getByLabel('Name', { exact: true }).fill('Shared State E2E');
    await page.getByLabel('Email', { exact: true }).fill(`shared-state-${Date.now()}@example.com`);
    await page.getByLabel('Password', { exact: true }).fill(randomBytes(18).toString('hex'));
    await page.getByRole('button', { name: 'Create account', exact: true }).click();
  }
  await page.getByRole('textbox', { name: 'Message Columbina', exact: true }).waitFor();
  evidence.user = await (await context.request.get('http://127.0.0.1:5173/api/auth/me')).json();
  await writeFile(cookiePath, JSON.stringify(await context.cookies()), { mode: 0o600 });
  if (process.env.E2E_MODE === 'cache') {
    await page.getByRole('button', { name: 'New chat', exact: true }).click();
    await turn('memory_tool', 'Please use search_memories to check the current meeting time and place for the Lantern astronomy club. Tell me only what your saved memory supports.');
    await turn('cached_follow_up', 'Repeat that time and place from the context you already have; no new search is needed.');
    await turn('cached_follow_up_again', 'And the meeting day? Use the same context, without searching again.');
  } else if (process.env.E2E_MODE === 'reading') {
    await page.getByRole('button', { name: 'New chat', exact: true }).click();
    await turn('reading_arm', 'Hi Columbina. I feel like a quiet chat about rainy evenings.');
  } else if (process.env.E2E_MODE === 'degraded') {
    await page.getByRole('button', { name: 'New chat', exact: true }).click();
    await turn('student_disabled', 'Hi Columbina. Just a quick hello — how are you?');
  } else {
    await turn('greeting', 'Hi Columbina. I have a little time to chat.');
    await turn('recent_context', 'I am choosing a name for a fictional lighthouse: Silver Wren or Quiet Harbor. I like Silver Wren because it sounds livelier.');
    await turn('follow_up', 'Which one did I lean toward, and why?');
    await turn('correction', 'Actually, I changed my mind: Quiet Harbor is the one I want. Silver Wren feels too busy.');
    await turn('corrected_follow_up', 'What name have I settled on now?');
    await turn('topic_change', 'Different topic: I like the sound of rain on windows. There is something cozy about it.');
    await page.getByRole('button', { name: 'New chat', exact: true }).click();
    await turn('new_conversation', 'Hello again. A fresh chat and a quick hello.');
  }
  await page.screenshot({ path: new URL(`shared-state-e2e-${evidence.mode}-2026-10-21.png`, root).pathname, fullPage: true });
} catch (error) {
  evidence.failure = String(error);
  console.error(error);
  await page.screenshot({ path: new URL(`shared-state-e2e-${evidence.mode}-failure-2026-10-21.png`, root).pathname, fullPage: true });
  process.exitCode = 1;
} finally {
  evidence.finished = new Date().toISOString();
  await writeFile(new URL(`shared-state-e2e-${evidence.mode}-2026-10-21.json`, root), JSON.stringify(evidence, null, 2));
  await browser.close();
}
