import assert from 'node:assert/strict'
import { test } from 'node:test'

import { DEG, angleDiff, directionTo, rayHitsBox, stepAxis, wobble } from '../src/motion/gaze.js'
import { STYLES } from '../src/motion/styles.js'

const DT = 0.05

// Turn from `from` to `to` (degrees) tick by tick; returns per-tick steps in degrees.
function turn (from, to, style, ticks = 60) {
  let axis = { pos: from * DEG, vel: 0 }
  const steps = []
  for (let i = 0; i < ticks; i++) {
    const next = stepAxis(axis, to * DEG, DT, style, { wrap: true })
    steps.push(angleDiff(axis.pos, next.pos) / DEG)
    axis = next
  }
  return { steps, final: axis.pos / DEG }
}

test('angles wrap the short way round', () => {
  assert.ok(Math.abs(angleDiff(170 * DEG, -170 * DEG) - 20 * DEG) < 1e-9)
  assert.ok(Math.abs(angleDiff(-170 * DEG, 170 * DEG) + 20 * DEG) < 1e-9)
})

for (const [name, style] of Object.entries(STYLES)) {
  test(`${name}: a 150° turn never snaps, starts gently, and settles`, () => {
    const { steps, final } = turn(0, 150, style)
    const maxStep = style.maxTurn * DT
    assert.ok(Math.max(...steps.map(Math.abs)) <= maxStep + 1e-6, 'faster than the top speed')
    assert.ok(Math.abs(steps[0]) <= style.accel * DT * DT + 1e-6, 'the first tick jumps instead of easing in')
    assert.ok(Math.abs(steps[0]) < Math.abs(steps[3]), 'no speeding up')
    assert.ok(Math.abs(angleDiff(final * DEG, 150 * DEG)) < 0.5 * DEG, `ended at ${final}`)
    // Overshoot, if any, stays small.
    const passed = steps.reduce((acc, s) => acc + s, 0)
    assert.ok(passed < 150 + 8, `overshot to ${passed}`)
  })
}

test('relaxed turns take longer than combat turns', () => {
  const settleTick = (style) => turn(0, 120, style).steps.findIndex((s, i, all) =>
    all.slice(i).every((x) => Math.abs(x) < 0.2))
  assert.ok(settleTick(STYLES.relaxed) > settleTick(STYLES.combat))
})

test('turning crosses ±180° the short way', () => {
  const { steps } = turn(170, -170, STYLES.relaxed, 30)
  assert.ok(steps.every((s) => s >= -1e-9), 'turned the long way round')
})

test('wobble stays within its amplitude', () => {
  for (let t = 0; t < 30; t += 0.1) {
    const w = wobble(t, 1.2, [0.1, 2, 3, 4])
    assert.ok(Math.abs(w.yaw) <= 1.2 * DEG + 1e-9 && Math.abs(w.pitch) <= 0.6 * DEG + 1e-9)
  }
})

test('crosshair ray hits a box straight ahead and misses one off to the side', () => {
  const eye = { x: 0, y: 1.62, z: 0 }
  const box = (x, z) => ({ min: { x: x - 0.4, y: 0, z: z - 0.4 }, max: { x: x + 0.4, y: 1.9, z: z + 0.4 } })
  const ahead = directionTo(eye, { x: 0, y: 1.5, z: -2.5 })
  assert.ok(rayHitsBox(eye, ahead.yaw, ahead.pitch, box(0, -2.5), 3.2))
  assert.ok(!rayHitsBox(eye, ahead.yaw + 25 * DEG, ahead.pitch, box(0, -2.5), 3.2), 'a wide swing still hit')
  assert.ok(!rayHitsBox(eye, ahead.yaw, ahead.pitch, box(0, -5), 3.2), 'hit beyond reach')
})
