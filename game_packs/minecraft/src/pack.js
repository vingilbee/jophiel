// Wires skills into a bot: the pathfinder plugin, the skill list and the runner. Shared by the
// service (index.js) and the integration tests, so tests exercise exactly what she runs.
import { createRequire } from 'node:module'

import { attachMotion } from './motion/motion.js'
import { attachNoticing } from './noticing.js'
import { attachSightings } from './sightings.js'
import { attachDoors } from './skills/doors.js'
import { WorldMemory } from './world_memory.js'
import { createSkills, manifest } from './skills/index.js'
import { SkillRunner } from './skills/runner.js'

const require = createRequire(import.meta.url)
const { pathfinder } = require('mineflayer-pathfinder')

// motion: false gives the old instant turns (for comparing; the default is the natural motion layer).
// memoryPath(): where this world's memory lives (explored ground, noted places).
// modes: the process's ModeSettings, so background modes stay on across a reconnect.
export function attachSkills (bot, { memoryPath, maxSeconds = 600, motion = true, random, modes = null } = {}) {
  bot.loadPlugin(pathfinder)
  // After the pathfinder, so each tick the motion layer adjusts what the pathfinder just asked for.
  const motionLayer = motion ? attachMotion(bot, { random }) : null
  attachDoors(bot)
  const sightings = attachSightings(bot)
  const memory = new WorldMemory(memoryPath?.() ?? null)
  const noticing = attachNoticing(bot, { memory })
  const skills = createSkills({ memory })
  const runner = new SkillRunner(bot, skills, { maxSeconds, motion: motionLayer, modes })
  // What a mode did is something she noticed: her hands see it, and it's in her play log.
  runner.on('mode', (result) => noticing.report('mode', 'info', result.text, {
    mode: result.mode, why: result.why, status: result.status, data: result.data
  }))
  return { runner, motion: motionLayer, noticing, sightings, memory, manifest: manifest(skills) }
}
