// Runs code she wrote (stage H) in a sealed box: a separate V8 isolate (isolated-vm) with no
// require, files, network, process or timers. The box gets exactly three things:
//   - skills.<name>(params): runs one of the pack's skills through the normal runner (stop, time
//     limits, low health, breathing and modes all apply) and returns its outcome
//     { status, reason, data }; bad params come back as status "invalid";
//   - state(): what she sees right now (readState), as plain data;
//   - log(...): lines kept with the result.
// Her code is the body of an async function; whatever it returns is the program's value.
//
// Safety for the whole program, not just each skill: stop() on the runner stops the program too,
// low health, death or a disconnect end it, and so does a skill ending as hurt or stopped. A time
// limit (wall clock) and a CPU budget (an endless loop) kill the box; the pack's own thread never
// waits on it. One program at a time, and while one runs nobody else can start a skill (her
// breathing reflex still can).
import { createRequire } from 'node:module'
import { randomUUID } from 'node:crypto'

import { BusyError, UnknownSkillError } from './skills/runner.js'
import { ParamError } from './skills/contract.js'
import { readState } from './state.js'

const require = createRequire(import.meta.url)
const ivm = require('isolated-vm')

export const PROGRAM_SECONDS = 300
const MEMORY_MB = 32
const CPU_MS = 2000 // her code's own thinking time; waiting on skills costs none
const CPU_CHECK_MS = 250
const MAX_CALLS = 40
const LOG_LINES = 50
const REFLEX_WAIT_MS = 25000

// Outcomes after which the program doesn't go on: she's hurt, someone stopped her, or she died.
const ENDS_PROGRAM = new Set(['hurt', 'stopped'])

class ProgramEnd extends Error {
  constructor (status, reason) {
    super(reason)
    this.status = status
    this.reason = reason
  }
}

// The box's side: skills.* and state() over the three references the host passes in.
const PRELUDE = `
const skills = new Proxy({}, {
  get (_, name) {
    if (typeof name !== 'string') return undefined
    return (params = {}) => _skill.apply(undefined, [name, JSON.stringify(params)],
      { arguments: { copy: true }, result: { promise: true } }).then((text) => JSON.parse(text))
  }
});
const state = () => JSON.parse(_state.applySync(undefined, [], { result: { copy: true } }));
const log = (...args) => _log.applySync(undefined,
  [args.map((a) => typeof a === 'string' ? a : JSON.stringify(a)).join(' ')], { arguments: { copy: true } });
`

// code: the body of an async function (her program). Resolves with
// { status, reason, value, calls, log, seconds }; never rejects for anything her code does.
export async function runProgram (runner, code, { seconds = PROGRAM_SECONDS, state = null, cpuMs = CPU_MS } = {}) {
  assertFree(runner)
  const { bot } = runner
  const readNow = state ?? (() => readState(bot))
  const started = Date.now()
  const calls = []
  const lines = []
  let end = null
  let finish
  const ended = new Promise((resolve) => { finish = resolve })
  const isolate = new ivm.Isolate({ memoryLimit: MEMORY_MB })

  const stopProgram = (status, reason) => {
    if (end) return
    end = new ProgramEnd(status, reason)
    finish(end)
    if (runner.current?.source === 'program') runner.stop(reason)
    if (!isolate.isDisposed) isolate.dispose()
  }
  const program = { stop: (reason = 'stopped by request') => stopProgram('stopped', reason) }
  runner.program = program

  const onDeath = () => stopProgram('failed', 'died')
  const onEnd = () => stopProgram('stopped', 'disconnected')
  bot.on('death', onDeath)
  bot.on('end', onEnd)
  const timer = setTimeout(() => stopProgram('timeout', `no result after ${seconds} s`), seconds * 1000)
  const cpu = setInterval(() => {
    if (!isolate.isDisposed && Number(isolate.cpuTime) / 1e6 > cpuMs) stopProgram('failed', `her code ran too long without a skill (over ${cpuMs} ms of thinking)`)
  }, CPU_CHECK_MS)

  async function callSkill (name, paramsText) {
    if (end) throw end
    if (calls.length >= MAX_CALLS) {
      stopProgram('failed', `more than ${MAX_CALLS} skill calls`)
      throw end
    }
    if (bot.health <= runner.lowHealth) {
      stopProgram('hurt', `health low (${Math.round(bot.health)}/20)`)
      throw end
    }
    // Her breathing reflex may be swimming her up between two calls: wait for it to finish.
    if (runner.current?.source === 'reflex') await runner.wait(runner.current.id, REFLEX_WAIT_MS)
    if (end) throw end
    let record
    try {
      record = runner.start(name, JSON.parse(paramsText), { source: 'program' })
    } catch (err) {
      if (!(err instanceof ParamError) && !(err instanceof UnknownSkillError)) {
        stopProgram('failed', err.message) // busy, most likely
        throw end
      }
      const outcome = { status: 'invalid', reason: err.message, data: {} }
      calls.push({ skill: name, params: safeParse(paramsText), ...outcome, seconds: 0 })
      return JSON.stringify(outcome)
    }
    calls.push(record) // the runner fills in its outcome; a stopped one shows up as stopped
    const finished = await runner.wait(record.id, (seconds + 5) * 1000)
    const outcome = { status: finished.status, reason: finished.reason, data: finished.data }
    if (!end && ENDS_PROGRAM.has(outcome.status)) stopProgram(outcome.status, `${name}: ${outcome.reason ?? outcome.status}`)
    if (end) throw end
    return JSON.stringify(outcome)
  }

  try {
    const context = await isolate.createContext()
    const global = context.global
    await global.set('_skill', new ivm.Reference((name, paramsText) => callSkill(name, paramsText)))
    await global.set('_state', new ivm.Reference(() => JSON.stringify(readNow())))
    await global.set('_log', new ivm.Reference((line) => {
      if (lines.length < LOG_LINES) lines.push(String(line).slice(0, 300))
    }))
    await context.eval(PRELUDE)
    // Her code on its own lines, so error line numbers are hers.
    const script = await isolate.compileScript(
      `(async () => { const value = await (async () => {\n${code}\n})(); return value === undefined ? undefined : JSON.stringify(value) })()`,
      { filename: 'program.js', lineOffset: -1 })
    const run = script.run(context, { promise: true, copy: true, timeout: cpuMs })
      .then((text) => ({ value: text === undefined ? undefined : JSON.parse(text) }))
      .catch((err) => ({ error: err }))
    const result = await Promise.race([run, ended])
    if (!end) {
      if (result.error) stopProgram('error', describeError(result.error))
      else {
        end = new ProgramEnd('done', null)
        end.value = result.value
      }
    }
  } catch (err) {
    if (!end) stopProgram('error', describeError(err)) // a syntax error, mostly
  } finally {
    clearTimeout(timer)
    clearInterval(cpu)
    bot.off('death', onDeath)
    bot.off('end', onEnd)
    if (!isolate.isDisposed) isolate.dispose()
    // A skill of hers that was stopped is still ending: she's free only once it has.
    const last = runner.current
    if (last?.source === 'program') await runner.wait(last.id, 10000)
    runner.program = null
  }
  return {
    status: end.status,
    reason: end.reason,
    value: end.value,
    calls: calls.map((c) => ({ skill: c.skill, params: c.params, status: c.status, reason: c.reason, data: c.data, seconds: c.seconds })),
    log: lines,
    seconds: Math.round((Date.now() - started) / 100) / 10
  }
}

// Throws BusyError unless a program could start now (her breathing reflex doesn't count: the
// program waits for it).
export function assertFree (runner) {
  if (runner.program) throw new BusyError('already running a program')
  if (runner.current && runner.current.source !== 'reflex') throw new BusyError(`already running ${runner.current.skill}`)
}

const PROGRAMS_KEPT = 10
const programs = [] // newest last: { id, status, ..., result fields once finished }

// For the API: starts a program and returns its record at once ({ id, status: 'running' }); the
// record fills in with runProgram's result when it ends. Throws BusyError like runProgram.
export function startProgram (runner, code, options = {}) {
  assertFree(runner)
  const record = { id: randomUUID(), status: 'running', started_at: new Date().toISOString() }
  programs.push(record)
  if (programs.length > PROGRAMS_KEPT) programs.splice(0, programs.length - PROGRAMS_KEPT)
  runProgram(runner, code, options)
    .then((result) => Object.assign(record, result))
    .catch((err) => Object.assign(record, { status: 'error', reason: err.message }))
  return { ...record }
}

// The record, waiting up to waitMs for it to finish.
export async function getProgram (id, waitMs = 0) {
  const record = programs.find((p) => p.id === id)
  const deadline = Date.now() + waitMs
  while (record?.status === 'running' && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 100))
  return record ? { ...record } : null
}

function safeParse (text) {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

// "ReferenceError: require is not defined [program.js:3:5]": the first line, plus where in her code.
function describeError (err) {
  const where = /program\.js:(\d+):(\d+)/.exec(err?.stack ?? '')
  const message = `${err?.name ?? 'Error'}: ${err?.message ?? String(err)}`
  return where ? `${message} (line ${where[1]})` : message
}
