// State JSON -> the few lines of text her hands will read (stage G phase 3). Pure.

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
    `Health ${state.health}/20, food ${state.food}/20. Holding ${state.held ? `${state.held.name} x${state.held.count}` : 'nothing'}.`,
    `Day ${state.time.day}, ${state.time.phase} (time ${state.time.time_of_day}), weather ${state.weather}.`,
    `Inventory: ${state.inventory.length ? state.inventory.map((i) => `${i.name} x${i.count}`).join(', ') : 'empty'}.`,
    `Nearby: ${state.nearby.length ? state.nearby.map((e) => `${e.label} ${where(e)}`).join('; ') : 'nothing within range'}.`,
    `Blocks: ${state.blocks?.length ? state.blocks.map((b) => `${b.name} ${where(b)}`).join('; ') : 'nothing notable close by'}.`
  ]
  if (state.noticed?.length) lines.push(`Noticed lately: ${state.noticed.map((n) => n.text).join('; ')}.`)
  return lines.join('\n')
}

function describePitch (down) {
  if (Math.abs(down) <= 10) return 'level'
  return down > 0 ? `${down}° down` : `${-down}° up`
}
