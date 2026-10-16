// State JSON -> the few lines of text her hands will read (stage G phase 3). Pure.
import { describeSurroundings } from './surroundings.js'
import { describeTerrain } from './terrain.js'

function where (entity) {
  const vertical = Math.abs(entity.dy) >= 2
    ? `, ${Math.abs(Math.round(entity.dy))} m ${entity.dy > 0 ? 'above' : 'below'}`
    : ''
  return `${entity.distance} m ${entity.side}${vertical}`
}

export function describeState (state) {
  const p = state.position
  const lines = [
    `You are ${state.username} (your own player). Other players online: ${state.players_online?.length ? state.players_online.join(', ') : 'none'}.`,
    `${state.username} at (${p.x}, ${p.y}, ${p.z}) in the ${state.dimension.replace('minecraft:', '')}, ` +
      `facing ${state.facing.compass}, looking ${describePitch(state.facing.pitch_down_deg)}, ` +
      `standing on ${state.standing_on ?? 'nothing'}${state.in_water ? ', in water' : ''}.`,
    `Health ${hearts(state.health)} of 10 hearts (${state.health}/20), food ${state.food}/20` +
      `${typeof state.oxygen === 'number' && state.oxygen < 20 && state.oxygen >= -20 ? `, air ${state.oxygen}/20` : ''}. ` +
      `Holding ${state.held ? `${state.held.name} x${state.held.count}` : 'nothing'}. ` +
      `Wearing ${Object.values(state.armor ?? {}).length ? Object.values(state.armor).join(', ') : 'no armor'}.`,
    `Day ${state.time.day}, ${state.time.phase} (time ${state.time.time_of_day}), weather ${state.weather}.`,
    `Inventory: ${state.inventory.length ? state.inventory.map((i) => `${i.name} x${i.count}`).join(', ') : 'empty'}.`,
    `Nearby: ${state.nearby.length ? state.nearby.map((e) => `${e.label} ${where(e)}`).join('; ') : 'nothing within range'}.`,
    `Blocks: ${state.blocks?.length ? state.blocks.map((b) => `${b.name} ${where(b)}`).join('; ') : 'nothing notable close by'}.`
  ]
  lines.push(...describeSurroundings(state.surroundings))
  lines.push(...describeTerrain(state.terrain))
  const spectating = new Set(state.players_spectating ?? [])
  for (const p of state.players ?? []) {
    if (!spectating.has(p.name)) lines.push(describePlayer(p, state.nearby))
  }
  for (const name of spectating) {
    lines.push(`${name} is in spectator mode: invisible to you and flying through walls, so you can't see, follow ` +
      'or reach them, and where you last saw them means nothing. Don\'t go looking; they can see you.')
  }
  for (const p of state.players_out_of_sight ?? []) {
    if (spectating.has(p.name)) continue
    lines.push(p.last_seen
      ? `${p.name} is online but out of sight: last seen at (${p.last_seen.x}, ${p.last_seen.y}, ${p.last_seen.z}), ` +
        `${p.distance} m from you, ${p.seconds_ago} s ago.`
      : `${p.name} is online but out of sight, and you haven't seen them since you joined.`)
  }
  if (state.noticed?.length) lines.push(`Noticed lately: ${state.noticed.map((n) => n.text).join('; ')}.`)
  return lines.join('\n')
}

// "tearDrop12 (4 m ahead-left): walking north, towards you; holding stone pickaxe; wearing iron
// chestplate; looking at oak log 3 m away. Last minute: broke oak log (x3) 5 s ago."
export function describePlayer (p, nearby = []) {
  const seen = nearby.find((e) => e.label === p.name)
  const parts = [p.pose === 'sleeping' ? 'in bed' : p.moving + (p.pose === 'sneaking' ? ', sneaking' : p.pose === 'swimming' ? ', swimming' : '')]
  if (p.facing) parts.push(`facing ${p.facing.compass} (your ${p.facing.side})`)
  parts.push(`holding ${p.holding ? p.holding.replace(/_/g, ' ') : 'nothing'}`)
  if (p.armor?.length) parts.push(`wearing ${p.armor.map((a) => a.replace(/_/g, ' ')).join(', ')}`)
  if (p.health !== null && p.health !== undefined) parts.push(`${hearts(p.health)} of 10 hearts`)
  if (p.looking_at) parts.push(`looking at ${p.looking_at.name.replace(/_/g, ' ')} ${p.looking_at.distance} m away`)
  const recent = p.recent?.length
    ? ` Last minute: ${p.recent.map((r) => `${r.text} ${r.seconds_ago} s ago`).join('; ')}.`
    : ''
  return `${p.name}${seen ? ` (${where(seen)})` : ''}: ${parts.join('; ')}.${recent}`
}

// Minecraft health is 20 points, shown as 10 hearts: say it the way the player sees it.
export function hearts (health) {
  return Math.round(health) / 2
}

function describePitch (down) {
  if (Math.abs(down) <= 10) return 'level'
  return down > 0 ? `${down}° down` : `${-down}° up`
}
