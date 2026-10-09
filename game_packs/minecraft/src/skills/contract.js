// The skill contract (plan doc, stage G): one goal, one fast loop, no model inside, no calling
// other skills. A few checkable parameters. Always ends with exactly one outcome.

export const OUTCOMES = ['done', 'failed', 'lost', 'blocked', 'hurt', 'timeout', 'stopped']

// Thrown inside a skill (or by the runner through ctx.check()) to end it with an outcome.
export class SkillEnd extends Error {
  constructor (status, reason = null, data = {}) {
    super(reason ? `${status}: ${reason}` : status)
    if (!OUTCOMES.includes(status)) throw new Error(`unknown outcome ${status}`)
    this.status = status
    this.reason = reason
    this.data = data
  }
}

export const done = (data = {}, reason = null) => new SkillEnd('done', reason, data)
export const failed = (reason, data = {}) => new SkillEnd('failed', reason, data)

export class ParamError extends Error {}

// Parameter specs: { type: 'string'|'number'|'integer'|'enum', required, default, min, max,
// values (enum), description }. Every skill also gets time_limit (seconds).
export function validateParams (spec, input = {}) {
  if (input === null || typeof input !== 'object' || Array.isArray(input)) {
    throw new ParamError('params must be an object')
  }
  for (const key of Object.keys(input)) {
    if (!(key in spec)) throw new ParamError(`unknown parameter ${key}`)
  }
  const out = {}
  for (const [key, rule] of Object.entries(spec)) {
    let value = input[key]
    if (value === undefined || value === null) {
      if (rule.required) throw new ParamError(`${key} is required`)
      if (rule.default !== undefined) out[key] = rule.default
      continue
    }
    if (rule.type === 'string' || rule.type === 'enum') {
      if (typeof value !== 'string' || !value.trim()) throw new ParamError(`${key} must be a non-empty string`)
      value = value.trim()
      if (value.length > 100) throw new ParamError(`${key} is too long`)
      if (rule.type === 'enum' && !rule.values.includes(value)) {
        throw new ParamError(`${key} must be one of ${rule.values.join(', ')}`)
      }
    } else if (rule.type === 'number' || rule.type === 'integer') {
      if (typeof value !== 'number' || !Number.isFinite(value)) throw new ParamError(`${key} must be a number`)
      if (rule.type === 'integer' && !Number.isInteger(value)) throw new ParamError(`${key} must be a whole number`)
      if (rule.min !== undefined && value < rule.min) throw new ParamError(`${key} must be at least ${rule.min}`)
      if (rule.max !== undefined && value > rule.max) throw new ParamError(`${key} must be at most ${rule.max}`)
    } else {
      throw new Error(`bad spec type ${rule.type}`)
    }
    out[key] = value
  }
  return out
}

export function timeLimitParam (defaultSeconds, maxSeconds) {
  return {
    type: 'number',
    min: 1,
    max: maxSeconds,
    default: defaultSeconds,
    description: `seconds before it gives up (default ${defaultSeconds}, at most ${maxSeconds})`
  }
}

// What the driver's strict schema will be generated from (phase 3).
export function manifestEntry (skill) {
  return {
    name: skill.name,
    description: skill.description,
    done_when: skill.doneWhen,
    stops_when: skill.stopsWhen,
    params: Object.fromEntries(Object.entries(skill.params).map(([key, rule]) => [key, {
      type: rule.type,
      required: Boolean(rule.required),
      ...(rule.default !== undefined ? { default: rule.default } : {}),
      ...(rule.min !== undefined ? { min: rule.min } : {}),
      ...(rule.max !== undefined ? { max: rule.max } : {}),
      ...(rule.values ? { values: rule.values } : {}),
      description: rule.description
    }]))
  }
}
