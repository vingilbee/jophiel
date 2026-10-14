// The Minecraft pack's skill list. Her hands (phase 3) see the manifest and pick one at a time.
import { manifestEntry } from './contract.js'
import { createExplore } from './explore.js'
import { fight } from './fight.js'
import { mine } from './mine.js'
import { followPlayer, goTo } from './movement.js'
import { craft, eat, sleep } from './survival.js'
import { collectItems, give, lookAt, place } from './hands.js'
import { smelt, store, take } from './containers.js'

// memory: this world's WorldMemory (explored chunks and noted places).
export function createSkills ({ memory }) {
  return [
    goTo, mine, fight, createExplore(memory), eat, sleep, craft, followPlayer,
    place, collectItems, give, lookAt, store, take, smelt
  ]
}

export function manifest (skills) {
  return skills.map(manifestEntry)
}
