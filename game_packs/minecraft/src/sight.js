// What she can know is there: blocks she can see from where she stands, or saw earlier. Mineflayer
// knows every block in the loaded chunks, so findBlock alone finds ore through solid stone (seen
// live: gather tunnelled to iron nobody could see, and stalled on ore it couldn't reach). A player
// knows a block only once a face of it has been in view; she remembers what she's seen, like
// someone who walked past it.
const FACES = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]
const REMEMBER = 4000 // positions; the oldest are forgotten first
const LOOK_AT = 128 // candidates checked per search: hidden ones can crowd out the nearest visible

const memories = new WeakMap()
function remembered (bot) {
  if (!memories.has(bot)) memories.set(bot, new Map())
  return memories.get(bot)
}

function seeThrough (block) {
  return !block || block.boundingBox !== 'block' || block.transparent
}

// Is any face of the block at this position in view? A ray from her eyes to the middle of each
// open face (one with air, water, glass... next to it) must reach it before hitting anything else.
export function canSeeBlock (bot, position) {
  const eye = bot.entity.position.offset(0, bot.entity.eyeHeight ?? 1.62, 0)
  for (const [dx, dy, dz] of FACES) {
    if (!seeThrough(bot.blockAt(position.offset(dx, dy, dz)))) continue
    const face = position.offset(0.5 + dx * 0.501, 0.5 + dy * 0.501, 0.5 + dz * 0.501)
    const delta = face.minus(eye)
    // The face must point at her, or she'd be looking at its back.
    if (delta.x * dx + delta.y * dy + delta.z * dz >= 0) continue
    const range = delta.norm()
    if (range < 0.01) return true
    const hit = bot.world.raycast(eye, delta.normalize(), range + 0.5,
      (b) => b.boundingBox === 'block' && !b.transparent)
    if (hit && hit.position.equals(position)) return true
  }
  return false
}

// findBlocks, but only blocks she can see now or has seen (and that are still that block).
export function seenBlocks (bot, { matching, maxDistance, count = 1 }) {
  const ids = new Set(Array.isArray(matching) ? matching : [matching])
  const memory = remembered(bot)
  const out = []
  for (const position of bot.findBlocks({ matching: [...ids], maxDistance, count: Math.max(count, LOOK_AT) })) {
    const block = bot.blockAt(position)
    if (!block) continue
    const key = position.toString()
    let seen = memory.get(key) === block.type
    if (!seen && canSeeBlock(bot, position)) {
      memory.delete(key)
      memory.set(key, block.type)
      if (memory.size > REMEMBER) memory.delete(memory.keys().next().value)
      seen = true
    }
    if (seen) out.push(position)
    if (out.length >= count) break
  }
  return out
}

export function seenBlock (bot, options) {
  const [position] = seenBlocks(bot, { ...options, count: 1 })
  return position ? bot.blockAt(position) : null
}
