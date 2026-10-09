// Pure maths for smooth looking. Angles in radians unless a name says deg.
// Mineflayer: yaw 0 faces north (-z) and grows counter-clockwise; pitch > 0 looks up.

export const DEG = Math.PI / 180
const TAU = Math.PI * 2

// Shortest signed difference to - from, in (-pi, pi].
export function angleDiff (from, to) {
  const d = ((to - from + Math.PI) % TAU + TAU) % TAU - Math.PI
  return d === -Math.PI ? Math.PI : d
}

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))

// One tick of one axis. Turning speeds up at most `accel`, never exceeds `maxTurn`, and eases out
// in proportion to the angle left (`gain`), so it starts gently, cruises, and settles: no snapping.
export function stepAxis ({ pos, vel }, target, dt, style, { wrap = false } = {}) {
  const error = wrap ? angleDiff(pos, target) : target - pos
  const maxTurn = style.maxTurn * DEG
  const want = clamp(error * style.gain, -maxTurn, maxTurn)
  const maxChange = style.accel * DEG * dt
  const next = vel + clamp(want - vel, -maxChange, maxChange)
  // Close enough and slow: settle exactly instead of creeping.
  if (Math.abs(error) < 0.05 * DEG && Math.abs(next) < 2 * DEG) return { pos: target, vel: 0 }
  // A fast turn can carry slightly past the target and come back, like a hand does.
  return { pos: pos + next * dt, vel: next }
}

// Slow aim drift: two incommensurate sine waves per axis, amplitude in degrees.
export function wobble (t, amplitudeDeg, phases) {
  const a = amplitudeDeg * DEG
  return {
    yaw: a * (0.6 * Math.sin(TAU * 0.37 * t + phases[0]) + 0.4 * Math.sin(TAU * 0.83 * t + phases[1])),
    pitch: 0.5 * a * (0.6 * Math.sin(TAU * 0.29 * t + phases[2]) + 0.4 * Math.sin(TAU * 0.71 * t + phases[3]))
  }
}

// Direction she's looking, as a unit vector.
export function viewVector (yaw, pitch) {
  return { x: -Math.sin(yaw) * Math.cos(pitch), y: Math.sin(pitch), z: -Math.cos(yaw) * Math.cos(pitch) }
}

// Yaw and pitch from an eye position to a point.
export function directionTo (eye, point) {
  const dx = point.x - eye.x
  const dy = point.y - eye.y
  const dz = point.z - eye.z
  return { yaw: Math.atan2(-dx, -dz), pitch: Math.atan2(dy, Math.hypot(dx, dz)) }
}

// Does a ray from `eye` along (yaw, pitch) hit the box within `reach`? Slab test.
export function rayHitsBox (eye, yaw, pitch, box, reach) {
  const dir = viewVector(yaw, pitch)
  let tMin = 0
  let tMax = reach
  for (const axis of ['x', 'y', 'z']) {
    const min = box.min[axis]
    const max = box.max[axis]
    if (Math.abs(dir[axis]) < 1e-9) {
      if (eye[axis] < min || eye[axis] > max) return false
      continue
    }
    let t1 = (min - eye[axis]) / dir[axis]
    let t2 = (max - eye[axis]) / dir[axis]
    if (t1 > t2) [t1, t2] = [t2, t1]
    tMin = Math.max(tMin, t1)
    tMax = Math.min(tMax, t2)
    if (tMin > tMax) return false
  }
  return true
}
