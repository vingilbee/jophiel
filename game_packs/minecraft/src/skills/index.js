// The Minecraft pack's skill list. Her hands (phase 3) see the manifest and pick one at a time.
import { manifestEntry } from './contract.js'
import { fight } from './fight.js'
import { mine } from './mine.js'
import { followPlayer, goTo } from './movement.js'
import { craft, eat, sleep } from './survival.js'
import { collectItems, give, lookAt, place } from './hands.js'
import { smelt, store, take } from './containers.js'
import { wear } from './armor.js'
import { hold, putInHotbar } from './inventory.js'
import { surface } from './water.js'

export function createSkills () {
  return [
    goTo, mine, fight, eat, sleep, craft, followPlayer,
    place, collectItems, give, lookAt, store, take, smelt, wear, hold, putInHotbar, surface
  ]
}

export function manifest (skills) {
  return skills.map(manifestEntry)
}
