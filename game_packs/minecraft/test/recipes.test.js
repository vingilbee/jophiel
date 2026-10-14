import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createRequire } from 'node:module'

import { didYouMean, suggestNames } from '../src/recipes.js'

const require = createRequire(import.meta.url)
const registry = require('minecraft-data')('26.1')
const items = Object.keys(registry.itemsByName)

test('suggestNames: "bed" finds the beds, "oak plank" finds oak_planks, nonsense finds nothing', () => {
  const beds = suggestNames(items, 'bed')
  assert.equal(beds[0], 'white_bed', beds.join(','))
  assert.ok(beds.every((n) => n.endsWith('_bed')), beds.join(','))
  assert.ok(suggestNames(items, 'oak plank').includes('oak_planks'))
  assert.deepEqual(suggestNames(items, 'zzqx'), [])
  assert.equal(didYouMean(items, 'zzqx'), '')
  assert.match(didYouMean(items, 'bed'), /^ \(did you mean: .*bed/)
  assert.ok(!suggestNames(items, 'oak_planks').includes('oak_planks'), 'never suggests the name itself')
})
