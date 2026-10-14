// Small hands-on skills: place, collect_items, give, look_at.
import { createRequire } from 'node:module'

import { ParamError, SkillEnd, done, failed, timeLimitParam } from './contract.js'
import { count, findEntity, findPlayer, goals, requireItem, requireOtherPlayer, walkTo } from './common.js'
import { parseTarget } from './movement.js'
import { seenBlock } from '../sight.js'

const require = createRequire(import.meta.url)
const { Vec3 } = require('vec3')

const REACH = 4

const isAir = (block) => block && (block.boundingBox === 'empty') && !['water', 'lava'].includes(block.name)
const isSolid = (block) => block?.boundingBox === 'block'

// "x y z": the block at that point (decimals are fine: 1.5 is in block 1).
function parsePlace (text) {
  const n = text.split(/[\s,]+/).filter(Boolean)
  if (n.length !== 3 || !n.every((v) => /^-?\d+(\.\d+)?$/.test(v))) throw new ParamError('where must be "x y z"')
  return new Vec3(Math.floor(+n[0]), Math.floor(+n[1]), Math.floor(+n[2]))
}

// Is anyone (her included) standing in this block?
function occupied (bot, position) {
  const box = { min: position, max: position.offset(1, 1, 1) }
  return Object.values(bot.entities).some((e) => {
    if (!e.position || e.name === 'item' || e.type === 'projectile' || e.type === 'other') return false
    const w = (e.width ?? 0.6) / 2
    const h = e.height ?? 1.8
    return e.position.x + w > box.min.x && e.position.x - w < box.max.x &&
      e.position.z + w > box.min.z && e.position.z - w < box.max.z &&
      e.position.y + h > box.min.y && e.position.y < box.max.y
  })
}

// A free spot on solid ground next to her, preferring the one in front.
function spotNextToHer (bot) {
  const here = bot.entity.position
  const feet = here.floored()
  const ahead = here.offset(-Math.sin(bot.entity.yaw) * 1.5, 0, -Math.cos(bot.entity.yaw) * 1.5)
  const spots = []
  for (let dx = -2; dx <= 2; dx++) {
    for (let dz = -2; dz <= 2; dz++) {
      for (const dy of [0, -1, 1]) {
        const p = feet.offset(dx, dy, dz)
        if (isAir(bot.blockAt(p)) && isSolid(bot.blockAt(p.offset(0, -1, 0))) && !occupied(bot, p)) spots.push(p)
      }
    }
  }
  return spots.sort((a, b) => a.offset(0.5, 0, 0.5).distanceTo(ahead) - b.offset(0.5, 0, 0.5).distanceTo(ahead))[0] ?? null
}

export const place = {
  name: 'place',
  description: 'Put down a block she carries (e.g. a crafting table, a torch, a furnace): next to her, or at a spot.',
  doneWhen: 'the block is in place',
  stopsWhen: 'she has none, the spot is taken or has nothing to rest on, can\'t reach it',
  params: {
    block: { type: 'string', required: true, description: 'the block item, e.g. crafting_table' },
    where: { type: 'string', description: 'optional "x y z"; default: a free spot next to her' },
    time_limit: timeLimitParam(30, 60)
  },
  validate (bot, params) {
    requireItem(bot, params.block)
    if (!bot.registry.blocksByName[params.block]) throw new ParamError(`${params.block} isn't a block`)
    if (params.where) parsePlace(params.where)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const item = bot.inventory.items().find((i) => i.name === params.block)
    if (!item) return failed(`has no ${params.block}`)

    const target = params.where ? parsePlace(params.where) : spotNextToHer(bot)
    if (!target) return failed('no free spot next to her (move to open ground first)')
    const there = bot.blockAt(target)
    if (!isAir(there)) return failed(`that spot is taken (${there?.name ?? 'unloaded'})`)
    // Something solid to place against: the ground first, then the sides.
    const faces = [new Vec3(0, 1, 0), new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(0, -1, 0)]
    const face = faces.find((f) => isSolid(bot.blockAt(target.minus(f))))
    if (!face) return failed('nothing solid for it to rest on')
    const reference = bot.blockAt(target.minus(face))

    if (bot.entity.position.distanceTo(target.offset(0.5, 0.5, 0.5)) > REACH) {
      if (!await walkTo(ctx, new goals.GoalNear(target.x, target.y, target.z, 3), { timeoutMs: 15000 })) return failed('can\'t reach that spot')
    }
    if (occupied(bot, target)) {
      // She's standing in it: step aside.
      await walkTo(ctx, new goals.GoalInvert(new goals.GoalNear(target.x, target.y, target.z, 1.5)), { timeoutMs: 4000 })
      if (occupied(bot, target)) return failed('someone is standing there')
    }
    if (bot.heldItem?.name !== params.block) await ctx.race(bot.equip(item, 'hand'))
    try {
      await ctx.race(bot.placeBlock(reference, face))
    } catch (err) {
      if (err instanceof SkillEnd) throw err
      // placeBlock times out waiting for the update now and then; the block may still be there.
    }
    await ctx.sleep(200)
    if (bot.blockAt(target)?.name !== params.block) return failed('the game didn\'t accept it')
    return done({ x: target.x, y: target.y, z: target.z })
  }
}

export const collectItems = {
  name: 'collect_items',
  description: 'Pick up items lying on the ground nearby (all, or one kind).',
  doneWhen: 'nothing (of that kind) is left within the radius',
  stopsWhen: 'nothing to pick up, items out of reach, inventory full',
  params: {
    item: { type: 'string', description: 'optional item name, e.g. apple' },
    radius: { type: 'integer', min: 2, max: 32, default: 16, description: 'how far to look, in blocks' },
    time_limit: timeLimitParam(60, 180)
  },
  validate (bot, params) {
    if (params.item) requireItem(bot, params.item)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const itemName = (e) => e.getDroppedItem?.()?.name ?? null
    const wanted = (e) => e.name === 'item' && e.isValid && (!params.item || itemName(e) === params.item) &&
      e.position.distanceTo(bot.entity.position) <= params.radius
    const before = new Map(bot.inventory.items().map((i) => [i.name, 0]))
    for (const i of bot.inventory.items()) before.set(i.name, before.get(i.name) + i.count)
    const gained = () => {
      const now = new Map()
      for (const i of bot.inventory.items()) now.set(i.name, (now.get(i.name) ?? 0) + i.count)
      return Object.fromEntries([...now].map(([n, c]) => [n, c - (before.get(n) ?? 0)]).filter(([, c]) => c > 0))
    }
    const skipped = new Set()

    if (!Object.values(bot.entities).some(wanted)) return failed(`no ${params.item ?? 'items'} on the ground nearby`)
    while (true) {
      ctx.progress({ collected: gained() })
      const next = Object.values(bot.entities).filter((e) => wanted(e) && !skipped.has(e.id))
        .sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position))[0]
      if (!next) {
        await ctx.sleep(300) // the last pickup's inventory update
        const got = gained()
        if (!Object.keys(got).length && skipped.size) return failed('couldn\'t reach them', { collected: got })
        return done({ collected: got, unreachable: skipped.size })
      }
      if (bot.inventory.emptySlotCount() === 0 && !bot.inventory.items().some((i) => i.name === itemName(next) && i.count < i.stackSize)) {
        return failed('inventory full', { collected: gained() })
      }
      const p = next.position
      await walkTo(ctx, new goals.GoalNear(p.x, p.y, p.z, 1), { timeoutMs: 8000 })
      const until = Date.now() + 2500
      while (next.isValid && Date.now() < until) await ctx.sleep(100)
      if (next.isValid) skipped.add(next.id)
    }
  }
}

export const give = {
  name: 'give',
  description: 'Walk up to a player and toss them some of an item.',
  doneWhen: 'the items are tossed to them',
  stopsWhen: 'she has none, the player isn\'t around, can\'t reach them',
  params: {
    player: { type: 'string', required: true, description: 'their in-game name' },
    item: { type: 'string', required: true, description: 'item name, e.g. bread' },
    count: { type: 'integer', min: 1, max: 64, default: 1, description: 'how many' },
    time_limit: timeLimitParam(60, 120)
  },
  validate (bot, params) {
    requireItem(bot, params.item)
    requireOtherPlayer(bot, params.player)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const have = count(bot, [params.item])
    if (!have) return failed(`has no ${params.item}`)
    const player = findPlayer(bot, params.player)
    if (!player) return failed(`can't see ${params.player}`)
    const n = Math.min(have, params.count)

    // About two blocks away: a tossed item flies about that far, so it lands at their feet and
    // not at hers (closer, she'd pick her own toss back up once the 2 s pickup delay is over).
    if (bot.entity.position.distanceTo(player.position) > 2.6) {
      await walkTo(ctx, new goals.GoalNear(player.position.x, player.position.y, player.position.z, 2), { timeoutMs: 25000 })
      if (!player.isValid) return new SkillEnd('lost', `${params.player} left`)
      if (bot.entity.position.distanceTo(player.position) > 3) return failed(`can't get to ${params.player}`)
    }
    if (ctx.motion) ctx.motion.track(null)
    // Look at their body, not their face: the item drops in front of them.
    await ctx.race(bot.lookAt(player.position.offset(0, 0.6, 0)))
    ctx.check()
    const type = bot.registry.itemsByName[params.item].id
    await ctx.race(bot.toss(type, null, n))
    const until = Date.now() + 1500
    while (count(bot, [params.item]) > have - n && Date.now() < until) await ctx.sleep(100)
    const given = have - count(bot, [params.item])
    if (given <= 0) return failed('the game didn\'t accept the toss')
    // Step back, out of reach of what she just tossed.
    bot.setControlState('back', true)
    await ctx.sleep(700)
    bot.setControlState('back', false)
    return done({ given, ...(given < params.count ? { wanted: params.count } : {}) })
  }
}

export const lookAt = {
  name: 'look_at',
  description: 'Turn to look at a player, a mob, a block or a place for a few seconds.',
  doneWhen: 'the seconds are up',
  stopsWhen: 'the target isn\'t around or leaves',
  params: {
    target: { type: 'string', required: true, description: '"player:<name>", "mob:<type>", "block:<type>" or "x y z"' },
    seconds: { type: 'number', min: 0.5, max: 30, default: 3, description: 'how long to keep looking' },
    time_limit: timeLimitParam(35, 35)
  },
  validate (bot, params) {
    const target = parseTarget(params.target)
    if (target.kind === 'player') requireOtherPlayer(bot, target.name)
  },
  async run (ctx, params) {
    const { bot } = ctx
    const target = parseTarget(params.target)
    let entity = null
    let point = null
    if (target.kind === 'player') entity = findPlayer(bot, target.name)
    else if (target.kind === 'mob') entity = findEntity(bot, target.name, 32)
    else if (target.kind === 'block') {
      const ids = bot.registry.blocksByName[target.name] ? [bot.registry.blocksByName[target.name].id] : []
      point = seenBlock(bot, { matching: ids, maxDistance: 32 })?.position.offset(0.5, 0.5, 0.5)
    } else point = new Vec3(target.x, target.y ?? bot.entity.position.y + 1.6, target.z)
    if (!entity && !point) return failed(`can't see ${target.name ?? 'that'}`)

    const until = Date.now() + params.seconds * 1000
    if (entity && ctx.motion) ctx.motion.track(entity)
    while (Date.now() < until) {
      if (entity && !entity.isValid) return new SkillEnd('lost', `${target.name} is gone`)
      if (!ctx.motion || point) await ctx.race(bot.lookAt(entity ? entity.position.offset(0, (entity.height ?? 1.8) * 0.85, 0) : point))
      await ctx.sleep(100)
    }
    return done({ looked_seconds: params.seconds })
  }
}
