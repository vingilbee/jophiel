import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createRequire } from 'node:module'

import { needsFor } from '../src/needs.js'

const require = createRequire(import.meta.url)
const registry = require('minecraft-data')('26.1')
const { Recipe } = require('prismarine-recipe')(registry)

const at = (steps, action, item) => steps.findIndex((s) => s.action === action && s.item === item)

test('needsFor: iron ore with nothing goes wood -> wooden pickaxe -> stone -> stone pickaxe -> iron', () => {
  const out = needsFor(registry, Recipe, 'iron_ore')
  assert.equal(out.kind, 'block')
  assert.equal(out.ready, false)
  assert.deepEqual(out.gaps, [])
  const order = ['wooden_pickaxe', 'cobblestone', 'stone_pickaxe', 'raw_iron'].map((item) =>
    out.steps.findIndex((s) => s.item === item))
  assert.ok(order.every((i) => i >= 0), JSON.stringify(out.steps))
  assert.deepEqual([...order].sort((a, b) => a - b), order, JSON.stringify(out.steps))
  const cobble = out.steps[at(out.steps, 'mine', 'cobblestone')]
  assert.equal(cobble.from, 'stone')
  assert.equal(cobble.tool, 'wooden_pickaxe')
  const iron = out.steps.at(-1)
  assert.deepEqual([iron.action, iron.from, iron.tool, iron.tool_needed], ['mine', 'iron_ore', 'stone_pickaxe', true])
  assert.ok(at(out.steps, 'mine', 'oak_log') === 0, 'logs come first')
})

test('needsFor: what she carries counts (the 2026-10-19 moment: a wooden pickaxe, 3 planks, 2 sticks)', () => {
  const have = { wooden_pickaxe: 1, oak_planks: 3, stick: 2, dirt: 2 }
  const out = needsFor(registry, Recipe, 'iron_ore', { have, tableNearby: true })
  assert.deepEqual(out.steps.map((s) => `${s.action} ${s.item}`),
    ['mine cobblestone', 'craft stone_pickaxe', 'mine raw_iron'])
  const far = needsFor(registry, Recipe, 'iron_ore', { have })
  assert.ok(at(far.steps, 'place', 'crafting_table') < at(far.steps, 'craft', 'stone_pickaxe'), 'a table first, away from hers')
  assert.equal(at(far.steps, 'craft', 'wooden_pickaxe'), -1, 'she has one')
})

test('needsFor: a tool she holds makes the block ready; an item she has is ready', () => {
  assert.equal(needsFor(registry, Recipe, 'iron_ore', { have: { stone_pickaxe: 1 } }).ready, true)
  assert.equal(needsFor(registry, Recipe, 'stone_pickaxe', { have: { stone_pickaxe: 1 } }).ready, true)
  assert.equal(needsFor(registry, Recipe, 'oak_log').steps[0].action, 'mine')
})

test('needsFor: iron ingots need a furnace and fuel; a placed table is not made again', () => {
  const out = needsFor(registry, Recipe, 'iron_ingot', { count: 3, have: { stone_pickaxe: 1 }, tableNearby: true })
  assert.ok(at(out.steps, 'smelt', 'iron_ingot') > at(out.steps, 'place', 'furnace'), JSON.stringify(out.steps))
  assert.equal(at(out.steps, 'craft', 'crafting_table'), -1)
  assert.equal(out.steps[at(out.steps, 'mine', 'raw_iron')].count, 3)
})

test('needsFor: mob drops are hunted, unknown names get suggestions', () => {
  assert.deepEqual(needsFor(registry, Recipe, 'string').steps[0], { action: 'hunt', item: 'string', count: 1, from: 'spider' })
  const bad = needsFor(registry, Recipe, 'bed')
  assert.equal(bad.known, false)
  assert.ok(bad.did_you_mean.includes('white_bed'))
})
