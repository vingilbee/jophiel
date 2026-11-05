// Her trajectory: what her player went through, tick by tick, kept for learning
// (docs/learning-system/POST-AUDIT-01-fill-missing-experience-capture.md; implementation:
// docs/learning-system/POST-AUDIT-01-implementation.md).
//
// While she is connected, every client tick Mineflayer exposes (`physicsTick`) becomes a frame: position,
// velocity, facing, on-ground, the controls held, water/lava, health, food, saturation, air, the hotbar slot,
// what she is digging or using, dimension, game mode, every inventory slot, and the entities within
// ENTITY_RADIUS. Between ticks, events: each damage packet with the cause the server reported (its damage
// type from the server's own registry, the entity that caused it), the server's death message, every health
// update, respawns, block changes near her, chat, the notices she made, skill runs and what put them on hold,
// her programs, the commands the API received, the low-level actions skills called (dig, place, attack, equip,
// craft, pathfinder goals...), and what she perceived when her state was read (the shape of the place, which
// is cached: it is a perception only when it was read, never on every tick).
//
// Honest about coverage. Mineflayer emits no tick while she is dead, before her position arrives, in an
// unloaded chunk, or for ticks its catch-up cap drops when the process lags: each frame carries its own time,
// so such a stretch shows as time without frames, never as frames that didn't happen. A value the client
// doesn't have is null (never 0 health, never an empty world). Anything not listed in COVERAGE is not
// recorded: server simulation state, entities or blocks further away, sounds, particles.
//
// Storage. Frames are deltas against the frame before, packed into chunks of at most CHUNK_TICKS ticks (or
// CHUNK_MS of time), each starting from a checkpoint: the full state before its first frame. Every chunk is
// therefore self-contained (no chunk needs another to be read), so retention can drop any of them. Chunks are
// JSON, gzipped off the tick path, kept in a ring of at most BUFFER_BYTES for the backend to pull
// (GET /trajectory). A chunk dropped from the ring before the backend took it is reported as dropped, so a
// loss is visible. Each connection is its own stream (a reconnect is never continuous observation); each
// process start is its own run.
import { createHash, randomUUID } from 'node:crypto'
import { createRequire } from 'node:module'
import { performance } from 'node:perf_hooks'
import { promisify } from 'node:util'
import { gzip } from 'node:zlib'

const require = createRequire(import.meta.url)
const gz = promisify(gzip)

export const FORMAT = 1
export const CHUNK_TICKS = 100 // 5 s of ticks at 20 Hz
export const CHUNK_MS = 5000 // a chunk also closes after this long, ticks or not (she may be dead)
export const ENTITY_RADIUS = 16
export const BLOCK_RADIUS = 6
export const BUFFER_BYTES = 64 * 1024 * 1024
export const BUFFER_CHUNKS = 4000
export const MAX_RESPONSE_CHUNKS = 40
export const MAX_RESPONSE_BYTES = 8 * 1024 * 1024
const TEXT_MAX = 300
const DROPS_KEPT = 200

const CONTROLS = ['forward', 'back', 'left', 'right', 'jump', 'sprint', 'sneak']
// Bot methods recorded as her low-level actions: called (with what) and how each ended.
export const ACTIONS = ['dig', 'stopDigging', 'placeBlock', 'activateBlock', 'activateEntity', 'activateItem',
  'deactivateItem', 'attack', 'equip', 'unequip', 'toss', 'tossStack', 'consume', 'craft', 'setQuickBarSlot',
  'sleep', 'wake', 'openContainer', 'openBlock', 'openFurnace', 'closeWindow', 'fish', 'useOn', 'swingArm', 'chat']

export const COVERAGE = {
  ticks: 'client ticks Mineflayer exposes (physicsTick, 50 ms nominal); none while dead, before her position ' +
    'arrives, in an unloaded chunk, or for ticks dropped by its catch-up cap: each frame has its own time',
  frame: ['position', 'velocity', 'yaw/pitch', 'on_ground', 'controls', 'in_water/in_lava', 'health', 'food',
    'saturation', 'air', 'hotbar slot', 'digging', 'using item', 'dimension', 'game mode',
    'inventory slots (armor and off-hand included)', `entities within ${ENTITY_RADIUS} blocks`],
  events: ['damage (server damage type and causing entity)', 'death message (server)', 'health updates',
    'spawn/respawn', `block changes within ${BLOCK_RADIUS} blocks`, 'chat and system messages', 'notices',
    'skill runs, mode holds, programs', 'API commands', 'low-level actions', 'perceived surroundings when read'],
  not_recorded: ['server simulation state', `entities beyond ${ENTITY_RADIUS} blocks`,
    `block changes beyond ${BLOCK_RADIUS} blocks`, 'chunk loads', 'sounds', 'particles', 'other players\' inventories'],
  surroundings: 'cached by the pack; recorded only when her state was read (perceived events), with when'
}

const r = (value, places) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  const f = 10 ** places
  return Math.round(value * f) / f
}

const clockMs = () => performance.timeOrigin + performance.now()

function controlBits (bot) {
  const state = bot.controlState
  if (!state) return null
  let bits = 0
  CONTROLS.forEach((name, i) => { if (state[name]) bits |= 1 << i })
  return bits
}

function slotItem (item) {
  return item ? [item.name, item.count] : null
}

// What a value passed to an action is, briefly: a block, an entity, an item, a point, or itself.
export function describeArg (value, bot) {
  if (value === null || value === undefined) return null
  if (typeof value === 'number' || typeof value === 'boolean') return value
  if (typeof value === 'string') return value.slice(0, TEXT_MAX)
  if (typeof value === 'function') return 'fn'
  if (typeof value !== 'object') return String(value).slice(0, 60)
  if (value.position && typeof value.type === 'number' && value.name !== undefined && 'boundingBox' in value) {
    const p = value.position
    return { block: value.name, at: [p.x, p.y, p.z] }
  }
  if (typeof value.id === 'number' && value.position && typeof value.type === 'string') { // an entity
    const p = value.position
    return { entity: value.id, name: value.username ?? value.name ?? null, at: [r(p.x, 1), r(p.y, 1), r(p.z, 1)] }
  }
  if (typeof value.count === 'number' && typeof value.name === 'string') return { item: value.name, count: value.count }
  if (typeof value.x === 'number' && typeof value.y === 'number' && typeof value.z === 'number' && Object.keys(value).length <= 4) {
    return [r(value.x, 3), r(value.y, 3), r(value.z, 3)]
  }
  if (value.result && typeof value.result.id === 'number') { // a recipe
    const item = bot?.registry?.items?.[value.result.id]
    return { recipe: item?.name ?? value.result.id, count: value.result.count ?? null, table: Boolean(value.requiresTable) }
  }
  if (Array.isArray(value)) return value.slice(0, 8).map((v) => describeArg(v, bot))
  const out = {}
  for (const [k, v] of Object.entries(value).slice(0, 8)) {
    out[k] = typeof v === 'object' && v !== null ? (Array.isArray(v) ? `[${v.length}]` : '{}') : describeArg(v, bot)
  }
  return out
}

function describeEntity (bot, entity, rawId) {
  if (!rawId) return null
  if (!entity) return { id: rawId - 1, known: false }
  const p = entity.position
  const me = bot.entity?.position
  return {
    id: entity.id,
    name: entity.username ?? entity.name ?? null,
    type: entity.type ?? null,
    at: p ? [r(p.x, 1), r(p.y, 1), r(p.z, 1)] : null,
    distance: p && me ? r(p.distanceTo(me), 1) : null
  }
}

// The full state of one tick, in the compact form frames use.
export function readFrame (bot, { entityRadius = ENTITY_RADIUS } = {}) {
  const e = bot.entity
  const pos = e?.position
  const ok = pos && Number.isFinite(pos.x)
  const inv = {}
  for (const [i, item] of (bot.inventory?.slots ?? []).entries()) {
    if (item) inv[i] = slotItem(item)
  }
  const ent = {}
  if (ok) {
    for (const other of Object.values(bot.entities ?? {})) {
      if (!other || other === e || !other.position) continue
      const d = other.position.distanceTo(pos)
      if (!(d <= entityRadius)) continue
      const op = other.position
      ent[other.id] = [other.username ?? other.name ?? other.type ?? '?', r(op.x, 1), r(op.y, 1), r(op.z, 1)]
    }
  }
  const dig = bot.targetDigBlock
  return {
    p: ok ? [r(pos.x, 3), r(pos.y, 3), r(pos.z, 3)] : null,
    v: ok && e.velocity ? [r(e.velocity.x, 3), r(e.velocity.y, 3), r(e.velocity.z, 3)] : null,
    r: ok ? [r(e.yaw, 4), r(e.pitch, 4)] : null,
    g: ok ? Boolean(e.onGround) : null,
    c: controlBits(bot),
    m: ok ? (e.isInWater ? 1 : 0) | (e.isInLava ? 2 : 0) : null,
    h: r(bot.health, 2),
    f: typeof bot.food === 'number' ? bot.food : null,
    s: r(bot.foodSaturation, 2),
    o: typeof bot.oxygenLevel === 'number' ? bot.oxygenLevel : null,
    q: Number.isInteger(bot.quickBarSlot) ? bot.quickBarSlot : null,
    d: dig?.position ? [dig.name, dig.position.x, dig.position.y, dig.position.z] : null,
    u: bot.usingHeldItem ? 1 : 0,
    dim: bot.game?.dimension ?? null,
    gm: bot.game?.gameMode ?? null,
    inv,
    ent
  }
}

const MAPS = new Set(['inv', 'ent'])

function same (a, b) {
  if (a === b) return true
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false
  return true
}

// What changed from `prev` to `next`, or null: scalars and arrays replaced, the maps (inventory, entities)
// by key, a key gone as null.
export function frameDelta (prev, next) {
  let out = null
  for (const key of Object.keys(next)) {
    if (MAPS.has(key)) {
      const was = prev[key] ?? {}
      const now = next[key]
      let sub = null
      for (const k of Object.keys(now)) if (!same(was[k], now[k])) (sub ??= {})[k] = now[k]
      for (const k of Object.keys(was)) if (!(k in now)) (sub ??= {})[k] = null
      if (sub) (out ??= {})[key] = sub
    } else if (!same(prev[key], next[key])) {
      (out ??= {})[key] = next[key]
    }
  }
  return out
}

export function applyDelta (state, delta) {
  if (!delta) return state
  const out = { ...state }
  for (const [key, value] of Object.entries(delta)) {
    if (MAPS.has(key)) {
      const map = { ...(state[key] ?? {}) }
      for (const [k, v] of Object.entries(value)) {
        if (v === null) delete map[k]
        else map[k] = v
      }
      out[key] = map
    } else {
      out[key] = value
    }
  }
  return out
}

// Every tick's full state in a decoded chunk: [{ tick, at_ms, state }], unchanged ticks included.
export function expandChunk (chunk) {
  let state = chunk.checkpoint
  return chunk.frames.map(([offset, delta], i) => {
    state = applyDelta(state, delta ?? null)
    return { tick: chunk.first_tick + i, at_ms: chunk.started_ms + offset, state }
  })
}

function digest (value) {
  return createHash('sha256').update(JSON.stringify(value)).digest('hex').slice(0, 16)
}

function chatText (message) {
  try {
    const simplify = require('prismarine-nbt').simplify
    const m = message?.type ? simplify(message) : message
    const args = (m?.with ?? []).map((w) => (typeof w === 'string' ? w : w?.text ?? w?.translate ?? null))
    return { translate: m?.translate ?? null, with: args, text: typeof m?.text === 'string' ? m.text : null }
  } catch {
    return { translate: null, with: [], text: null, unreadable: true }
  }
}

export class Trajectory {
  constructor ({ runId = randomUUID(), chunkTicks = CHUNK_TICKS, chunkMs = CHUNK_MS, entityRadius = ENTITY_RADIUS,
    blockRadius = BLOCK_RADIUS, bufferBytes = BUFFER_BYTES, bufferChunks = BUFFER_CHUNKS, now = clockMs,
    timers = true } = {}) {
    this.runId = runId
    this.startedAt = new Date().toISOString()
    this.chunkTicks = chunkTicks
    this.chunkMs = chunkMs
    this.entityRadius = entityRadius
    this.blockRadius = blockRadius
    this.bufferBytes = bufferBytes
    this.bufferChunks = bufferChunks
    this.now = now
    this.timers = timers
    this.streams = [] // { id, index, started_at, ended_at, end_reason, next_seq, ticks, username, version }
    this.stream = null
    this.ring = [] // sealed chunks, oldest first: { meta, data (gzip Buffer), fetched }
    this.ringBytes = 0
    this.drops = [] // { stream, from_seq, to_seq, chunks, reason, first_tick, last_tick, started_at, ended_at }
    this.sealing = Promise.resolve()
    this.counts = { chunks: 0, bytes: 0, raw_bytes: 0, dropped_chunks: 0, ticks: 0, events: 0, encode_ms: 0 }
    this._detach = null
  }

  // -- streams -------------------------------------------------------------------------------------------

  // A new stream for this connection, and the listeners that record it. Call right after createBot, so the
  // server's registry (damage types) arrives while we listen. Returns detach().
  attach (bot, { runner = null, noticing = null, programs = null } = {}) {
    this.detach('replaced by a new connection')
    const stream = {
      id: randomUUID(), index: this.streams.length, started_at: new Date().toISOString(), ended_at: null,
      end_reason: null, next_seq: 0, ticks: 0, username: bot.username ?? null, version: bot.version ?? null
    }
    this.streams.push(stream)
    if (this.streams.length > 50) this.streams.splice(0, this.streams.length - 50)
    this.stream = stream
    this.bot = bot
    this.damageTypes = null
    this.prev = null
    this._open(this.now())
    const on = []
    const listen = (emitter, name, fn) => {
      if (!emitter?.on) return
      emitter.on(name, fn)
      on.push(() => emitter.off ? emitter.off(name, fn) : emitter.removeListener(name, fn))
    }
    const client = bot._client

    listen(bot, 'physicsTick', () => this.tick())
    listen(client, 'registry_data', (packet) => {
      if (packet?.id === 'minecraft:damage_type' && Array.isArray(packet.entries)) {
        this.damageTypes = packet.entries.map((entry) => entry.key)
        this.event('registry', { damage_types: this.damageTypes.length })
      }
    })
    listen(client, 'damage_event', (packet) => {
      if (!bot.entity || packet.entityId !== bot.entity.id) return
      const type = this.damageTypes?.[packet.sourceTypeId] ?? null
      this.event('damage', {
        type,
        type_id: packet.sourceTypeId,
        type_source: type ? 'server damage_type registry' : 'unknown: the registry did not name this id',
        cause: describeEntity(bot, bot.entities?.[packet.sourceCauseId - 1], packet.sourceCauseId),
        direct: describeEntity(bot, bot.entities?.[packet.sourceDirectId - 1], packet.sourceDirectId),
        source_at: packet.sourcePosition ? [r(packet.sourcePosition.x, 2), r(packet.sourcePosition.y, 2),
          r(packet.sourcePosition.z, 2)] : null,
        health_before: r(bot.health, 2),
        at: bot.entity.position ? [r(bot.entity.position.x, 2), r(bot.entity.position.y, 2), r(bot.entity.position.z, 2)] : null
      })
    })
    listen(client, 'death_combat_event', (packet) => {
      if (!bot.entity || packet.playerId !== bot.entity.id) return
      this.event('death_message', { ...chatText(packet.message), source: 'server death_combat_event' })
    })
    listen(bot, 'health', () => this.event('health', {
      h: r(bot.health, 2), f: typeof bot.food === 'number' ? bot.food : null, s: r(bot.foodSaturation, 2)
    }))
    listen(bot, 'death', () => this.event('death', { at: this.prev?.p ?? null }))
    listen(bot, 'respawn', () => this.event('respawn', {}))
    listen(bot, 'spawn', () => {
      stream.username = bot.username ?? stream.username
      stream.version = bot.version ?? stream.version
      this.event('spawn', { username: bot.username ?? null, version: bot.version ?? null })
    })
    listen(bot, 'kicked', (reason) => this.event('kicked', { reason: String(typeof reason === 'string' ? reason : JSON.stringify(reason)).slice(0, TEXT_MAX) }))
    listen(bot, 'blockUpdate', (oldBlock, newBlock) => {
      const block = newBlock ?? oldBlock
      const me = bot.entity?.position
      if (!block?.position || !me || block.position.distanceTo(me) > this.blockRadius) return
      if (oldBlock?.name === newBlock?.name) return
      const p = block.position
      this.event('block', { at: [p.x, p.y, p.z], from: oldBlock?.name ?? null, to: newBlock?.name ?? null })
    })
    listen(bot, 'messagestr', (text, position) => {
      if (position === 'game_info') return // the action bar, repeated every second
      this.event('message', { text: String(text).slice(0, TEXT_MAX), position: position ?? null })
    })
    listen(bot, 'end', (reason) => this.end(String(reason ?? 'disconnected')))
    listen(runner, 'start', (run) => this.event('skill_start', {
      run_id: run.id, skill: run.skill, params: run.params, source: run.source
    }))
    listen(runner, 'finish', (run) => this.event('skill_finish', {
      run_id: run.id, skill: run.skill, status: run.status, reason: run.reason, data: run.data, seconds: run.seconds
    }))
    listen(runner, 'mode', (m) => this.event('mode', {
      run_id: m.run_id, mode: m.mode, why: m.why, skill: m.skill, params: m.params, status: m.status, reason: m.reason
    }))
    listen(programs, 'start', (p) => this.event('program_start', {
      program_id: p.id, code: p.code, code_sha: p.code_sha, seconds: p.seconds ?? null, limits: p.limits ?? null
    }))
    listen(programs, 'finish', (p) => this.event('program_finish', {
      program_id: p.id, status: p.status, reason: p.reason, value: p.value ?? null, calls: p.calls, actions: p.actions,
      placed: p.placed, log: (p.log ?? []).slice(-50), seconds: p.seconds, body: p.body
    }))
    if (noticing?.onNotice) {
      const fn = (n) => this.event('notice', { seq: n.seq, kind: n.kind, urgency: n.urgency, text: n.text, data: n.data })
      noticing.onNotice(fn)
      on.push(() => noticing.offNotice?.(fn))
    }
    const timer = this.timers ? setInterval(() => this._maybeSeal(), 500) : null
    timer?.unref?.()
    this._detach = () => {
      for (const off of on) off()
      if (timer) clearInterval(timer)
    }
    return () => this.detach('detached')
  }

  // Bot methods wrapped once per bot: each call is an `act` event, how it ended an `act_end` (never changes
  // what the call does or returns). Mineflayer adds its methods (and the pathfinder) only on 'inject_allowed',
  // once connected; this listener comes after its own, so they are there by then (as src/kills.js does).
  instrument (bot) {
    if (bot.__trajectoryInstrumented) return
    bot.__trajectoryInstrumented = true
    if (typeof bot.dig === 'function') this._wrap(bot)
    else bot.once('inject_allowed', () => this._wrap(bot))
  }

  _wrap (bot) {
    let next = 0
    const self = this
    for (const name of ACTIONS) {
      const original = bot[name]
      if (typeof original !== 'function') continue
      bot[name] = function (...args) {
        const id = ++next
        self.event('act', { id, fn: name, args: args.slice(0, 4).map((a) => describeArg(a, bot)) })
        let result
        try {
          result = original.apply(this, args)
        } catch (err) {
          self.event('act_end', { id, fn: name, ok: false, error: String(err?.message ?? err).slice(0, TEXT_MAX) })
          throw err
        }
        if (result && typeof result.then === 'function') {
          result.then(
            () => self.event('act_end', { id, fn: name, ok: true }),
            (err) => self.event('act_end', { id, fn: name, ok: false, error: String(err?.message ?? err).slice(0, TEXT_MAX) })
          )
        } else {
          self.event('act_end', { id, fn: name, ok: true, sync: true })
        }
        return result
      }
    }
    const pf = bot.pathfinder
    if (pf && typeof pf.setGoal === 'function') {
      const setGoal = pf.setGoal
      pf.setGoal = function (goal, dynamic) {
        self.event('act', {
          id: ++next,
          fn: 'pathfinder.setGoal',
          args: [goal ? {
            goal: goal.constructor?.name ?? 'goal',
            ...(['x', 'y', 'z', 'rangeSq', 'range'].reduce((o, k) => (typeof goal[k] === 'number' ? { ...o, [k]: r(goal[k], 2) } : o), {})),
            ...(goal.entity ? { entity: goal.entity.id } : {})
          } : null, Boolean(dynamic)]
        })
        return setGoal.call(this, goal, dynamic)
      }
    }
  }

  detach (reason) {
    if (this._detach) {
      this._detach()
      this._detach = null
    }
    if (this.stream && !this.stream.ended_at) this.end(reason)
  }

  // The stream ends (a disconnect, a new connection, shutdown): its last chunk says so.
  end (reason) {
    const stream = this.stream
    if (!stream || stream.ended_at) return
    this.event('stream_end', { reason: String(reason).slice(0, TEXT_MAX) })
    stream.ended_at = new Date().toISOString()
    stream.end_reason = String(reason).slice(0, TEXT_MAX)
    this._seal(true)
    this.stream = null
    if (this._detach) { // its listeners and timer go with it
      this._detach()
      this._detach = null
    }
  }

  // -- recording -----------------------------------------------------------------------------------------

  _open (at) {
    this.chunk = {
      started_ms: at, checkpoint: this.prev ?? (this.bot ? readFrame(this.bot, { entityRadius: this.entityRadius }) : null),
      extras: this._extras(), first_tick: this.stream?.ticks ?? 0, frames: [], events: [], perceived: new Set(),
      tick_ms: 0, tick_ms_max: 0, kinds: {}
    }
  }

  _extras () {
    const bot = this.bot
    if (!bot) return {}
    return {
      username: bot.username ?? null, version: bot.version ?? null,
      time_of_day: bot.time?.timeOfDay ?? null, day: bot.time?.day ?? null,
      weather: bot.thunderState > 0 ? 'thunder' : bot.isRaining ? 'rain' : bot.isRaining === false ? 'clear' : null,
      xp_level: bot.experience?.level ?? null, alive: bot.isAlive ?? null
    }
  }

  tick () {
    if (!this.stream || !this.bot) return
    const started = performance.now()
    const at = this.now()
    const frame = readFrame(this.bot, { entityRadius: this.entityRadius })
    const base = this.prev ?? this.chunk.checkpoint
    const delta = base ? frameDelta(base, frame) : frame
    const offset = Math.round(at - this.chunk.started_ms)
    this.chunk.frames.push(delta ? [offset, delta] : [offset])
    if (!this.chunk.checkpoint) this.chunk.checkpoint = {}
    this.prev = frame
    this.stream.ticks += 1
    this.counts.ticks += 1
    const spent = performance.now() - started
    this.chunk.tick_ms += spent
    if (spent > this.chunk.tick_ms_max) this.chunk.tick_ms_max = spent
    if (this.chunk.frames.length >= this.chunkTicks) this._seal(false)
  }

  // One event, in order, at the current tick (the last tick seen; -1 before the first).
  event (kind, data = {}) {
    if (!this.stream) return
    const at = this.now()
    this.chunk.events.push([this.stream.ticks - 1, Math.round(at - this.chunk.started_ms), kind, data])
    this.chunk.kinds[kind] = (this.chunk.kinds[kind] ?? 0) + 1
    this.counts.events += 1
  }

  // Her state was read (GET /state): what she perceived of the place then. The shape of the place is
  // cached by the pack, so this is a perception only now, with its own time; content once per chunk, then
  // by digest.
  perceived (state) {
    if (!this.stream || !state) return
    const content = {
      surroundings: state.surroundings ?? null, terrain: state.terrain ?? null, blocks: state.blocks ?? null,
      heights: state.heights ?? null, players_out_of_sight: state.players_out_of_sight ?? null
    }
    const key = digest(content)
    const first = !this.chunk.perceived.has(key)
    this.chunk.perceived.add(key)
    this.event('perceived', { digest: key, read_at: state.at ?? null, ...(first ? { content } : { same_as_earlier: true }) })
  }

  // A command the API received and what it answered (dispatch, before any result).
  command (route, body, status, answer) {
    this.event('command', { route, body, status, answer })
  }

  _maybeSeal () {
    if (!this.stream || !this.chunk) return
    if (this.now() - this.chunk.started_ms < this.chunkMs) return
    if (!this.chunk.frames.length && !this.chunk.events.length) {
      this.chunk.started_ms = this.now() // nothing happened: the chunk starts again from now
      return
    }
    this._seal(false)
  }

  _seal (final) {
    const stream = this.stream
    const c = this.chunk
    if (!stream || !c) return
    const endedMs = this.now()
    if (!c.frames.length && !c.events.length && !final) return
    const seq = stream.next_seq++
    const body = {
      format: FORMAT, run: this.runId, stream: stream.id, stream_index: stream.index, seq,
      first_tick: c.first_tick, ticks: c.frames.length, started_ms: c.started_ms, ended_ms: endedMs,
      checkpoint: c.checkpoint, extras: c.extras, frames: c.frames, events: c.events,
      coverage: { ...COVERAGE, entity_radius: this.entityRadius, block_radius: this.blockRadius },
      final
    }
    const meta = {
      run: this.runId, stream: stream.id, stream_index: stream.index, seq, first_tick: c.first_tick,
      ticks: c.frames.length, events: c.events.length, kinds: c.kinds,
      started_at: new Date(c.started_ms).toISOString(), ended_at: new Date(endedMs).toISOString(), final,
      username: stream.username, version: stream.version, stream_started_at: stream.started_at,
      overhead: { tick_ms_total: r(c.tick_ms, 3), tick_ms_max: r(c.tick_ms_max, 3) }
    }
    this._open(endedMs)
    // Off the tick path: encode, then into the ring in seq order (each seal waits for the one before).
    this.sealing = this.sealing.then(async () => {
      const t0 = performance.now()
      const raw = Buffer.from(JSON.stringify(body))
      const data = await gz(raw)
      meta.overhead.encode_ms = r(performance.now() - t0, 3)
      meta.raw_bytes = raw.length
      meta.bytes = data.length
      meta.sha256 = createHash('sha256').update(data).digest('hex')
      this.counts.chunks += 1
      this.counts.bytes += data.length
      this.counts.raw_bytes += raw.length
      this.counts.encode_ms += meta.overhead.encode_ms
      this._push({ meta, data, fetched: false })
    }).catch(() => {
      this._drop([{ meta: { ...meta, bytes: 0 } }], 'encode_failed')
    })
  }

  _push (entry) {
    this.ring.push(entry)
    this.ringBytes += entry.data.length
    const evicted = []
    while (this.ring.length > this.bufferChunks || (this.ringBytes > this.bufferBytes && this.ring.length > 1)) {
      const old = this.ring.shift()
      this.ringBytes -= old.data.length
      if (!old.fetched) evicted.push(old)
    }
    if (evicted.length) this._drop(evicted, 'buffer_full')
  }

  _drop (entries, reason) {
    for (const { meta } of entries) {
      this.counts.dropped_chunks += 1
      const last = this.drops.at(-1)
      if (last && last.stream === meta.stream && last.reason === reason && last.to_seq === meta.seq - 1) {
        last.to_seq = meta.seq
        last.chunks += 1
        last.last_tick = meta.first_tick + meta.ticks - 1
        last.ended_at = meta.ended_at
      } else {
        this.drops.push({ stream: meta.stream, run: this.runId, from_seq: meta.seq, to_seq: meta.seq, chunks: 1, reason,
          first_tick: meta.first_tick, last_tick: meta.first_tick + meta.ticks - 1, started_at: meta.started_at,
          ended_at: meta.ended_at })
      }
    }
    if (this.drops.length > DROPS_KEPT) this.drops.splice(0, this.drops.length - DROPS_KEPT)
  }

  // Wait for chunks being encoded (tests, shutdown).
  async settle () {
    await this.sealing
  }

  // -- reading (GET /trajectory) ---------------------------------------------------------------------------

  // Chunks after a cursor, oldest first. cursor: { stream, seq } of the last chunk the reader holds, or null
  // for everything buffered. A cursor the ring no longer holds starts from the oldest chunk kept; chunks that
  // left the ring unread are in `dropped` (reported once).
  since (cursor = null, { maxChunks = MAX_RESPONSE_CHUNKS, maxBytes = MAX_RESPONSE_BYTES } = {}) {
    let start = 0
    if (cursor?.stream) {
      const at = this.ring.findIndex((e) => e.meta.stream === cursor.stream && e.meta.seq === Number(cursor.seq))
      if (at >= 0) {
        start = at + 1
      } else {
        // Not held any more (or never): the first chunk after it, if its stream is still here.
        const later = this.ring.findIndex((e) => e.meta.stream === cursor.stream && e.meta.seq > Number(cursor.seq))
        const streamIndex = this.streams.find((s) => s.id === cursor.stream)?.index
        if (later >= 0) {
          start = later
        } else if (streamIndex !== undefined) {
          const after = this.ring.findIndex((e) => e.meta.stream_index > streamIndex)
          start = after >= 0 ? after : this.ring.length
        }
      }
    }
    const chunks = []
    let bytes = 0
    let i = start
    for (; i < this.ring.length && chunks.length < maxChunks; i++) {
      const entry = this.ring[i]
      if (chunks.length && bytes + entry.data.length > maxBytes) break
      bytes += entry.data.length
      entry.fetched = true
      chunks.push({ ...entry.meta, encoding: 'json+gzip', data: entry.data.toString('base64') })
    }
    const dropped = this.drops
    this.drops = []
    return {
      run: this.runId, run_started_at: this.startedAt, format: FORMAT, chunks, dropped, more: i < this.ring.length,
      streams: this.streams.map(({ next_seq: nextSeq, ...s }) => ({ ...s, last_seq: nextSeq - 1 })),
      current: this.stream?.id ?? null, buffered: { chunks: this.ring.length, bytes: this.ringBytes },
      coverage: { ...COVERAGE, entity_radius: this.entityRadius, block_radius: this.blockRadius }
    }
  }

  stats () {
    return {
      run: this.runId, run_started_at: this.startedAt, format: FORMAT, recording: Boolean(this.stream),
      current: this.stream?.id ?? null, ...this.counts, buffered: { chunks: this.ring.length, bytes: this.ringBytes },
      pending_drops: this.drops.length, buffer_bytes: this.bufferBytes, chunk_ticks: this.chunkTicks,
      coverage: { ...COVERAGE, entity_radius: this.entityRadius, block_radius: this.blockRadius }
    }
  }
}
