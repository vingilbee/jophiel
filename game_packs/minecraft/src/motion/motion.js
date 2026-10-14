// The motion style layer: everything that turns her head or picks her pace goes through here.
//
// Mineflayer moves her in the direction her head faces, and the pathfinder snaps that direction
// to the next step every tick, with the head level. We take over bot.look (so the pathfinder,
// mining and skills only *request* a direction) and, once per physics tick, move the real head
// toward the request through stepAxis(): speed and acceleration limits, ease-out, a reaction delay
// for big changes, and a slow wobble. Because heading = walking direction, smooth turns also make
// her walk curves around corners. Pace: sprint in moods on long straight stretches, slow down for
// sharp turns, no bunny-hopping. Gaze: slightly down at the ground ahead while walking, glances at
// mobs and players, looks around when idle.
import { STYLES } from './styles.js'
import { DEG, angleDiff, directionTo, rayHitsBox, stepAxis, wobble } from './gaze.js'

const DT = 0.05 // one physics tick
const HEADING_FRESH_MS = 200 // the pathfinder asks every tick while walking
const BIG_AIM_CHANGE = 20 * DEG // a new aim this far away waits for the reaction delay
const BIG_HEADING_CHANGE = 50 * DEG
const HEADING_REACTION = [60, 120] // starting to walk: a short delay (none for turns mid-walk)
const SLOW_DOWN_TURN = 30 * DEG // no sprinting while turning more than this
const STOP_AND_TURN = 75 * DEG // turn on the spot first when the new direction is this far off
const SETTLE = 2.5 * DEG // a look request counts as reached within this
const WAIT_LIMIT_MS = 1500
const GLANCE_YAW_LIMIT = 12 * DEG // glancing while walking can't turn her far from her path

export function attachMotion (bot, { random = Math.random, styles = STYLES } = {}) {
  // Mineflayer adds look/setControlState (and the pathfinder its tick handler) only once the
  // connection allows plugins, so the layer installs itself when she's in the world.
  let rawLook = null
  let rawSetControlState = null
  const rand = (lo, hi) => lo + (hi - lo) * random()
  const phases = [0, 0, 0, 0].map(() => random() * Math.PI * 2)
  const t0 = performance.now()

  let style = styles.relaxed
  let styleName = 'relaxed'
  let idle = true
  let head = null // { yaw: {pos, vel}, pitch: {pos, vel} }
  let goal = null // the direction being pursued now (before wobble)
  let pendingSince = null // when a big change was first seen (reaction delay)
  let pendingDelay = 0
  let heading = null // { yaw, at } from the pathfinder
  let aim = null // { yaw, pitch, at } from bot.look / bot.lookAt (mining, skills)
  let tracking = null // an entity to keep looking at (fight)
  let glance = null // { entity, until } while walking
  let nextGlanceAt = 0
  let idleLook = null // { entity } or { yaw, pitch }
  let nextIdleAt = 0
  let stillSince = performance.now()
  let wasWalking = false
  let path = null
  let sprintMood = true
  let nextMoodAt = 0
  let requestedSprint = false
  const waiters = new Set()
  const stats = { ticks: 0, maxStepDeg: 0, snaps: 0, misses: 0 }

  function resync () {
    if (!bot.entity) return
    head = { yaw: { pos: bot.entity.yaw, vel: 0 }, pitch: { pos: bot.entity.pitch, vel: 0 } }
    goal = { yaw: bot.entity.yaw, pitch: bot.entity.pitch }
    pendingSince = null
  }

  const eye = () => bot.entity.position.offset(0, bot.entity.eyeHeight ?? 1.62, 0)
  const headOf = (e) => e.position.offset(0, (e.height ?? 1.8) * 0.85, 0)
  const walking = (now) => Boolean(bot.pathfinder?.isMoving?.() && heading && now - heading.at < HEADING_FRESH_MS)
  const remainingPath = () => {
    if (!path?.length) return 0
    const last = path[path.length - 1]
    return bot.entity.position.distanceTo(last)
  }

  // ---- requests --------------------------------------------------------------------------

  function waitFor (yaw, pitch, at) {
    return new Promise((resolve) => {
      waiters.add({ yaw, pitch, at, deadline: performance.now() + WAIT_LIMIT_MS, resolve })
    })
  }

  // The pathfinder asks with pitch exactly 0 and no force: that's "the way I'm walking".
  // Anything else (mining, skills, mineflayer's lookAt) is something to look at.
  const look = (yaw, pitch, force) => {
    const now = performance.now()
    if (pitch === 0 && !force) {
      heading = { yaw, at: now }
      return Promise.resolve()
    }
    aim = { yaw, pitch, at: now }
    return waitFor(yaw, pitch, now)
  }

  const setControlState = (control, state) => {
    if (control === 'sprint') {
      requestedSprint = state
      state = state && sprintAllowed(performance.now())
    }
    return rawSetControlState.call(bot, control, state)
  }

  function sprintAllowed (now) {
    if (!bot.pathfinder?.isMoving?.()) return true // a skill steering directly (backing off): let it
    if (heading && Math.abs(angleDiff(bot.entity.yaw, heading.yaw)) > SLOW_DOWN_TURN) return false
    if (style.sprint === 'always') return true
    if (style.sprint === 'rarely') return remainingPath() > 24
    if (now > nextMoodAt) {
      sprintMood = random() < 0.6
      nextMoodAt = now + rand(4000, 9000)
    }
    return sprintMood && remainingPath() > 10
  }

  // ---- choosing where to look ------------------------------------------------------------

  function nearbyLookables (radius) {
    const here = bot.entity.position
    return Object.values(bot.entities).filter((e) =>
      e !== bot.entity && e.position && ['player', 'hostile', 'animal', 'passive', 'mob'].includes(e.type) &&
      e.position.distanceTo(here) <= radius)
  }

  function pickIdleLook (now) {
    nextIdleAt = now + rand(2000, 6000)
    const roll = random()
    const players = nearbyLookables(16).filter((e) => e.type === 'player')
    const mobs = nearbyLookables(16).filter((e) => e.type !== 'player')
    if (roll < 0.4 && players.length) return { entity: players[Math.floor(random() * players.length)] }
    if (roll < 0.65 && mobs.length) return { entity: mobs[Math.floor(random() * mobs.length)] }
    return { yaw: bot.entity.yaw + rand(-100, 100) * DEG, pitch: rand(-15, 10) * DEG }
  }

  function desired (now) {
    const from = eye()
    const trackDir = tracking?.isValid ? directionTo(from, headOf(tracking)) : null

    if (walking(now)) {
      if (trackDir && Math.abs(angleDiff(heading.yaw, trackDir.yaw)) < 45 * DEG) return { ...trackDir, big: BIG_AIM_CHANGE }
      const walk = { yaw: heading.yaw, pitch: -style.walkPitch * DEG, big: BIG_HEADING_CHANGE, heading: true }
      if (style.glances) {
        if (glance && (now > glance.until || !glance.entity.isValid)) glance = null
        if (!glance && now > nextGlanceAt) {
          nextGlanceAt = now + rand(4000, 9000)
          const ahead = nearbyLookables(14).filter((e) =>
            Math.abs(angleDiff(heading.yaw, directionTo(from, headOf(e)).yaw)) < 60 * DEG)
          if (ahead.length) glance = { entity: ahead[Math.floor(random() * ahead.length)], until: now + rand(600, 1200) }
        }
        if (glance) {
          const dir = directionTo(from, headOf(glance.entity))
          walk.yaw += Math.max(-GLANCE_YAW_LIMIT, Math.min(GLANCE_YAW_LIMIT, angleDiff(heading.yaw, dir.yaw)))
          walk.pitch = dir.pitch
        }
      }
      return walk
    }

    if (trackDir) return { ...trackDir, big: BIG_AIM_CHANGE }
    if (aim && (!heading || aim.at >= heading.at)) return { yaw: aim.yaw, pitch: aim.pitch, big: BIG_AIM_CHANGE }
    if (idle && style.glances && now - stillSince > 1500) {
      if (!idleLook || now > nextIdleAt || (idleLook.entity && !idleLook.entity.isValid)) idleLook = pickIdleLook(now)
      const dir = idleLook.entity ? directionTo(from, headOf(idleLook.entity)) : idleLook
      return { yaw: dir.yaw, pitch: dir.pitch, big: BIG_AIM_CHANGE }
    }
    if (heading) return { yaw: heading.yaw, pitch: -style.walkPitch * DEG, big: BIG_HEADING_CHANGE, heading: true }
    return null
  }

  // ---- the tick --------------------------------------------------------------------------

  function onTick () {
    if (!bot.entity) return
    if (!head) resync()
    const now = performance.now()
    stats.ticks++
    if (walking(now) || !bot.entity.onGround) stillSince = now

    // Where to look, after the reaction delay for big changes.
    const want = desired(now)
    const isWalking = walking(now)
    if (want) {
      const jump = Math.max(Math.abs(angleDiff(goal.yaw, want.yaw)), Math.abs(want.pitch - goal.pitch))
      // Corners of a path she's already walking aren't news: no reaction delay for those.
      const followingPath = want.heading && wasWalking
      if (jump > want.big && !followingPath) {
        if (pendingSince === null) {
          pendingSince = now
          pendingDelay = want.heading ? rand(...HEADING_REACTION) : rand(...style.reaction)
        }
        if (now - pendingSince >= pendingDelay) {
          goal = { yaw: want.yaw, pitch: want.pitch }
          pendingSince = null
        }
      } else {
        goal = { yaw: want.yaw, pitch: want.pitch }
        pendingSince = null
      }
    }

    // Move the head toward it, wobbling slightly.
    wasWalking = isWalking
    const drift = wobble((now - t0) / 1000, style.wobble, phases)
    const before = bot.entity.yaw
    const turning = want?.heading && isWalking ? { ...style, ...style.walkTurn } : style
    head.yaw = stepAxis(head.yaw, goal.yaw + drift.yaw, DT, turning, { wrap: true })
    head.pitch = stepAxis(head.pitch, Math.max(-89 * DEG, Math.min(89 * DEG, goal.pitch + drift.pitch)), DT, style)
    rawLook.call(bot, head.yaw.pos, head.pitch.pos, false).catch(() => {})
    const stepDeg = Math.abs(angleDiff(before, bot.entity.yaw)) / DEG
    stats.maxStepDeg = Math.max(stats.maxStepDeg, stepDeg)
    if (stepDeg > 35) stats.snaps++

    // Pace: the pathfinder has set this tick's controls already; adjust them.
    if (walking(now)) {
      const turn = Math.abs(angleDiff(bot.entity.yaw, heading.yaw))
      if (turn > STOP_AND_TURN) rawSetControlState.call(bot, 'forward', false)
      // Sprint-jumping along flat ground is a bot tell; keep jumps for real obstacles.
      if (bot.controlState.jump && requestedSprint && !bot.entity.isInWater && style.sprint !== 'always') {
        rawSetControlState.call(bot, 'jump', false)
      }
      const sprint = requestedSprint && sprintAllowed(now)
      if (bot.controlState.sprint !== sprint) rawSetControlState.call(bot, 'sprint', sprint)
    }

    // Look requests that have been reached (or given up on, or replaced).
    for (const w of waiters) {
      const reached = Math.abs(angleDiff(bot.entity.yaw, w.yaw)) < SETTLE && Math.abs(bot.entity.pitch - w.pitch) < SETTLE
      if (reached || now > w.deadline || (aim && aim.at > w.at)) {
        waiters.delete(w)
        w.resolve()
      }
    }
  }

  const onPath = (results) => { path = results.path }
  let installed = false
  function install () {
    if (installed || !bot.look) return
    installed = true
    rawLook = bot.look
    rawSetControlState = bot.setControlState
    bot.look = look
    bot.setControlState = setControlState
    // Registered after the pathfinder's handler, so each tick we adjust what it just asked for.
    bot.on('physicsTick', onTick)
    bot.on('forcedMove', resync)
    bot.on('respawn', resync)
    bot.on('path_update', onPath)
    resync()
  }
  if (bot.entity && bot.look) install()
  else bot.once('spawn', install)

  return {
    get style () { return styleName },
    stats,
    setStyle (name) {
      style = styles[name] ?? styles.relaxed
      styleName = styles[name] ? name : 'relaxed'
    },
    setIdle (value) {
      idle = value
      idleLook = null
      glance = null
      if (!value) aim = null
    },
    // Keep looking at an entity (null to stop).
    track (entity) {
      tracking = entity
    },
    // Resolves once she's looking at what she's tracking (or after maxMs).
    async ready (maxMs = 400) {
      const end = performance.now() + maxMs
      while (performance.now() < end) {
        if (!tracking?.isValid) return false
        const dir = directionTo(eye(), headOf(tracking))
        if (Math.abs(angleDiff(bot.entity.yaw, dir.yaw)) < 6 * DEG && Math.abs(bot.entity.pitch - dir.pitch) < 8 * DEG) return true
        await new Promise((resolve) => setTimeout(resolve, 50))
      }
      return false
    },
    // Is her crosshair on the entity right now (within reach)?
    onTarget (entity, reach = 3.2) {
      const w = (entity.width ?? 0.6) / 2 + 0.1
      const h = entity.height ?? 1.8
      const p = entity.position
      const box = { min: { x: p.x - w, y: p.y - 0.1, z: p.z - w }, max: { x: p.x + w, y: p.y + h + 0.1, z: p.z + w } }
      return rayHitsBox(eye(), bot.entity.yaw, bot.entity.pitch, box, reach)
    },
    // A swing that goes wide now and then, so her aim is never perfect.
    fumble () {
      const miss = random() < style.missChance
      if (miss) stats.misses++
      return miss
    },
    detach () {
      bot.off('spawn', install)
      if (!installed) return
      bot.look = rawLook
      bot.setControlState = rawSetControlState
      bot.off('physicsTick', onTick)
      bot.off('forcedMove', resync)
      bot.off('respawn', resync)
      bot.off('path_update', onPath)
      for (const w of waiters) w.resolve()
      waiters.clear()
    }
  }
}
