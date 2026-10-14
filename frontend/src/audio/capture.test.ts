import { afterEach, expect, test } from 'bun:test'
import { startCapture } from './capture'

const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
const originalContext = Object.getOwnPropertyDescriptor(globalThis, 'AudioContext')
const originalWorklet = Object.getOwnPropertyDescriptor(globalThis, 'AudioWorkletNode')

afterEach(() => {
  for (const [name, descriptor] of [['navigator', originalNavigator], ['AudioContext', originalContext], ['AudioWorkletNode', originalWorklet]] as const) {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor)
    else Reflect.deleteProperty(globalThis, name)
  }
})

test('muting disables every microphone track without ending capture; unmute restores them', async () => {
  const tracks = [0, 1].map(() => ({ enabled: true, readyState: 'live', stop() { this.readyState = 'ended' } }))
  let closed = false
  const node = () => ({ connect() { return this }, disconnect() {}, gain: { value: 1 } })
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: {
    mediaDevices: { getUserMedia: async () => ({ active: true, getAudioTracks: () => tracks, getTracks: () => tracks }) },
  } })
  Object.defineProperty(globalThis, 'AudioContext', { configurable: true, value: class {
    sampleRate = 16000
    state = 'running'
    destination = {}
    audioWorklet = { addModule: async () => {} }
    createMediaStreamSource = node
    createGain = node
    async close() { closed = true }
  } })
  Object.defineProperty(globalThis, 'AudioWorkletNode', { configurable: true, value: class {
    port = { onmessage: null, postMessage() {} }
    connect() { return this }
    disconnect() {}
  } })
  const capture = await startCapture(() => {})
  capture.setMuted(true)
  expect(tracks.every((track) => !track.enabled)).toBe(true)
  expect(capture.active).toBe(true)
  expect(closed).toBe(false)
  capture.setMuted(false)
  expect(tracks.every((track) => track.enabled)).toBe(true)
  capture.stop()
  expect(tracks.every((track) => track.readyState === 'ended')).toBe(true)
  expect(capture.active).toBe(false)
  expect(closed).toBe(true)
})
