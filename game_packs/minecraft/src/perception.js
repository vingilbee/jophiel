// Her view, kept up to date: an observation (src/view.js) from her eyes whenever she has moved or
// turned enough, or it's old; what it shows (src/scene.js) matched into her landmarks
// (src/landmarks.js); the blocks it hit handed to her block memory (src/sight.js), so a block counts
// as seen when a ray of her view reached it. It also runs on its own while she's in the world, every
// EVERY_MS, so walking reveals places even between state reads, and new landmarks become notices.
//
// The local safety senses (src/surroundings.js: the ground right around her, walls, drops; the open
// space she's in) stay as they are: this is the far, forward view, a separate source.
import { botCamera, botWorld, END, observe } from './view.js'
import { describeFeature, readScene } from './scene.js'
import { rememberSeen } from './sight.js'
import { bearingWord, compass, relativeBearing } from './state.js'

const EVERY_MS = 1500
const STALE_MS = 2000
const MOVED = 0.75 // blocks
const TURNED = 4 * Math.PI / 180
// The dimension her landmarks are kept under (the same everywhere they're looked up).
export const dimensionOf = (bot) => bot.game?.dimension ?? 'overworld'

const REMEMBER_HITS = /(_ore|_log|_bed)$|^(water|lava|crafting_table|furnace|chest|barrel|ancient_debris|spawner|bell|obsidian)$/

function angleDiff (a, b) {
  const d = ((a - b) % (2 * Math.PI) + 3 * Math.PI) % (2 * Math.PI) - Math.PI
  return Math.abs(d)
}

export function attachPerception (bot, { memory, noticing = null, options = {} } = {}) {
  let last = null // { obs, scene, landmarks: {visible, added}, camera, at }
  let timer = null
  let slowSince = 0

  function current (force = false) {
    if (!bot.entity?.position) return null
    const cam = botCamera(bot)
    if (!force && last) {
      const moved = Math.hypot(cam.eye.x - last.camera.eye.x, cam.eye.y - last.camera.eye.y, cam.eye.z - last.camera.eye.z)
      const fresh = Date.now() - last.at < STALE_MS
      if (fresh && moved < MOVED && angleDiff(cam.yaw, last.camera.yaw) < TURNED && Math.abs(cam.pitch - last.camera.pitch) < TURNED) {
        return last
      }
    }
    return look(cam)
  }

  // A view from the camera given (her own eyes unless a skill turns her first).
  function look (cam = botCamera(bot)) {
    const world = botWorld(bot)
    const obs = observe(world, cam.eye, cam.yaw, cam.pitch, options)
    const feet = bot.entity.position
    const scene = readScene(obs, world, feet)
    const dimension = dimensionOf(bot)
    const marks = memory?.landmarks?.update(scene, { dimension, observation: obs.id, world }) ?? { visible: [], added: [] }
    feedSight(obs)
    last = { obs, scene, marks, camera: cam, at: Date.now(), dimension }
    if (noticing && marks.added.length) announce(marks.added)
    return last
  }

  function feedSight (obs) {
    const { store, names } = obs
    for (let k = 0; k < store.n; k++) {
      if (store.end[k] !== END.SURFACE || store.name[k] < 0) continue
      const name = names[store.name[k]]
      if (REMEMBER_HITS.test(name)) rememberSeen(bot, store.hit[k * 3], store.hit[k * 3 + 1], store.hit[k * 3 + 2], name)
    }
  }

  function announce (ids) {
    for (const id of ids) {
      const lm = memory.landmarks.get(id)
      if (!lm || lm.confidence !== 'clear') continue
      if (lm.kind !== 'opening' && lm.kind !== 'structure' && lm.kind !== 'lava') continue
      const f = featureView(lm)
      const urgent = lm.kind === 'lava' && f.distance <= 8
      noticing.report('landmark', urgent ? 'heads_up' : 'info', `${id} came into view: ${describeFeature(f)}`,
        { id, kind: lm.kind, x: lm.x, y: lm.y, z: lm.z, distance: f.distance })
    }
  }

  // A landmark as a feature seen from where she is now (its facts, her distance and side). The ground
  // between was measured from where she stood then: kept only while it's in view from here.
  function featureView (lm, { inView = true } = {}) {
    const p = bot.entity.position
    const dx = lm.x + 0.5 - p.x
    const dz = lm.z + 0.5 - p.z
    const facts = { ...lm.facts }
    if (!inView) delete facts.between
    return {
      kind: lm.kind, x: lm.x, y: lm.y, z: lm.z, ...facts, box: lm.box,
      distance: Math.round(Math.hypot(dx, lm.y - p.y, dz)),
      side: bearingWord(relativeBearing(bot.entity.yaw, dx, dz)),
      dy: Math.round(lm.y - Math.floor(p.y))
    }
  }

  // What goes into her state: the view's gist and the landmarks, compact (the rays stay here).
  function summary () {
    const now = current()
    if (!now) return null
    const { obs, scene, marks, camera } = now
    const p = bot.entity.position
    const all = memory?.landmarks?.list({ dimension: now.dimension, near: p, within: 256, max: 12 }) ?? []
    const visible = new Set(marks.visible)
    const entry = (lm) => {
      const f = featureView(lm, { inView: visible.has(lm.id) })
      return {
        id: lm.id, kind: lm.kind, confidence: lm.confidence, status: visible.has(lm.id) ? 'visible' : lm.status,
        x: lm.x, y: lm.y, z: lm.z, distance: f.distance, side: f.side, dy: f.dy,
        compass: compass(Math.atan2(-(lm.x + 0.5 - p.x), -(lm.z + 0.5 - p.z))),
        last_seen: lm.last_seen, text: describeFeature(f),
        ...(lm.route ? { route: lm.route } : {}),
        ...(lm.approaches?.length ? { last_approach: lm.approaches[lm.approaches.length - 1] } : {})
      }
    }
    return {
      id: obs.id,
      at: obs.at,
      ms: obs.ms,
      facing: compass(camera.yaw),
      pitch_down_deg: Math.round(-camera.pitch * 180 / Math.PI),
      fov_h: obs.camera.fov_h,
      fov_v: obs.camera.fov_v,
      range: obs.camera.range,
      rays: obs.rays,
      ends: obs.ends,
      truncated: obs.truncated,
      inside_cover: obs.eye_covered,
      light_known: obs.light_known,
      unknown: scene.unknown,
      in_view: all.filter((l) => visible.has(l.id)).slice(0, 8).map(entry),
      remembered: all.filter((l) => !visible.has(l.id)).slice(0, 6).map(entry)
    }
  }

  function start () {
    stop()
    timer = setInterval(() => {
      // A slow view (a huge open landscape, a slow machine) backs off for a while.
      if (Date.now() < slowSince) return
      try {
        const t = Date.now()
        current()
        if (Date.now() - t > 120) slowSince = Date.now() + 10000
      } catch {}
    }, EVERY_MS)
    timer.unref?.()
  }

  function stop () {
    if (timer) clearInterval(timer)
    timer = null
  }

  if (bot.entity?.position && options.background !== false) start()
  else if (options.background !== false) bot.once('spawn', start)
  bot.once('end', stop)

  return {
    current,
    look,
    summary,
    featureView,
    world: () => botWorld(bot),
    landmarks: () => memory?.landmarks ?? null,
    last: () => last,
    start,
    stop
  }
}

// ---- words ----

const ago = (iso, now = Date.now()) => {
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000))
  return s < 90 ? `${s} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`
}

export function describeRoute (r, now = Date.now()) {
  if (!r) return null
  const when = `checked ${ago(r.at, now)} from (${r.from.x} ${r.from.y} ${r.from.z}), ${r.dig ? 'digging allowed' : 'no digging'}`
  if (r.status === 'success') return `a route was found (${when})`
  const closest = r.closest ? `; the closest spot reached is (${r.closest.x} ${r.closest.y} ${r.closest.z}), ${r.remaining} m from it` : ''
  if (r.status === 'no_route') return `no route under these rules within loaded terrain (${when})${closest}`
  return `the search ran out of time before finding one: not proof there's none (${when})${closest}`
}

function describeApproach (a) {
  return `last try ${ago(a.at)}: ${a.outcome}${a.reached ? `, got to (${a.reached.x} ${a.reached.y} ${a.reached.z}) ${a.remaining} m from it` : ''}`
}

// Her state's lines about the view. Says what was seen now, what's remembered, and what navigation
// found, separately.
export function describeView (v) {
  if (!v) return []
  const lines = [`In view (your eyes: facing ${v.facing}, ${v.pitch_down_deg > 0 ? `looking ${v.pitch_down_deg}° down` : v.pitch_down_deg < 0 ? `looking ${-v.pitch_down_deg}° up` : 'looking level'}; ` +
    `${v.fov_h}° wide, up to ${v.range} m; view ${v.id}):`]
  if (!v.in_view.length) lines.push('  nothing grouped as a landmark (no opening, passage, structure, water or lava picked out)')
  for (const l of v.in_view) {
    lines.push(`  ${l.id} ${l.confidence === 'possible' ? '(unsure) ' : ''}${l.text}`)
    if (l.route) lines.push(`    route to ${l.id}: ${describeRoute(l.route)}`)
    else if (l.last_approach) lines.push(`    ${describeApproach(l.last_approach)}`)
  }
  for (const u of v.unknown ?? []) lines.push(`  ${u.side} part of the view: terrain not loaded from ${u.from} m on (unknown, not empty)`)
  if (v.truncated) lines.push('  (the view was cut short: part of it wasn\'t looked at)')
  if (v.remembered?.length) {
    lines.push('Remembered places (not in view now; where they were when last seen):')
    for (const l of v.remembered) {
      const tail = l.status === 'gone' ? ' (gone)' : ''
      lines.push(`  ${l.id} ${l.text.replace(/, at \(/, ' at (')}; last seen ${ago(l.last_seen)}${tail}`)
      if (l.route) lines.push(`    route to ${l.id}: ${describeRoute(l.route)}`)
    }
  }
  return lines
}
