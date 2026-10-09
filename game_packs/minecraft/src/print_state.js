// Print what she currently knows, through the pack API (what the backend will call).
//   node --env-file-if-exists=.env src/print_state.js [--json]
import { readFileSync } from 'node:fs'
import { loadConfig } from './config.js'

const config = loadConfig()
const token = config.apiToken || readFileSync('var/api_token', 'utf8').trim()
const res = await fetch(`http://127.0.0.1:${config.apiPort}/state`, {
  headers: { authorization: `Bearer ${token}` }
})
const body = await res.json()
if (!res.ok) {
  console.error(`${res.status}: ${body.error}`)
  process.exit(1)
}
console.log(process.argv.includes('--json') ? JSON.stringify(body, null, 2) : body.text)
