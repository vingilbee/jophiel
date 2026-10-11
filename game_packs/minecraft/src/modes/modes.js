// Background modes: small always-on behaviours beside whatever skill she's running. Her body still
// does one thing at a time: when a mode is needed, the runner puts the skill on hold, runs the
// mode's action (itself a skill: fight, mine, collect_items), then picks the skill back up.
// Seen live: following the user to a cave, she could neither fight off the spiders nor grab the
// iron on the way, though she'd said she would.
//   guard:  a hostile close to her, or to the player she's with -> fight it
//   gather: a block on her list close by (iron_ore, coal_ore...) -> mine it, then catch up
//   pickup: items on the ground right by her -> pick them up
// Settings outlive a reconnect (one object per pack process). Checks are rules, never a model.
import { ParamError } from '../skills/contract.js'
import { bestToolFor, blockIds, findPlayer, hostilesAround, requireBlock } from '../skills/common.js'
import { pickTarget } from '../skills/fight.js'

export const MODE_NAMES = ['guard', 'gather', 'pickup']
// Skills a mode may put on hold and pick back up. The quick ones (craft, place, eat, sleep, give)
// finish in a moment; fight is already what guard would do.
export const RESUMABLE = new Set(['follow_player', 'go_to', 'explore', 'mine', 'collect_items'])

const GUARD_RADIUS = 8 // a hostile this close to her or to the player she's with
const GUARD_REACH = 12 // ...and no further than this from her: she doesn't run off after it
const GATHER_RADIUS = 6
const GATHER_LEASH = 16 // the player she's with must be this close, or she'd fall behind
const GATHER_MAX = 8 // blocks per stop
const PICKUP_RADIUS = 4
const MAX_GATHER = 8 // names on the list
const ACTION_SECONDS = { guard: 30, gather: 30, pickup: 10 }
const COOLDOWN_MS = { guard: 2000, gather: 2000, pickup: 4000 }
const SKIP_MS = 60000 // a target that didn't work out is left alone this long

export class ModeSettings {
  constructor () {
    this.guard = false
    this.gather = []
    this.pickup = false
  }

  // input: { guard?, gather?, pickup? }; what's left out stays as it is.
  set (input, bot) {
    if (input === null || typeof input !== 'object' || Array.isArray(input)) throw new ParamError('modes must be an object')
    for (const key of Object.keys(input)) {
      if (!MODE_NAMES.includes(key)) throw new ParamError(`no mode called ${key} (modes: ${MODE_NAMES.join(', ')})`)
    }
    for (const key of ['guard', 'pickup']) {
      if (key in input && typeof input[key] !== 'boolean') throw new ParamError(`${key} must be true or false`)
    }
    let gather = this.gather
    if ('gather' in input) {
      if (!Array.isArray(input.gather) || input.gather.some((n) => typeof n !== 'string')) {
        throw new ParamError('gather must be a list of block names')
      }
      gather = [...new Set(input.gather.map((n) => n.trim()).filter(Boolean))]
      if (gather.length > MAX_GATHER) throw new ParamError(`gather takes at most ${MAX_GATHER} block names`)
      for (const name of gather) requireBlock(bot, name)
    }
    if ('guard' in input) this.guard = input.guard
    if ('pickup' in input) this.pickup = input.pickup
    this.gather = gather
    return this.toJSON()
  }

  toJSON () {
    return { guard: this.guard, gather: [...this.gather], pickup: this.pickup }
  }

  // "guard on; gathering iron_ore, coal_ore; picking up drops", or null when all are off.
  describe () {
    const parts = []
    if (this.guard) parts.push('guard on (fights hostiles near you or the player you\'re with)')
    if (this.gather.length) parts.push(`gathering ${this.gather.join(', ')} on the way`)
    if (this.pickup) parts.push('picking up drops nearby')
    return parts.length ? parts.join('; ') : null
  }
}

// iron_ore also means deepslate_iron_ore: below y=0 the same ore is the deepslate kind.
export function gatherIds (bot, names) {
  const ids = new Set()
  for (const name of names) {
    for (const id of blockIds(bot, name)) ids.add(id)
    if (name.endsWith('_ore') && !name.startsWith('deepslate_')) {
      for (const id of blockIds(bot, `deepslate_${name}`)) ids.add(id)
    }
  }
  return [...ids]
}

// The player she's with in this skill: follow_player's player, or go_to's player:<name>.
export function companionOf (run) {
  const params = run?.params ?? {}
  if (run?.skill === 'follow_player') return params.player ?? null
  if (run?.skill === 'go_to' && typeof params.target === 'string' && params.target.startsWith('player:')) {
    return params.target.slice('player:'.length)
  }
  return null
}

// What the modes remember between checks: when each last acted, and targets to leave alone.
export class ModeMemory {
  constructor (now = Date.now) {
    this.now = now
    this.lastAction = {}
    this.skipped = new Map() // key -> until
  }

  ready (mode) {
    const last = this.lastAction[mode]
    return last === undefined || this.now() - last >= COOLDOWN_MS[mode]
  }

  skipping (key) {
    const until = this.skipped.get(key)
    if (until === undefined) return false
    if (until > this.now()) return true
    this.skipped.delete(key)
    return false
  }

  // After an action: cooldown, and its targets skipped unless it worked.
  acted (pick, worked) {
    this.lastAction[pick.mode] = this.now()
    if (!worked) for (const key of pick.keys ?? []) this.skipped.set(key, this.now() + SKIP_MS)
  }
}

// The mode to run now, if any: { mode, skill, params, seconds, why, keys }. Guard first, then
// gather, then pickup. `run` is the record of the skill on hold (its skill and params).
export function pickMode (bot, settings, memory, run) {
  if (!RESUMABLE.has(run?.skill)) return null
  const companion = companionOf(run)
  const friend = companion ? findPlayer(bot, companion) : null
  return (settings.guard && memory.ready('guard') && guardPick(bot, memory, friend)) ||
    (settings.gather.length && memory.ready('gather') && gatherPick(bot, settings, memory, run, friend)) ||
    (settings.pickup && memory.ready('pickup') && pickupPick(bot, memory, run)) ||
    null
}

function guardPick (bot, memory, friend) {
  const here = bot.entity.position
  const threats = hostilesAround(bot, GUARD_REACH).filter((e) => !memory.skipping(`mob:${e.id}`) && (
    e.position.distanceTo(here) <= GUARD_RADIUS ||
    (friend && e.position.distanceTo(friend.position) <= GUARD_RADIUS)))
  const target = pickTarget(bot, threats)
  if (!target) return null
  const nearFriend = friend && target.position.distanceTo(friend.position) <= GUARD_RADIUS
  return {
    mode: 'guard',
    skill: 'fight',
    params: { target: target.name, radius: GUARD_REACH, time_limit: ACTION_SECONDS.guard },
    seconds: ACTION_SECONDS.guard,
    why: `${target.name} ${Math.round(target.position.distanceTo(here))} m away` +
      (nearFriend ? `, close to ${friend.username}` : ''),
    keys: [`mob:${target.id}`],
    near: nearFriend ? friend.username : null
  }
}

function gatherPick (bot, settings, memory, run, friend) {
  const here = bot.entity.position
  if (friend && friend.position.distanceTo(here) > GATHER_LEASH) return null
  let ids = gatherIds(bot, settings.gather)
  if (run.skill === 'mine') {
    const own = new Set(blockIds(bot, run.params.block))
    ids = ids.filter((id) => !own.has(id)) // she's mining those anyway
  }
  if (!ids.length) return null
  const positions = bot.findBlocks({ matching: ids, maxDistance: GATHER_RADIUS, count: 32 })
    .filter((p) => !memory.skipping(`block:${p}`))
    .sort((a, b) => a.distanceTo(here) - b.distanceTo(here))
  for (const position of positions) {
    const block = bot.blockAt(position)
    if (!block) continue
    if (!bestToolFor(bot, block).harvestable) {
      memory.skipped.set(`block:${position}`, memory.now() + SKIP_MS) // no pickaxe for it yet
      continue
    }
    const same = positions.filter((p) => bot.blockAt(p)?.type === block.type)
    return {
      mode: 'gather',
      skill: 'mine',
      params: { block: block.name, count: Math.min(same.length, GATHER_MAX), radius: GATHER_RADIUS + 2, time_limit: ACTION_SECONDS.gather },
      seconds: ACTION_SECONDS.gather,
      why: `${same.length} ${block.name} ${Math.round(position.distanceTo(here))} m away`,
      keys: same.map((p) => `block:${p}`)
    }
  }
  return null
}

function pickupPick (bot, memory, run) {
  if (run.skill === 'collect_items' || run.skill === 'mine') return null // both pick up already
  const here = bot.entity.position
  const items = Object.values(bot.entities).filter((e) => e.name === 'item' && e.isValid &&
    e.position.distanceTo(here) <= PICKUP_RADIUS && !memory.skipping(`item:${e.id}`))
  if (!items.length) return null
  return {
    mode: 'pickup',
    skill: 'collect_items',
    params: { radius: PICKUP_RADIUS + 2, time_limit: ACTION_SECONDS.pickup },
    seconds: ACTION_SECONDS.pickup,
    why: `${items.length} item${items.length === 1 ? '' : 's'} on the ground`,
    keys: items.map((e) => `item:${e.id}`)
  }
}

// Whether a mode's action is worth a line to the user (Columbina is woken for it): ore
// gathered, a mob killed near the player she's with, or a mode that didn't work. Pickups, and
// fights only she was in, are just recorded.
export function worthSaying (result) {
  if (result.mode === 'pickup') return false
  if (result.status !== 'done') return true
  return result.mode === 'gather' || Boolean(result.near)
}

// One line for a mode's action, for her notices: "guard: killed 1 zombie near tearDrop12".
export function describeAction (result) {
  const data = result.data ?? {}
  let what
  if (result.mode === 'guard') {
    what = data.killed ? `killed ${data.killed} ${result.params.target}` : `fought a ${result.params.target} (${result.reason ?? result.status})`
    if (result.near) what += ` near ${result.near}`
  } else if (result.mode === 'gather') {
    what = data.collected ? `mined ${data.collected} ${result.params.block} on the way` : `couldn't mine the ${result.params.block} (${result.reason ?? result.status})`
  } else {
    const got = Object.entries(data.collected ?? {}).map(([name, n]) => `${name} x${n}`)
    what = got.length ? `picked up ${got.join(', ')}` : `couldn't pick up the items (${result.reason ?? result.status})`
  }
  return `${result.mode}: ${what}`
}
