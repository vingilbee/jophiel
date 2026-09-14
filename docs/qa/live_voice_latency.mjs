import fs from 'node:fs'
import { chromium } from '../../frontend/node_modules/playwright/index.mjs'

const base = 'http://127.0.0.1:54173'
const wavPath = '../../backend/app/ai/voice/ref_columbina_6s.wav'

function readPcmWav(path) {
  const wav = fs.readFileSync(new URL(path, import.meta.url))
  if (wav.toString('ascii', 0, 4) !== 'RIFF' || wav.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error(`not a RIFF/WAVE file: ${path}`)
  }
  let offset = 12
  let sampleRate = null
  let channels = null
  let bits = null
  let pcm = null
  while (offset + 8 <= wav.length) {
    const id = wav.toString('ascii', offset, offset + 4)
    const size = wav.readUInt32LE(offset + 4)
    const start = offset + 8
    if (id === 'fmt ') {
      channels = wav.readUInt16LE(start + 2)
      sampleRate = wav.readUInt32LE(start + 4)
      bits = wav.readUInt16LE(start + 14)
    } else if (id === 'data') {
      pcm = wav.subarray(start, start + size)
      break
    }
    offset = start + size + (size % 2)
  }
  if (channels !== 1 || bits !== 16 || !pcm) {
    throw new Error(`expected mono 16-bit WAV, got ${sampleRate}Hz/${channels}ch/${bits}bit`)
  }
  if (sampleRate === 16000) return { sampleRate, pcm }
  const input = new Int16Array(pcm.buffer, pcm.byteOffset, pcm.byteLength / 2)
  const output = new Int16Array(Math.round(input.length * 16000 / sampleRate))
  for (let i = 0; i < output.length; i++) {
    const source = i * (sampleRate / 16000)
    const left = Math.floor(source)
    const right = Math.min(left + 1, input.length - 1)
    const fraction = source - left
    output[i] = Math.max(-32768, Math.min(32767, Math.round(input[left] * (1 - fraction) + input[right] * fraction)))
  }
  return { sampleRate: 16000, pcm: Buffer.from(output.buffer) }
}

const audio = readPcmWav(wavPath)
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
page.setDefaultTimeout(20_000)
await page.goto(base + '/login')

const email = `voice-latency-${Date.now()}@example.com`
const password = 'Synthetic-voice-latency-2026'
const signup = await context.request.post(base + '/api/auth/signup', {
  headers: { Origin: base },
  data: { name: 'Voice Latency QA', email, password },
})
if (signup.status() !== 201) throw new Error(`signup failed: ${signup.status()} ${await signup.text()}`)
await page.getByPlaceholder('you@example.com').fill(email)
await page.locator('input[type=password]').fill(password)
await page.locator('form button[type=submit]').click()
await page.waitForURL('**/chat')

const result = await page.evaluate(async ({ pcm, sampleRate, base }) => {
  const run = (audio, rate, url) => new Promise((resolve, reject) => {
    const turnId = crypto.randomUUID()
    const started = performance.now()
    const events = []
    let firstBinaryAt = null
    let audioStartAt = null
    let lastTokenAt = null
    let settled = false
    const ws = new WebSocket(`ws://${new URL(url).host}/api/chat/ws`)
    ws.binaryType = 'arraybuffer'
    const finish = value => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      ws.close()
      resolve({
        ...value,
        events,
        timings: {
          voiceEndToTranscriptMs: at('transcript'),
          transcriptToFirstTokenMs: between('transcript', 'token'),
          transcriptToLastTokenMs: between('transcript', 'last_token'),
          transcriptToFirstAudioMs: between('transcript', 'first_audio'),
          voiceEndToFirstAudioMs: at('first_audio'),
          voiceEndToDoneMs: at('done'),
          firstTokenToFirstAudioMs: between('token', 'first_audio'),
        },
      })
    }
    const timer = setTimeout(() => finish({ timeout: true }), 120_000)
    const marks = {}
    const mark = (name, time = performance.now()) => {
      if (marks[name] == null) marks[name] = time
    }
    const at = name => marks[name] == null ? null : Math.round(marks[name] - started)
    const between = (a, b) => marks[a] == null || marks[b] == null ? null : Math.round(marks[b] - marks[a])
    ws.onerror = () => finish({ error: 'websocket error' })
    ws.onmessage = event => {
      if (typeof event.data !== 'string') {
        if (firstBinaryAt == null) {
          firstBinaryAt = performance.now()
          mark('first_audio', firstBinaryAt)
          events.push({ type: 'first_audio', ms: at('first_audio') })
        }
        return
      }
      const message = JSON.parse(event.data)
      if (message.type === 'transcript') {
        mark('transcript')
        events.push({ type: 'transcript', ms: at('transcript'), content: message.content })
      } else if (message.type === 'token') {
        if (marks.token == null) mark('token')
        mark('last_token')
        events.push({ type: 'token', ms: at('token'), content: message.content })
        lastTokenAt = performance.now()
      } else if (message.type === 'audio_start') {
        audioStartAt = performance.now()
        mark('audio_start', audioStartAt)
        events.push({ type: 'audio_start', ms: at('audio_start'), sampleRate: message.sample_rate })
      } else if (message.type === 'audio_end') {
        events.push({ type: 'audio_end', ms: at('audio_end') })
      } else if (['done', 'error', 'stopped'].includes(message.type)) {
        mark(message.type)
        events.push({ type: message.type, ms: at(message.type), detail: message.detail })
        if (message.type === 'done' || message.type === 'error' || message.type === 'stopped') finish({ terminal: message })
      }
    }
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'voice_start', conversation_id: null, turn_id: turnId, sample_rate: rate }))
      ws.send(audio)
      ws.send(JSON.stringify({ type: 'voice_end', turn_id: turnId }))
      mark('voice_end')
    }
  })
  const runs = []
  for (let i = 0; i < 3; i++) runs.push(await run(new Uint8Array(pcm), sampleRate, base))
  return runs
}, { pcm: [...audio.pcm], sampleRate: audio.sampleRate, base })

console.log(JSON.stringify({
  audio: { path: wavPath, sampleRate: audio.sampleRate, seconds: audio.pcm.length / 2 / audio.sampleRate },
  runs: result,
}, null, 2))
await browser.close()
