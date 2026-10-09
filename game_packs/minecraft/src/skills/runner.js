// Runs one skill at a time and owns the shared safety no skill can switch off:
//   - stop() ends it at once (pause, takeover, end session all come through here);
//   - a time limit, capped by maxSeconds;
//   - low health is handled first: the skill ends as `hurt`, she backs away from the nearest
//     hostile and eats if she can, then reports;
//   - death and disconnects end it.
// LAN-only joining is enforced before she is ever in a world (config.assertPrivateHost).
import { EventEmitter } from 'node:events'
import { randomUUID } from 'node:crypto'

import { ParamError, SkillEnd, validateParams } from './contract.js'
import { eatBest, nearestHostile, retreatFrom, stopEverything } from './common.js'
import { SKILL_STYLE } from '../motion/styles.js'

export class BusyError extends Error {}
export class UnknownSkillError extends Error {}

const SAFETY_SECONDS = 6

export class SkillRunner extends EventEmitter {
  constructor (bot, skills, { maxSeconds = 300, lowHealth = 6, keep = 20, motion = null } = {}) {
    super()
    this.bot = bot
    this.motion = motion
    this.skills = new Map(skills.map((s) => [s.name, s]))
    this.maxSeconds = maxSeconds
    this.lowHealth = lowHealth
    this.keep = keep
    this.runs = [] // newest last; finished and current
    this.active = null // { record, controller }

    this._onHealth = () => {
      const run = this.active
      if (!run || run.skill.name === 'eat' || this.bot.health > this.lowHealth) return
      run.lowHealth = true
      this._abort(new SkillEnd('hurt', `health low (${Math.round(this.bot.health)}/20)`))
    }
    this._onDeath = () => this._abort(new SkillEnd('failed', 'died'))
    this._onEnd = () => this._abort(new SkillEnd('stopped', 'disconnected'))
    bot.on('health', this._onHealth)
    bot.on('death', this._onDeath)
    bot.on('end', this._onEnd)
  }

  get current () {
    return this.active?.record ?? null
  }

  get (id) {
    return this.runs.find((r) => r.id === id) ?? null
  }

  history () {
    return this.runs.filter((r) => r.status !== 'running')
  }

  // source: who asked ("driver" for her hands in a session, "user" by hand), so a session ending
  // stops only what its driver started.
  start (name, input = {}, { source = 'user' } = {}) {
    if (this.active) throw new BusyError(`already running ${this.active.record.skill}`)
    const skill = this.skills.get(name)
    if (!skill) throw new UnknownSkillError(`no skill named ${name}`)
    const params = validateParams(skill.params, input)
    if (skill.validate) skill.validate(this.bot, params) // throws ParamError for e.g. unknown block names

    const record = {
      id: randomUUID(),
      skill: name,
      params,
      source,
      status: 'running',
      reason: null,
      data: {},
      started_at: new Date().toISOString(),
      ended_at: null,
      seconds: null
    }
    const controller = new AbortController()
    const run = { record, controller, skill, lowHealth: false }
    this.motion?.setStyle(SKILL_STYLE[name] ?? 'relaxed')
    this.motion?.setIdle(false)
    this.active = run
    this.runs.push(record)
    if (this.runs.length > this.keep) this.runs.splice(0, this.runs.length - this.keep)

    const seconds = Math.min(params.time_limit ?? this.maxSeconds, this.maxSeconds)
    const timer = setTimeout(() => this._abort(new SkillEnd('timeout', `no result after ${seconds} s`)), seconds * 1000)
    this._execute(run, timer).catch(() => {}) // _execute never rejects; belt and braces
    this.emit('start', record)
    return record
  }

  stop (reason = 'stopped by request') {
    const run = this.active
    if (run?.controller.signal.aborted) {
      // Already ending (e.g. getting safe after low health): stop that too, right now.
      run.stopRequested = true
      stopEverything(this.bot)
    }
    this._abort(new SkillEnd('stopped', reason))
    return run?.record ?? null
  }

  // Resolves with the record once finished, or as it stands after timeoutMs.
  wait (id, timeoutMs) {
    const record = this.get(id)
    if (!record || record.status !== 'running') return Promise.resolve(record)
    return new Promise((resolve) => {
      const onFinish = (finished) => {
        if (finished.id !== id) return
        clearTimeout(timer)
        this.off('finish', onFinish)
        resolve(finished)
      }
      const timer = setTimeout(() => {
        this.off('finish', onFinish)
        resolve(record)
      }, timeoutMs)
      this.on('finish', onFinish)
    })
  }

  _abort (end) {
    const run = this.active
    if (!run || run.controller.signal.aborted) return
    run.controller.abort(end)
    stopEverything(this.bot) // makes any pathfinder/dig/consume promise in the skill settle now
  }

  async _execute (run, timer) {
    const { record, controller, skill } = run
    const ctx = makeContext(this.bot, record, controller.signal, this.motion)
    const aborted = new Promise((resolve) => {
      controller.signal.addEventListener('abort', () => resolve(controller.signal.reason), { once: true })
    })
    let end
    try {
      // The abort wins even if a skill is slow to notice it.
      end = await Promise.race([skill.run(ctx, record.params).then((result) => result ?? new SkillEnd('done')), aborted])
    } catch (err) {
      end = err instanceof SkillEnd ? err : new SkillEnd('failed', `error: ${err.message}`)
    }
    if (!(end instanceof SkillEnd)) end = new SkillEnd('failed', 'skill returned no outcome')
    clearTimeout(timer)
    if (!controller.signal.aborted) controller.abort(end) // anything still running in the skill stops acting
    stopEverything(this.bot)

    if (run.lowHealth && this.bot.entity) {
      end.data = { ...end.data, ...(await this._getSafe(run)) }
    }

    this.motion?.track(null)
    this.motion?.setStyle('relaxed')
    this.motion?.setIdle(true)
    record.status = end.status
    record.reason = end.reason
    record.data = { ...record.data, ...end.data }
    record.ended_at = new Date().toISOString()
    record.seconds = Math.round((Date.parse(record.ended_at) - Date.parse(record.started_at)) / 100) / 10
    this.active = null
    this.emit('finish', record)
  }

  // Low health: back away from the nearest hostile, then eat. Bounded, and stop() still works:
  // a new skill can't start until this is over, and stopEverything() is safe to call anytime.
  async _getSafe (run) {
    const result = { retreated: false, ate: null }
    const deadline = Date.now() + SAFETY_SECONDS * 1000
    const threat = nearestHostile(this.bot, 12)
    if (threat) {
      result.retreated = true
      await retreatFrom(this.bot, threat, Math.min(3000, deadline - Date.now()), () => run.stopRequested)
    }
    if (!run.stopRequested && this.bot.food < 20 && Date.now() < deadline) {
      result.ate = await eatBest(this.bot).catch(() => null)
    }
    if (run.stopRequested) result.stopped_while_getting_safe = true
    stopEverything(this.bot)
    return result
  }

  detach () {
    this._abort(new SkillEnd('stopped', 'shutting down'))
    this.bot.off('health', this._onHealth)
    this.bot.off('death', this._onDeath)
    this.bot.off('end', this._onEnd)
  }
}

function makeContext (bot, record, signal, motion) {
  const check = () => {
    if (signal.aborted) throw signal.reason
  }
  return {
    bot,
    motion, // null when the motion layer is off
    signal,
    check,
    // Sleep that ends early (by throwing the outcome) when the skill is stopped.
    sleep (ms) {
      check()
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          signal.removeEventListener('abort', onAbort)
          resolve()
        }, ms)
        const onAbort = () => {
          clearTimeout(timer)
          reject(signal.reason)
        }
        signal.addEventListener('abort', onAbort, { once: true })
      })
    },
    // Await a bot promise, but give up the moment the skill is stopped.
    async race (promise) {
      check()
      let onAbort
      const stopped = new Promise((resolve, reject) => {
        onAbort = () => reject(signal.reason)
        signal.addEventListener('abort', onAbort, { once: true })
      })
      try {
        return await Promise.race([promise, stopped])
      } finally {
        signal.removeEventListener('abort', onAbort)
        promise.catch?.(() => {}) // a stopped pathfinder/dig rejects later; that's expected
      }
    },
    // Numbers that survive into the outcome even on timeout or stop.
    progress (data) {
      Object.assign(record.data, data)
    }
  }
}

export { ParamError }
