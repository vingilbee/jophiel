// Single actions for code she writes (stage H): one swing, one block broken or placed, one look,
// one item in hand, one jump. Skills are whole moves ("fight hostile mobs"); these are what moves
// are made of, so a program of hers can build a move no skill has ("hit this rabbit", "dig my way
// up"). Each finishes in one call and returns { status: 'done' | 'failed', reason, data }, like a
// skill. No target is off limits (the user decided, 2026-10-15): her code decides what to hit.
// The sandbox (src/sandbox.js) owns the limits: how many, one at a time, stop, low health.
import { createRequire } from 'node:module'

import { attackCooldownMs, bestToolFor } from './skills/common.js'
import { canGoIn, isSolid, occupied } from './skills/hands.js'
import { entityLabel } from './state.js'
import { viewVector } from './motion/gaze.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')

export const ENTITY_REACH = 3 // from her eyes to the nearest point of its body, as in survival
export const BLOCK_REACH = 4.5 // from her eyes to the middle of the block
const DIG_SECONDS = 20 // longer than this with what she has (obsidian by hand): refused
const PICKUP_MS = 1200 // a drop next to her lands in her inventory within about this
const PICKUP_REACH = 2.5
const PICKUP_POLL_MS = 50
const JUMP_MS = 800 // a jump is up and over in about half a second
const JUMP_POLL_MS = 25
const FACING_DEG = 12 // already looking this close to where a block goes: no turn first
const PLACE_SETTLE_MS = 150
const SLOTS = ['hand', 'off-hand', 'head', 'torso', 'legs', 'feet']
const FACES = [new Vec3(0, 1, 0), new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(0, -1, 0)]

const done = (data = {}) => ({ status: 'done', reason: null, data })
const failed = (reason, data = {}) => ({ status: 'failed', reason, data })
const round1 = (n) => Math.round(n * 10) / 10

const eye = (bot) => bot.entity.position.offset(0, bot.entity.eyeHeight ?? 1.62, 0)

// Whole-number block coordinates, or null.
function blockPos (x, y, z) {
  const n = [x, y, z].map(Number)
  return n.every(Number.isFinite) ? new Vec3(Math.floor(n[0]), Math.floor(n[1]), Math.floor(n[2])) : null
}

// From her eyes to the nearest point of the entity's box.
function reachTo (bot, entity) {
  const from = eye(bot)
  const w = (entity.width ?? 0.6) / 2
  const h = entity.height ?? 1.8
  const p = entity.position
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))
  const nearest = new Vec3(clamp(from.x, p.x - w, p.x + w), clamp(from.y, p.y, p.y + h), clamp(from.z, p.z - w, p.z + w))
  return nearest.distanceTo(from)
}

const middle = (p) => p.offset(0.5, 0.5, 0.5)

// Is she already looking at this point (within FACING_DEG)?
function facing (bot, point) {
  const from = eye(bot)
  const to = point.minus(from)
  const length = Math.hypot(to.x, to.y, to.z)
  if (!length) return true
  const v = viewVector(bot.entity.yaw, bot.entity.pitch)
  const cos = (v.x * to.x + v.y * to.y + v.z * to.z) / length
  return Math.acos(Math.max(-1, Math.min(1, cos))) <= FACING_DEG * Math.PI / 180
}

// race(promise): settles with the promise, or rejects once the program ends (src/sandbox.js).
export function createActions (bot, { race = (promise) => promise, sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)) } = {}) {
  return {
    // One swing at the entity with this id (state().nearby[i].id), if it's within reach.
    async attack (id) {
      const entity = bot.entities[Number(id)]
      if (!entity || entity.isValid === false) return failed('nothing with that id here (gone, or out of sight)')
      if (entity === bot.entity) return failed('that\'s you')
      const label = entityLabel(entity)
      if (reachTo(bot, entity) > ENTITY_REACH) {
        return failed(`out of reach: ${label} is ${round1(reachTo(bot, entity))} m away, needs ${ENTITY_REACH} or less`)
      }
      await race(bot.lookAt(entity.position.offset(0, (entity.height ?? 1.8) * 0.5, 0)))
      if (entity.isValid === false || !bot.entities[entity.id]) return failed(`${label} is gone`)
      const distance = reachTo(bot, entity)
      if (distance > ENTITY_REACH) return failed(`${label} moved out of reach (${round1(distance)} m)`)
      bot.attack(entity)
      return done({ target: label, distance: round1(distance), cooldown_ms: attackCooldownMs(bot.heldItem?.name) })
    },

    // Break the block at x, y, z with the best tool she carries (equipped for it).
    async dig (x, y, z) {
      const p = blockPos(x, y, z)
      if (!p) return failed('dig needs numbers x, y, z')
      const block = bot.blockAt(p)
      if (!block) return failed('that spot isn\'t loaded')
      if (block.name === 'air' || block.name === 'cave_air' || block.name === 'void_air') return failed('nothing to dig there (air)')
      if (block.name === 'water' || block.name === 'lava') return failed(`can't dig ${block.name}`)
      if (block.diggable === false) return failed(`${block.name} can't be broken`)
      const distance = eye(bot).distanceTo(middle(p))
      if (distance > BLOCK_REACH) return failed(`out of reach: ${round1(distance)} m away, needs ${BLOCK_REACH} or less`)
      const { item, harvestable } = bestToolFor(bot, block)
      const tool = harvestable ? item : bot.heldItem
      if (tool && bot.heldItem?.type !== tool.type) await race(bot.equip(tool, 'hand'))
      const ms = block.digTime(tool?.type ?? null, false, false, false, tool?.enchants ?? [], bot.entity.effects)
      if (ms > DIG_SECONDS * 1000) return failed(`${block.name} would take ${Math.round(ms / 1000)} s with ${tool?.name ?? 'her hand'}`)
      const carried = () => bot.inventory.items().reduce((n, i) => n + i.count, 0)
      const had = carried()
      await race(bot.dig(block, true))
      const after = bot.blockAt(p)
      if (after?.name === block.name) return failed(`the ${block.name} is still there`)
      // What drops next to her is hers in a moment: wait for it, so her code's next look at her
      // inventory has it (a trial: she dug, found nothing to climb with yet, and gave up).
      if (harvestable && bot.entity.position.distanceTo(middle(p)) <= PICKUP_REACH + 1) {
        const until = Date.now() + PICKUP_MS
        while (carried() <= had && Date.now() < until) await race(sleep(PICKUP_POLL_MS))
      }
      return done({ block: block.name, x: p.x, y: p.y, z: p.z, tool: tool?.name ?? 'hand', drops: harvestable,
        picked_up: Math.max(0, carried() - had) })
    },

    // Put the held block (or `item`, equipped first) at x, y, z, resting against a solid block beside it.
    async place (x, y, z, item = null) {
      const p = blockPos(x, y, z)
      if (!p) return failed('place needs numbers x, y, z')
      if (item) {
        const carried = bot.inventory.items().find((i) => i.name === String(item))
        if (!carried) return failed(`she has no ${item}`)
        if (bot.heldItem?.name !== carried.name) await race(bot.equip(carried, 'hand'))
      }
      const held = bot.heldItem
      const placing = held && bot.registry.blocksByName[held.name]
      if (!placing) {
        const blocks = [...new Set(bot.inventory.items().map((i) => i.name).filter((n) => bot.registry.blocksByName[n]))]
        const holding = held ? `she holds ${held.name}, not a block` : 'her hand is empty'
        return failed(`${holding}: pass the block to place, e.g. act.place(x, y, z, "${blocks[0] ?? 'dirt'}")` +
          (blocks.length ? ` (blocks she carries: ${blocks.slice(0, 8).join(', ')})` : ' (she carries no blocks)'))
      }
      const there = bot.blockAt(p)
      if (!there) return failed('that spot isn\'t loaded')
      if (!canGoIn(there, placing)) return failed(`that spot is taken (${there.name})`)
      const distance = eye(bot).distanceTo(middle(p))
      if (distance > BLOCK_REACH) return failed(`out of reach: ${round1(distance)} m away, needs ${BLOCK_REACH} or less`)
      const face = FACES.find((f) => isSolid(bot.blockAt(p.minus(f))))
      if (!face) return failed('nothing solid next to that spot for it to rest on')
      if (occupied(bot, p)) {
        const feet = bot.entity.position.floored()
        if (feet.equals(p) && !bot.entity.onGround) {
          return failed(`she is in that spot herself (in the air): the block under her feet is ${p.x} ${p.y - 1} ${p.z}`)
        }
        return failed('someone (maybe her) is standing in that spot')
      }
      const reference = bot.blockAt(p.minus(face))
      // Looking at it already, or it's right under her feet while she's in the air (mid-jump):
      // place at once. Turning first lets her fall back into the spot she's filling (live: her code
      // looked at the corner of the block under her, and every place was refused).
      const feet = bot.entity.position.floored()
      const underHer = !bot.entity.onGround && p.x === feet.x && p.z === feet.z && p.y < feet.y
      const looking = bot._placeBlockWithOptions && (underHer ||
        facing(bot, reference.position.offset(0.5 + face.x * 0.5, 0.5 + face.y * 0.5, 0.5 + face.z * 0.5)))
      try {
        await race(looking
          ? bot._placeBlockWithOptions(reference, face, { swingArm: 'right', forceLook: 'ignore' })
          : bot.placeBlock(reference, face))
      } catch (err) {
        if (err?.name === 'ProgramEnd') throw err
        // placeBlock times out waiting for the update now and then; the block may still be there.
      }
      await race(sleep(PLACE_SETTLE_MS))
      const now = bot.blockAt(p)
      if (!now || now.name === there.name) return failed('the game didn\'t accept it')
      return done({ block: now.name, x: p.x, y: p.y, z: p.z })
    },

    // Turn her head to face the point x, y, z (a block's middle is +0.5 on each axis).
    async look (x, y, z) {
      const n = [x, y, z].map(Number)
      if (!n.every(Number.isFinite)) return failed('look needs numbers x, y, z')
      await race(bot.lookAt(new Vec3(n[0], n[1], n[2])))
      return done({ yaw: round1(bot.entity.yaw * 180 / Math.PI), pitch: round1(bot.entity.pitch * 180 / Math.PI) })
    },

    // Hold an item, or wear it: slot is hand (default), off-hand, head, torso, legs or feet.
    async equip (item, slot = 'hand') {
      if (!SLOTS.includes(slot)) return failed(`slot must be one of ${SLOTS.join(', ')}`)
      const carried = bot.inventory.items().find((i) => i.name === String(item))
      if (!carried) return failed(`she has no ${item}`)
      await race(bot.equip(carried, slot))
      return done({ item: carried.name, slot })
    },

    // One jump from the ground, resolving as soon as her feet are a block up (still rising), or at
    // its top: placing a block under her feet right after is how she climbs. Timing that from her
    // code missed (a trial, 2026-10-15: she polled her height, and by the time she placed she was
    // falling back into it); waiting for the very top left about 200 ms, and a place now and then
    // came too late (tests, 2026-10-17).
    async jump () {
      if (!bot.entity.onGround) return failed('not on the ground (still in the air: wait until state().on_ground)')
      const from = bot.entity.position.y
      const feet = bot.entity.position.floored()
      const deadline = Date.now() + JUMP_MS
      let left = false
      bot.setControlState('jump', true)
      try {
        while (Date.now() < deadline) {
          await race(sleep(JUMP_POLL_MS))
          if (!bot.entity.onGround && !left) {
            left = true
            bot.setControlState('jump', false) // held, she'd jump again on landing
          }
          if (left && (bot.entity.position.y >= from + 1 || (bot.entity.velocity?.y ?? 0) <= 0)) break
        }
      } finally {
        bot.setControlState('jump', false)
      }
      if (!left) return failed('she didn\'t leave the ground (a block over her head?)')
      const top = bot.entity.position.y
      const data = { from_y: round1(from), top_y: round1(top), rose: round1(top - from) }
      // Climbing needs her feet a block up at the top; a ceiling cuts the jump short.
      if (top - from < 1) {
        const over = [2, 3].map((dy) => bot.blockAt(feet.offset(0, dy, 0))).find(isSolid)
        return { status: 'done', data, reason: `rose only ${data.rose}, less than a block` +
          (over ? `: ${over.name} over her head (a full jump needs the 2 blocks over her head clear)` : '') }
      }
      return done(data)
    }
  }
}

export const ACTION_NAMES = ['attack', 'dig', 'place', 'look', 'equip', 'jump']
