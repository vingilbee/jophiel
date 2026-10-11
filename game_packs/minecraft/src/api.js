// The pack's local API: loopback only, bearer token on every request, JSON in and out.
//   GET  /health                      -> { connected, spawned }
//   GET  /state                       -> readState() JSON plus a `text` rendering
//   GET  /skills                      -> the skill manifest
//   POST /skills/run  {skill, params, source?} -> 202 the new run | 409 busy | 400 bad params
//   GET  /skills/runs/<id>?wait=<s>   -> the run; waits up to <s> (max 60) for it to finish
//   GET  /skills/current              -> the running skill, or null
//   POST /skills/stop                 -> stops the running skill (pause/takeover come here)
//   GET  /skills/history              -> recent finished runs
//   GET  /notices?since=<seq>&wait=<s> -> { next, notices }: what she noticed after <seq>, waiting
//                                        up to <s> (max 60) for something new
//   GET  /recipes/<item>              -> what it takes to make <item>, against what she carries
//   GET  /modes                       -> background modes { guard, gather: [blocks], pickup }
//   POST /modes {guard?, gather?, pickup?} -> sets the ones given; 400 for an unknown block name
import { createServer } from 'node:http'
import { timingSafeEqual } from 'node:crypto'

import { ParamError } from './skills/contract.js'
import { BusyError, UnknownSkillError } from './skills/runner.js'

const MAX_BODY = 16 * 1024

function tokenMatches (header, token) {
  const given = Buffer.from(header ?? '')
  const expected = Buffer.from(`Bearer ${token}`)
  return given.length === expected.length && timingSafeEqual(given, expected)
}

function send (res, status, body) {
  res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' })
  res.end(JSON.stringify(body))
}

function readJson (req) {
  return new Promise((resolve, reject) => {
    if (!(req.headers['content-type'] ?? '').startsWith('application/json')) {
      return reject(new ParamError('send JSON (content-type: application/json)'))
    }
    let size = 0
    const chunks = []
    req.on('data', (chunk) => {
      size += chunk.length
      if (size > MAX_BODY) {
        reject(new ParamError('body too large'))
        req.destroy()
      } else {
        chunks.push(chunk)
      }
    })
    req.on('end', () => {
      try {
        resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {})
      } catch {
        reject(new ParamError('body is not valid JSON'))
      }
    })
    req.on('error', reject)
  })
}

// pack: { status(), state(), runner() -> SkillRunner|null, manifest(), noticing() -> noticing|null, modes() -> ModeSettings,
//         recipe(item) }
export function startApi ({ host, port, token, pack }) {
  const allowedHosts = new Set([`${host}:${port}`, `localhost:${port}`])

  async function route (req, res) {
    const url = new URL(req.url, 'http://local')
    const path = url.pathname
    const key = `${req.method} ${path}`
    const runner = pack.runner()
    const needsWorld = () => {
      if (!pack.status().spawned || !runner) {
        send(res, 503, { error: 'not in the world' })
        return true
      }
      return false
    }

    if (key === 'GET /health') return send(res, 200, pack.status())
    if (key === 'GET /state') return needsWorld() || send(res, 200, pack.state())
    if (key === 'GET /skills') return send(res, 200, pack.manifest())
    if (key === 'GET /skills/current') return send(res, 200, runner?.current ?? null)
    if (key === 'GET /skills/history') return send(res, 200, runner?.history() ?? [])
    if (key === 'POST /skills/stop') return send(res, 200, runner?.stop() ?? null)
    if (key === 'POST /skills/run') {
      if (needsWorld()) return
      const body = await readJson(req)
      if (typeof body.skill !== 'string') throw new ParamError('skill is required')
      const source = body.source === 'driver' ? 'driver' : 'user'
      return send(res, 202, runner.start(body.skill, body.params ?? {}, { source }))
    }
    if (key === 'GET /modes') return send(res, 200, pack.modes().toJSON())
    if (key === 'POST /modes') {
      if (needsWorld()) return // block names are checked against the world's version
      return send(res, 200, pack.modes().set(await readJson(req), runner.bot))
    }
    if (key === 'GET /notices') {
      const noticing = pack.noticing?.()
      if (!noticing) return send(res, 503, { error: 'not in the world' })
      const since = Math.max(Number(url.searchParams.get('since') ?? 0) || 0, 0)
      const wait = Math.min(Math.max(Number(url.searchParams.get('wait') ?? 0) || 0, 0), 60)
      const notices = await noticing.wait(since, wait * 1000)
      return send(res, 200, { next: notices.at(-1)?.seq ?? Math.max(since, 0), notices })
    }
    const recipe = /^\/recipes\/([a-z0-9_]{1,64})$/.exec(path)
    if (recipe && req.method === 'GET') return needsWorld() || send(res, 200, pack.recipe(recipe[1]))
    const run = /^\/skills\/runs\/([0-9a-f-]{36})$/.exec(path)
    if (run && req.method === 'GET') {
      const wait = Math.min(Math.max(Number(url.searchParams.get('wait') ?? 0) || 0, 0), 60)
      const record = runner && (wait ? await runner.wait(run[1], wait * 1000) : runner.get(run[1]))
      return record ? send(res, 200, record) : send(res, 404, { error: 'no such run' })
    }
    return send(res, 404, { error: 'not found' })
  }

  const server = createServer((req, res) => {
    // A browser page can't reach us through DNS rebinding: the Host header must be ours.
    if (!allowedHosts.has(req.headers.host)) return send(res, 421, { error: 'wrong host' })
    if (!tokenMatches(req.headers.authorization, token)) return send(res, 401, { error: 'unauthorized' })
    route(req, res).catch((err) => {
      if (err instanceof ParamError || err instanceof UnknownSkillError) return send(res, 400, { error: err.message })
      if (err instanceof BusyError) return send(res, 409, { error: err.message })
      send(res, 500, { error: err.message })
    })
  })
  return new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(port, host, () => resolve(server))
  })
}
