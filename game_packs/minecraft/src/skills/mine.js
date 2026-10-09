// mine(block, count): break blocks of a type and pick up what drops, until she has `count`.
import { done, failed, timeLimitParam } from './contract.js'
import { bestToolFor, blockIds, count, goals, requireBlock, walkTo } from './common.js'

const MAX_UNREACHABLE = 6

// Item names a block type drops (stone -> cobblestone). Empty for blocks that drop nothing by hand.
export function dropNames (registry, ids) {
  const names = new Set()
  for (const id of ids) {
    for (const drop of registry.blocks[id]?.drops ?? []) {
      const itemId = typeof drop === 'number' ? drop : drop?.drop?.id ?? drop?.drop ?? drop?.id
      const item = registry.items[itemId]
      if (item) names.add(item.name)
    }
  }
  return [...names]
}

export const mine = {
  name: 'mine',
  description: 'Break blocks of one type nearby and collect what drops. "log" means any kind of log.',
  doneWhen: 'she has collected count of what the block drops',
  stopsWhen: 'none left within radius, no suitable tool, unreachable, attacked badly, time limit',
  params: {
    block: { type: 'string', required: true, description: 'block name, e.g. oak_log, stone, coal_ore, or "log"' },
    count: { type: 'integer', min: 1, max: 64, default: 1, description: 'how many to collect' },
    radius: { type: 'integer', min: 4, max: 64, default: 32, description: 'how far to look, in blocks' },
    time_limit: timeLimitParam(120, 600)
  },
  validate (bot, params) {
    requireBlock(bot, params.block)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const ids = blockIds(bot, params.block)
    const drops = dropNames(bot.registry, ids)
    const start = count(bot, drops)
    const unreachable = new Set()
    let mined = 0

    const collected = () => (drops.length ? count(bot, drops) - start : mined)
    // The server says "picked up" before it sends the inventory update; give it a moment.
    const settle = async () => {
      const until = Date.now() + 1500
      while (collected() < params.count && Date.now() < until) await ctx.sleep(100)
    }

    while (true) {
      ctx.progress({ mined, collected: collected() })
      if (collected() >= params.count) return done({ mined, collected: collected() })

      const here = bot.entity.position
      const position = bot.findBlocks({ matching: ids, maxDistance: params.radius, count: 64 })
        .filter((p) => !unreachable.has(p.toString()))
        .sort((a, b) => a.distanceTo(here) - b.distanceTo(here))[0]
      if (!position) {
        await settle()
        if (collected() >= params.count) return done({ mined, collected: collected() })
        return failed(`no ${params.block} within ${params.radius} blocks`, { mined, collected: collected() })
      }

      const block = bot.blockAt(position)
      const tool = bestToolFor(bot, block)
      if (!tool.harvestable) {
        const needs = Object.keys(block.harvestTools ?? {}).map((id) => bot.registry.items[id]?.name).filter(Boolean)
        return failed(`no tool that can harvest ${block.name} (needs ${needs.slice(0, 3).join(' or ')})`,
          { mined, collected: collected() })
      }

      const reached = await walkTo(ctx, new goals.GoalLookAtBlock(position, bot.world, { reach: 4 }), { canDig: true, timeoutMs: 30000 })
      if (!reached) {
        unreachable.add(position.toString())
        if (unreachable.size >= MAX_UNREACHABLE) return failed('can\'t reach any', { mined, collected: collected() })
        continue
      }

      const current = bot.blockAt(position)
      if (!current || !ids.includes(current.type)) continue // someone else broke it
      if (tool.item && bot.heldItem?.type !== tool.item.type) await ctx.race(bot.equip(tool.item, 'hand'))
      const drops = watchDrops(bot, position)
      try {
        await ctx.race(bot.dig(current, true))
        mined++
        await pickUp(ctx, drops)
        if (mined >= params.count) await settle()
      } finally {
        drops.off()
      }
    }
  }
}

// Items that pop out near the block while (and just after) it breaks.
function watchDrops (bot, position) {
  const items = new Set()
  const onDrop = (entity) => {
    if (entity.position.distanceTo(position) < 3) items.add(entity)
  }
  bot.on('itemDrop', onDrop)
  return { items, off: () => bot.off('itemDrop', onDrop) }
}

// Walk onto each drop until it's picked up (gone), a few seconds at most each.
async function pickUp (ctx, drops) {
  const { bot } = ctx
  await ctx.sleep(400) // drops land and become collectable (about 10 ticks)
  for (const item of drops.items) {
    const until = Date.now() + 4000
    while (item.isValid && Date.now() < until) {
      ctx.check()
      const p = item.position
      if (bot.entity.position.distanceTo(p) > 1.2) {
        if (!await walkTo(ctx, new goals.GoalNear(p.x, p.y, p.z, 1), { canDig: false, timeoutMs: 4000 })) break
      } else {
        await ctx.sleep(100)
      }
    }
  }
}
