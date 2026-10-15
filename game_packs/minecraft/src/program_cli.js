// Run a program by hand in her sandbox (src/sandbox.js) and wait for it, through the pack API.
//   bun run program my_program.js             the file is the body of an async function
//   bun run program -e "await skills.eat({})"
//   bun run skill stop                         stops it (and the skill it's running)
// Inside: await skills.<name>(params) -> { status, reason, data }, state(), log(...).
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

const args = process.argv.slice(2)
const code = args[0] === '-e' ? args[1] : args[0] && readFileSync(args[0], 'utf8')
if (!code) {
  console.error('usage: bun run program <file.js> | bun run program -e "<code>"')
  process.exit(1)
}
const started = await call('POST', '/programs', { code })
if (started.status !== 202) {
  console.error(`${started.status}: ${started.body.error}`)
  process.exit(1)
}
console.log('running her program (Ctrl+C here doesn\'t stop her: use "bun run skill stop")')
let program = started.body
while (program.status === 'running') program = (await call('GET', `/programs/${program.id}?wait=30`)).body
for (const c of program.calls ?? []) {
  console.log(`  ${c.skill} ${JSON.stringify(c.params)} -> ${c.status}${c.reason ? `: ${c.reason}` : ''}`)
}
for (const line of program.log ?? []) console.log(`  log: ${line}`)
console.log(`${program.status}${program.reason ? `: ${program.reason}` : ''} after ${program.seconds} s`)
if (program.value !== undefined) console.log(`returned ${JSON.stringify(program.value, null, 2)}`)
