// Watching the other players: what they're doing, as a player next to them would see it. Before
// this she knew only where the user was, so "here, I broke the bed", "I put the table down" or
// "wait, where are you going?" were news to her (2026-10-13 session).
//
// From what the server sends any nearby player: what they hold and wear, how they move, what
// they look at, blocks they break and place, mobs they hit and kill, getting hurt, sleeping,
// sneaking, items they throw and pick up. Not their inventory, menus or hunger.
//
// Two outputs: `watchedPlayers(bot)` for the state (each player's doing-now and the last minute),
// and notices for what's worth hearing about now (getting into bed, getting hit, throwing her
// something, placing or breaking a bed, table, furnace or chest). Cheap checks, no model.
import { bearingWord, compass, relativeBearing } from './state.js'

const TICKS_PER_CHECK = 20 // 1 s
const RECENT_MS = 60000 // "lately" covers the last minute
const RECENT_KEPT = 8
const MERGE_MS = 30000 // "broke stone" again within this long counts up instead of a new line
const REACH = 5.5 // a block changing this close to a player who just swung is theirs
const SWING_MS = 1500
const BREAKING_MS = 3000 // a break-progress mark this recent claims the block for its breaker
const THROWN = 1.8 // an item appearing this close to a player's eyes left their hand
// ...and still there this long after: /give spawns one that's gone at once (the pickup animation).
const THROWN_STAYS_MS = 500
const LOOK_RANGE = 6
const HURT_AGAIN_MS = 15000
const SPRINT = 4.5 // m/s, horizontal (walking is ~4.3, sprinting ~5.6)
const STILL = 0.3
// Blocks whose placing or breaking is news: things you build a camp around.
const NOTABLE = (name) => name.endsWith('_bed') || ['crafting_table', 'furnace', 'chest', 'barrel', 'smoker', 'blast_furnace'].includes(name)

const WATCHED = new WeakMap()

const label = (name) => name.replace(/_/g, ' ')

function itemName (item) {
  return item ? item.name : null
}

// The block a player is looking at (head yaw/pitch), or null: the block itself, with its position.
export function lookedAtBlock (bot, entity, range = LOOK_RANGE) {
  const yaw = entity.headYaw ?? entity.yaw
  const pitch = entity.pitch ?? 0
  const dir = { x: -Math.sin(yaw) * Math.cos(pitch), y: Math.sin(pitch), z: -Math.cos(yaw) * Math.cos(pitch) }
  const eye = entity.position.offset(0, entity.eyeHeight ?? 1.62, 0)
  const hit = bot.world.raycast(eye, eye.offset(dir.x, dir.y, dir.z).minus(eye), range)
  return hit && hit.name !== 'air' ? hit : null
}

// What a player is looking at, for her state: { name, distance }, or null.
export function lookingAt (bot, entity, range = LOOK_RANGE) {
  const hit = lookedAtBlock(bot, entity, range)
  if (!hit) return null
  const eye = entity.position.offset(0, entity.eyeHeight ?? 1.62, 0)
  return { name: hit.name, distance: Math.round(hit.position.offset(0.5, 0.5, 0.5).distanceTo(eye)) }
}

// The entity's synced metadata value by name (pose, health...), or undefined.
function meta (bot, entity, key) {
  const keys = bot.registry.entitiesByName[entity.name ?? 'player']?.metadataKeys
  const index = keys ? keys.indexOf(key) : -1
  return index >= 0 ? entity.metadata?.[index] : undefined
}

// Mineflayer reports pose as a number (or a name, by version).
const POSES = { 0: 'standing', 1: 'gliding', 2: 'sleeping', 3: 'swimming', 5: 'sneaking' }
function poseOf (bot, entity) {
  const raw = meta(bot, entity, 'pose')
  const pose = typeof raw === 'number' ? POSES[raw] : typeof raw === 'string' ? raw.toLowerCase() : null
  return pose === 'crouching' ? 'sneaking' : pose
}

export function attachWatching (bot, { report = () => {}, now = Date.now } = {}) {
  const players = new Map() // username -> watch record
  WATCHED.set(bot, { players, now })
  let ticks = 0

  function record (username) {
    if (!players.has(username)) {
      players.set(username, { recent: [], last: null, speed: 0, heading: null, closer: 0, swungAt: 0, pose: null, hurtAt: 0 })
    }
    return players.get(username)
  }

  const playerOf = (entity) => entity?.type === 'player' && entity !== bot.entity && entity.username ? entity.username : null
  const others = () => Object.values(bot.players)
    .filter((p) => p.username !== bot.username && p.entity?.position).map((p) => p.entity)

  // Something a player did: into their last minute, counted up if it repeats.
  function did (username, text, data = {}) {
    const watch = record(username)
    const at = now()
    const previous = watch.recent.at(-1)
    if (previous && previous.text === text && at - previous.at <= MERGE_MS) {
      previous.count += 1
      previous.at = at
    } else {
      watch.recent.push({ text, count: 1, at, ...data })
      if (watch.recent.length > RECENT_KEPT) watch.recent.splice(0, watch.recent.length - RECENT_KEPT)
    }
  }

  // Where something is, from her: "3 m north-west (ahead-left)".
  function fromHer (position) {
    const self = bot.entity
    const dx = position.x - self.position.x
    const dz = position.z - self.position.z
    const distance = Math.round(Math.hypot(dx, position.y - self.position.y, dz))
    return { distance, text: `${distance} m ${compass(Math.atan2(-dx, -dz))} (${bearingWord(relativeBearing(self.yaw, dx, dz))})` }
  }

  // The player who most likely changed the block at `position`: whoever was seen breaking it,
  // else the nearest player within reach who just swung. Never when she's closer (her own work).
  const breaking = new Map() // block position -> { username, at }
  function changedBy (position, { breakingOnly = false } = {}) {
    const key = position.toString()
    const marked = breaking.get(key)
    if (marked && now() - marked.at <= BREAKING_MS) return marked.username
    if (breakingOnly) return null
    const center = position.offset(0.5, 0.5, 0.5)
    let best = null
    for (const entity of others()) {
      const d = entity.position.offset(0, 1.62, 0).distanceTo(center)
      if (d > REACH || now() - record(entity.username).swungAt > SWING_MS) continue
      if (!best || d < best.d) best = { username: entity.username, d }
    }
    if (!best || (bot.entity && bot.entity.position.offset(0, 1.62, 0).distanceTo(center) < best.d)) return null
    return best.username
  }

  const onSwing = (entity) => {
    const username = playerOf(entity)
    if (username) record(username).swungAt = now()
  }

  const onBreaking = (block, stage, entity) => {
    const username = playerOf(entity)
    if (username && block) breaking.set(block.position.toString(), { username, at: now() })
  }

  const onBlockUpdate = (old, block) => {
    if (!old || !block || !bot.entity) return
    const wasAir = old.name === 'air' || old.name === 'cave_air' || old.boundingBox === 'empty'
    const isAir = block.name === 'air' || block.name === 'cave_air'
    if (wasAir === isAir || block.name === 'water' || block.name === 'lava' || block.name === 'fire') return
    const position = block.position ?? old.position
    if (!position) return
    if (isAir) {
      const username = changedBy(position)
      if (!username) return
      did(username, `broke ${label(old.name)}`)
      if (NOTABLE(old.name)) {
        report('player_activity', 'info', `${username} broke a ${label(old.name)}, ${fromHer(position).text}`,
          { player: username, action: 'broke', block: old.name })
      }
    } else {
      const username = changedBy(position)
      if (!username) return
      did(username, `placed ${label(block.name)}`)
      if (NOTABLE(block.name)) {
        report('player_activity', 'info', `${username} placed a ${label(block.name)}, ${fromHer(position).text}`,
          { player: username, action: 'placed', block: block.name })
      }
    }
    breaking.delete(position.toString())
  }

  const onHurt = (entity, source) => {
    const victim = playerOf(entity)
    const attacker = playerOf(source)
    if (attacker && !victim && entity !== bot.entity) {
      did(attacker, `hit ${label(entity.name ?? 'something')}`)
      return
    }
    if (!victim) return
    const by = source && source !== entity ? (source === bot.entity ? 'you' : playerOf(source) ?? label(source.name ?? 'something')) : null
    did(victim, by ? `got hit by ${by}` : 'took damage')
    const watch = record(victim)
    if (now() - watch.hurtAt < HURT_AGAIN_MS) return
    watch.hurtAt = now()
    const health = meta(bot, entity, 'health')
    const hearts = typeof health === 'number' && health > 0 ? ` (${Math.round(health) / 2} of 10 hearts left)` : ''
    report('player_hurt', 'heads_up', `${victim} ${by ? `got hit by ${by === 'you' ? 'you' : `a ${by}`}` : 'took damage'}${hearts}`,
      { player: victim, by, health: typeof health === 'number' ? health : null })
  }

  // A mob dying right after a player hit it: they killed it.
  const onDead = (entity) => {
    if (!entity || entity.type === 'player') return
    for (const [username, watch] of players) {
      const last = watch.recent.at(-1)
      if (last?.text === `hit ${label(entity.name ?? '')}` && now() - last.at <= 3000) {
        did(username, `killed ${label(entity.name)}`)
        report('player_activity', 'info', `${username} killed a ${label(entity.name)}`, { player: username, action: 'killed', mob: entity.name })
        return
      }
    }
  }

  // Items appearing at a player's hands were thrown; news when they come her way. Mineflayer
  // reports an item again on each update, so each one is looked at once, and only counted if it's
  // still there a moment later (see THROWN_STAYS_MS).
  const dropsSeen = new Set() // item entity ids
  const thrown = new Map() // item entity id -> { entity, username, at }
  const onItemDrop = (entity) => {
    if (entity.id === undefined || dropsSeen.has(entity.id)) return
    dropsSeen.add(entity.id)
    if (dropsSeen.size > 500) dropsSeen.delete(dropsSeen.values().next().value)
    for (const player of others()) {
      if (player.position.offset(0, player.eyeHeight ?? 1.62, 0).distanceTo(entity.position) > THROWN) continue
      thrown.set(entity.id, { entity, username: player.username, at: now() })
      return
    }
  }
  function settleThrows () {
    for (const [id, toss] of thrown) {
      if (now() - toss.at < THROWN_STAYS_MS) continue
      thrown.delete(id)
      const item = toss.entity.getDroppedItem?.()
      if (!item || bot.entities[id] !== toss.entity || toss.entity.isValid === false) continue
      did(toss.username, `threw ${label(item.name)} x${item.count}`)
      if (bot.entity && bot.entity.position.distanceTo(toss.entity.position) <= 8) {
        report('player_activity', 'heads_up', `${toss.username} threw ${label(item.name)} x${item.count} your way`,
          { player: toss.username, action: 'threw', item: item.name, count: item.count })
      }
    }
  }

  const onCollect = (collector, collected) => {
    const username = playerOf(collector)
    const item = collected?.getDroppedItem?.()
    if (username && item) did(username, `picked up ${label(item.name)} x${item.count}`)
  }

  // Each second: movement, pose changes (getting into bed, waking up), throws settling.
  function check () {
    const at = now()
    settleThrows()
    for (const entity of others()) {
      const watch = record(entity.username)
      const p = entity.position
      if (watch.last) {
        const seconds = Math.max(0.05, (at - watch.last.at) / 1000)
        const dx = p.x - watch.last.x
        const dz = p.z - watch.last.z
        watch.speed = Math.hypot(dx, dz) / seconds
        watch.heading = watch.speed > STILL ? compass(Math.atan2(-dx, -dz)) : null
        if (bot.entity) {
          const before = Math.hypot(watch.last.x - bot.entity.position.x, watch.last.z - bot.entity.position.z)
          const after = Math.hypot(p.x - bot.entity.position.x, p.z - bot.entity.position.z)
          watch.closer = before - after
        }
      }
      watch.last = { x: p.x, y: p.y, z: p.z, at }
      const pose = poseOf(bot, entity)
      if (pose !== watch.pose) {
        if (pose === 'sleeping') {
          did(entity.username, 'got into bed')
          report('player_sleeping', 'heads_up', `${entity.username} got into bed`, { player: entity.username, action: 'sleeping' })
        } else if (watch.pose === 'sleeping') {
          did(entity.username, 'got out of bed')
        }
        watch.pose = pose
      }
    }
    for (const [key, mark] of breaking) if (at - mark.at > BREAKING_MS) breaking.delete(key)
  }
  const onTick = () => {
    if (++ticks % TICKS_PER_CHECK) return
    try {
      check()
    } catch {
      // a half-loaded world: next second
    }
  }

  bot.on('physicsTick', onTick)
  bot.on('entitySwingArm', onSwing)
  bot.on('blockBreakProgressObserved', onBreaking)
  bot.on('blockUpdate', onBlockUpdate)
  bot.on('entityHurt', onHurt)
  bot.on('entityDead', onDead)
  bot.on('itemDrop', onItemDrop)
  bot.on('playerCollect', onCollect)

  return {
    check,
    detach () {
      bot.off('physicsTick', onTick)
      bot.off('entitySwingArm', onSwing)
      bot.off('blockBreakProgressObserved', onBreaking)
      bot.off('blockUpdate', onBlockUpdate)
      bot.off('entityHurt', onHurt)
      bot.off('entityDead', onDead)
      bot.off('itemDrop', onItemDrop)
      bot.off('playerCollect', onCollect)
      WATCHED.delete(bot)
    }
  }
}

// Which way a player faces, on the compass and from her side: "this way" from the user means
// that way (seen live: "this direction is the cave", four times, and she couldn't tell which).
export function facingOf (bot, entity) {
  const yaw = entity.headYaw ?? entity.yaw
  if (typeof yaw !== 'number' || !bot.entity) return null
  return { compass: compass(yaw), side: bearingWord(relativeBearing(bot.entity.yaw, -Math.sin(yaw), -Math.cos(yaw))) }
}

// The players she can see and what they're doing, for the state: holding, wearing, moving,
// pose, looking at, and the last minute. [] when watching isn't attached.
export function watchedPlayers (bot) {
  const watched = WATCHED.get(bot)
  if (!watched || !bot.entity) return []
  const at = watched.now()
  const out = []
  for (const player of Object.values(bot.players)) {
    const entity = player.entity
    if (player.username === bot.username || !entity?.position) continue
    const watch = watched.players.get(player.username)
    const armor = [5, 4, 3, 2].map((slot) => itemName(entity.equipment?.[slot])).filter(Boolean)
    const health = meta(bot, entity, 'health')
    let moving = 'standing still'
    if (watch && watch.speed > STILL) {
      moving = `${watch.speed >= SPRINT ? 'running' : 'walking'} ${watch.heading}` +
        (watch.closer > 0.5 ? ', towards you' : watch.closer < -0.5 ? ', away from you' : '')
    }
    let looking = null
    try {
      looking = lookingAt(bot, entity)
    } catch {
      // chunk not loaded where they look
    }
    out.push({
      name: player.username,
      holding: itemName(entity.equipment?.[0] ?? entity.heldItem),
      armor,
      health: typeof health === 'number' && health > 0 ? Math.round(health * 10) / 10 : null,
      moving,
      pose: poseOf(bot, entity) ?? 'standing',
      facing: facingOf(bot, entity),
      looking_at: looking,
      recent: (watch?.recent ?? []).filter((r) => at - r.at <= RECENT_MS)
        .map((r) => ({ text: r.count > 1 ? `${r.text} (x${r.count})` : r.text, seconds_ago: Math.round((at - r.at) / 1000) }))
    })
  }
  return out
}
