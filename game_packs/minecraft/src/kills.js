// Her kills, counted by code: a mob she hit that dies soon after. Every hit goes through
// bot.attack (her fight skill, her single actions, the programs she wrote), so that's where
// it's counted. Seen live (2026-10-17): a move she taught herself killed three sheep and
// reported "the target is gone" each time, so her hands thought it had failed and kept
// chasing sheep. The state's `kills` ({label: count}, since she joined) is what steps like
// "kill a sheep" are checked against.
import { entityLabel } from './state.js'

export const HIT_WINDOW_MS = 5000 // a death this long after her last hit is hers

export function attachKills (bot, { now = () => Date.now() } = {}) {
  const hits = new Map() // entity id -> { label, at }
  const counts = {}
  const attack = bot.attack.bind(bot)
  bot.attack = (entity, ...rest) => {
    const at = now()
    for (const [id, hit] of hits) if (at - hit.at > HIT_WINDOW_MS) hits.delete(id)
    if (entity && entity !== bot.entity && entity.id !== undefined) hits.set(entity.id, { label: entityLabel(entity), at })
    return attack(entity, ...rest)
  }
  bot.on('entityDead', (entity) => {
    const hit = hits.get(entity?.id)
    if (!hit) return
    hits.delete(entity.id)
    if (now() - hit.at > HIT_WINDOW_MS) return
    counts[hit.label] = (counts[hit.label] ?? 0) + 1
    bot.emit('kill', hit.label)
  })
  bot.killCounts = counts
  return { counts }
}
