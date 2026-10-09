// The noticing layer: what she'd mention if she were playing next to you. Runs all the time,
// whatever skill is running, with cheap checks and no model. Each thing is noticed once (per
// entity, per place, per day...), described in a few words with where it is, and kept in a short
// list that Columbina reads in phase 3 (GET /notices). Places also go into her world memory.
//
// She only notices what she could see: blocks and mobs need a clear line of sight (findBlock
// alone would see diamonds through stone), except lava right next to her.
import { EventEmitter } from 'node:events'

import { blockIds, isHostile } from './skills/common.js'
import { bearingWord, compass, dayPhase, relativeBearing } from './state.js'

const KEEP = 200
const TICKS_PER_CHECK = 20 // 1 s
const CHECKS_PER_SCAN = 3 // block scans every 3 s
const QUIET_AFTER_SPAWN_MS = 5000 // the player list arrives at join: those aren't news
const HOSTILE_RADIUS = 16
const HOSTILE_AGAIN_MS = 60000 // the same mob is news again after this long
const URGENT_CLOSE = 6
const CREEPER_URGENT = 10

// Places worth knowing. announce: false = remember quietly (water is everywhere).
const PLACES = [
  { kind: 'village', blocks: ['bell'], mobs: ['villager'], radius: 96, spacing: 160, urgency: 'heads_up' },
  { kind: 'spawner', blocks: ['spawner'], radius: 16, spacing: 24, urgency: 'heads_up' },
  { kind: 'ore', blocks: ['diamond_ore', 'deepslate_diamond_ore', 'emerald_ore', 'deepslate_emerald_ore', 'ancient_debris', 'gold_ore', 'deepslate_gold_ore'], radius: 12, spacing: 12, urgency: 'info', byName: true },
  { kind: 'cave', blocks: ['cave_air'], radius: 10, spacing: 64, announce: false },
  { kind: 'water', blocks: ['water'], radius: 24, spacing: 48, announce: false },
  { kind: 'lava', blocks: ['lava'], radius: 8, spacing: 24, urgency: 'heads_up', nearAnyway: 3 }
]

export function attachNoticing (bot, { memory }) {
  const events = new EventEmitter()
  const notices = []
  const last = new Map() // dedupe key -> time
  let seq = 0
  let ticks = 0
  let checks = 0
  let spawnedAt = Date.now()
  let lastPhase = null
  let lastWeather = null
  let lastHealth = Number.isFinite(bot.health) ? bot.health : null // from the previous health update
  let windowHealth = null // health at the start of the current 3 s window
  let windowAt = 0

  const eye = () => bot.entity.position.offset(0, bot.entity.eyeHeight ?? 1.62, 0)

  function where (position) {
    const self = bot.entity
    const dx = position.x - self.position.x
    const dz = position.z - self.position.z
    const dy = position.y - self.position.y
    const distance = Math.round(Math.hypot(dx, dy, dz))
    const side = bearingWord(relativeBearing(self.yaw, dx, dz))
    const heading = compass(Math.atan2(-dx, -dz))
    const vertical = Math.abs(dy) >= 3 ? `, ${Math.abs(Math.round(dy))} m ${dy > 0 ? 'up' : 'down'}` : ''
    return {
      text: `${distance} m ${heading} (${side}${vertical})`,
      data: { distance, direction: heading, side, x: Math.round(position.x), y: Math.round(position.y), z: Math.round(position.z) }
    }
  }

  function add (kind, urgency, text, data = {}, key = null, againMs = Infinity) {
    const now = Date.now()
    if (key) {
      const seen = last.get(key)
      if (seen !== undefined && now - seen < againMs) return null
      last.set(key, now)
    }
    const notice = { seq: ++seq, at: new Date(now).toISOString(), kind, urgency, text, data }
    notices.push(notice)
    if (notices.length > KEEP) notices.splice(0, notices.length - KEEP)
    events.emit('notice', notice)
    return notice
  }

  // Can she see this point from her eyes? (The first block on the way is the thing itself.)
  function canSee (target) {
    const from = eye()
    const delta = target.minus(from)
    const distance = delta.norm()
    if (distance < 1) return true
    const hit = bot.world.raycast(from, delta.normalize(), distance)
    return !hit || hit.position.distanceTo(target) < 1
  }

  function scanPlaces () {
    for (const place of PLACES) {
      const ids = place.blocks.flatMap((b) => blockIds(bot, b))
      const candidates = bot.findBlocks({ matching: ids, maxDistance: place.radius, count: 6 })
        .map((p) => bot.blockAt(p)).filter(Boolean)
      let found = candidates.find((b) =>
        (place.nearAnyway && b.position.distanceTo(bot.entity.position) <= place.nearAnyway) || bot.canSeeBlock(b))
      let label = found?.name
      if (!found && place.mobs) {
        const mob = Object.values(bot.entities).find((e) => place.mobs.includes(e.name) &&
          e.position.distanceTo(bot.entity.position) <= place.radius && canSee(e.position.offset(0, e.height * 0.8, 0)))
        if (mob) {
          found = { position: mob.position }
          label = mob.name
        }
      }
      if (!found) continue
      const remembered = memory.note(place.kind, found.position, { spacing: place.spacing, name: place.byName ? label : null })
      if (!remembered || place.announce === false) continue
      const at = where(found.position)
      const what = place.kind === 'village' ? `a village (saw a ${label})` : place.kind === 'ore' ? label.replace(/_/g, ' ') : place.kind
      add(place.kind, place.urgency, `${what}, ${at.text}`, { ...at.data, name: label })
    }
  }

  function checkMobs () {
    const here = bot.entity.position
    for (const e of Object.values(bot.entities)) {
      if (e === bot.entity || !isHostile(e)) continue
      const distance = e.position.distanceTo(here)
      if (distance > HOSTILE_RADIUS) continue
      const close = distance <= URGENT_CLOSE || (e.name === 'creeper' && distance <= CREEPER_URGENT)
      if (!close && !canSee(e.position.offset(0, (e.height ?? 1.8) * 0.8, 0))) continue
      const at = where(e.position)
      add('hostile', close ? 'urgent' : 'heads_up', `${e.name.replace(/_/g, ' ')} ${at.text}`,
        { ...at.data, mob: e.name, entity_id: e.id }, `hostile:${e.id}`, HOSTILE_AGAIN_MS)
    }
  }

  function checkWorld () {
    const phase = dayPhase(bot.time.timeOfDay)
    if (lastPhase && phase !== lastPhase) {
      const day = bot.time.day
      if (phase === 'sunset') add('night_coming', 'heads_up', 'the sun is setting', { day }, `sunset:${day}`)
      if (phase === 'night') add('night', 'heads_up', 'it\'s night: monsters spawn in the dark', { day }, `night:${day}`)
      if (phase === 'day' && lastPhase !== 'day') add('morning', 'info', 'morning', { day }, `morning:${day}`)
    }
    lastPhase = phase
    const weather = bot.thunderState > 0 ? 'thunder' : bot.isRaining ? 'rain' : 'clear'
    if (lastWeather && weather !== lastWeather) {
      add('weather', weather === 'thunder' ? 'heads_up' : 'info',
        weather === 'clear' ? 'the rain stopped' : weather === 'thunder' ? 'a thunderstorm' : 'it started raining', { weather })
    }
    lastWeather = weather
  }

  function onTick () {
    if (!bot.entity) return
    if (++ticks % TICKS_PER_CHECK !== 0) return
    checks++
    checkMobs()
    checkWorld()
    if (checks % CHECKS_PER_SCAN === 0) scanPlaces()
  }

  function onHealth () {
    const now = Date.now()
    if (now - windowAt > 3000) {
      windowHealth = lastHealth ?? bot.health
      windowAt = now
    }
    lastHealth = bot.health
    const dropped = windowHealth - bot.health
    if (dropped >= 4 || (dropped > 0 && bot.health <= 8)) {
      const urgent = bot.health <= 8
      add('hurt', urgent ? 'urgent' : 'heads_up', `took damage: health ${Math.round(bot.health)}/20`,
        { health: Math.round(bot.health), lost: Math.round(dropped) }, 'hurt', 15000)
      windowHealth = bot.health
      windowAt = now
    }
    if (bot.food <= 6) add('hungry', 'heads_up', `hungry: food ${bot.food}/20`, { food: bot.food }, 'hungry', 300000)
  }

  const onDeath = () => add('died', 'urgent', 'died', {}, 'died', 5000)
  const onSpawn = () => {
    spawnedAt = Date.now()
    lastPhase = null
    lastWeather = null
  }
  const onJoined = (player) => {
    if (player.username === bot.username || Date.now() - spawnedAt < QUIET_AFTER_SPAWN_MS) return
    add('player_joined', 'info', `${player.username} joined`, { player: player.username }, `joined:${player.username}`, 10000)
  }
  const onLeft = (player) => {
    if (player.username === bot.username) return
    add('player_left', 'info', `${player.username} left`, { player: player.username }, `left:${player.username}`, 10000)
  }

  bot.on('physicsTick', onTick)
  bot.on('health', onHealth)
  bot.on('death', onDeath)
  bot.on('spawn', onSpawn)
  bot.on('playerJoined', onJoined)
  bot.on('playerLeft', onLeft)

  return {
    // Notices after `since` (a seq), oldest first.
    since (since = 0) {
      return notices.filter((n) => n.seq > since)
    },
    // Resolves with new notices after `since`, waiting up to ms for at least one.
    wait (since, ms) {
      const ready = this.since(since)
      if (ready.length || ms <= 0) return Promise.resolve(ready)
      return new Promise((resolve) => {
        const done = () => {
          clearTimeout(timer)
          events.off('notice', done)
          resolve(this.since(since))
        }
        const timer = setTimeout(done, ms)
        events.on('notice', done)
      })
    },
    get seq () { return seq },
    onNotice (listener) {
      events.on('notice', listener)
    },
    detach () {
      bot.off('physicsTick', onTick)
      bot.off('health', onHealth)
      bot.off('death', onDeath)
      bot.off('spawn', onSpawn)
      bot.off('playerJoined', onJoined)
      bot.off('playerLeft', onLeft)
    }
  }
}
