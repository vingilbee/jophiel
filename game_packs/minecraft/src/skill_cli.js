// Run one skill by hand and wait for its outcome, through the pack API.
//   bun run skill mine block=log count=3
//   bun run skill go_to "target=player:<their name>"
//   bun run skill stop
import { readFileSync } from 'node:fs'
import { loadConfig } from './config.js'

const config = loadConfig()
const token = config.apiToken || readFileSync('var/api_token', 'utf8').trim()
const base = `http://127.0.0.1:${config.apiPort}`
const call = (method, path, body) => fetch(base + path, {
  method,
  headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
  body: body ? JSON.stringify(body) : undefined
}).then(async (res) => ({ status: res.status, body: await res.json() }))

const [skill, ...pairs] = process.argv.slice(2)
if (!skill) {
  const { body } = await call('GET', '/skills')
  for (const s of body) console.log(`${s.name}: ${s.description}\n  params: ${Object.keys(s.params).join(', ')}`)
  process.exit(0)
}
if (skill === 'stop') {
  console.log(JSON.stringify((await call('POST', '/skills/stop')).body))
  process.exit(0)
}

// key=value; numbers stay numbers.
const params = Object.fromEntries(pairs.map((pair) => {
  const [key, ...rest] = pair.split('=')
  const value = rest.join('=')
  return [key, /^-?\d+(\.\d+)?$/.test(value) ? Number(value) : value]
}))
const started = await call('POST', '/skills/run', { skill, params })
if (started.status !== 202) {
  console.error(`${started.status}: ${started.body.error}`)
  process.exit(1)
}
console.log(`running ${skill} ${JSON.stringify(started.body.params)} (Ctrl+C here doesn't stop her: use "bun run skill stop")`)
let run = started.body
while (run.status === 'running') run = (await call('GET', `/skills/runs/${run.id}?wait=30`)).body
console.log(`${run.status}${run.reason ? `: ${run.reason}` : ''} after ${run.seconds} s ${JSON.stringify(run.data)}`)
