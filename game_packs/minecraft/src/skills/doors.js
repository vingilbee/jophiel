// Doors and gates. Pathfinder judges a block by its type, so it treats every door, even an open
// one, as a wall (and its own door opening is switched off upstream as unreliable). We plan routes
// through wooden doors and gates as if they were open, open any closed one just before she
// reaches it, and close doors she opened once she's through (so mobs don't follow into houses).
// Iron doors and gates stay walls: they need redstone.
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')

const OPEN_AHEAD = [0.6, 1.2, 1.8] // blocks ahead (along her heading) to check for closed doors
const COOLDOWN_MS = 1500 // one click per door per this long; a double click would close it again
const CLOSE_AFTER = 2.2 // she's this far past a door she opened: close it
const LEAVE_OPEN_AFTER = 4 // farther than this (out of easy reach): leave it

export function isPassableDoor (name) {
  return Boolean(name) && !name.startsWith('iron_') &&
    (name.endsWith('_door') || name.endsWith('_fence_gate'))
}

const isOpen = (block) => block?.getProperties?.().open === true
// The lower half of a door identifies it (both halves open it).
const doorKey = (block) => {
  const half = block.getProperties?.().half
  const p = half === 'upper' ? block.position.offset(0, -1, 0) : block.position
  return p.toString()
}

// Make a pathfinder Movements class route through doors and gates.
export function doorAware (MovementsClass) {
  return class DoorAwareMovements extends MovementsClass {
    getBlock (pos, dx, dy, dz) {
      const b = super.getBlock(pos, dx, dy, dz)
      if (isPassableDoor(b?.name)) {
        b.safe = true
        b.physical = false
        b.replaceable = false
        b.openable = false // never let pathfinder "use" it itself: that toggles open doors shut
        b.height = pos.y + dy
      }
      return b
    }
  }
}

// Pathfinder snaps each path step to "the top of" the block there, which for a door's thin
// collision shape means standing on top of the door. Put those steps back in the doorway.
export function fixDoorSteps (bot, path) {
  for (const node of path) {
    const at = new Vec3(Math.floor(node.x), Math.floor(node.y), Math.floor(node.z))
    for (const position of [at, at.offset(0, -1, 0)]) {
      const block = bot.blockAt(position)
      if (isPassableDoor(block?.name) && block.getProperties?.().half !== 'upper') {
        node.x = position.x + 0.5
        node.y = position.y
        node.z = position.z + 0.5
        break
      }
    }
  }
}

export function attachDoors (bot) {
  const clicked = new Map() // door key -> last click time
  const opened = new Map() // door key -> position of a door she opened

  function click (block, key) {
    const now = Date.now()
    if (now - (clicked.get(key) ?? 0) < COOLDOWN_MS) return false
    clicked.set(key, now)
    bot.activateBlock(block).catch(() => {})
    return true
  }

  function onTick () {
    if (!bot.entity) return
    const here = bot.entity.position

    if (bot.pathfinder?.isMoving?.()) {
      const dir = { x: -Math.sin(bot.entity.yaw), z: -Math.cos(bot.entity.yaw) }
      for (const ahead of OPEN_AHEAD) {
        for (const dy of [0, 1]) {
          const block = bot.blockAt(new Vec3(here.x + dir.x * ahead, here.y + dy, here.z + dir.z * ahead).floored())
          if (!isPassableDoor(block?.name) || isOpen(block)) continue
          const key = doorKey(block)
          if (click(block, key)) opened.set(key, block.position)
        }
      }
    }

    for (const [key, position] of opened) {
      const distance = here.distanceTo(position.offset(0.5, 0, 0.5))
      if (distance > LEAVE_OPEN_AFTER) {
        opened.delete(key)
        continue
      }
      if (distance < CLOSE_AFTER) continue
      const block = bot.blockAt(position)
      if (!isPassableDoor(block?.name) || !isOpen(block)) {
        opened.delete(key)
        continue
      }
      // Not in someone's face: leave it open while a player is in or right next to the doorway.
      const someone = Object.values(bot.players).some((p) => p.entity && p.entity !== bot.entity &&
        p.entity.position.distanceTo(position.offset(0.5, 0, 0.5)) < 1.8)
      if (!someone && click(block, key)) opened.delete(key)
    }
  }

  // Runs before pathfinder starts following the new path (it emits, then uses the same array).
  const onPath = (results) => fixDoorSteps(bot, results.path)
  bot.on('physicsTick', onTick)
  bot.on('path_update', onPath)
  return {
    detach: () => {
      bot.off('physicsTick', onTick)
      bot.off('path_update', onPath)
    }
  }
}
