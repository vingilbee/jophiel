// A real vanilla server for skill tests: flat world, accounts off, loopback, a free port, a fresh
// world every run, set up through the server console (setblock, summon, give...).
// Runs only if the user has accepted the Minecraft EULA for their own server (server/eula.txt).
import { spawn } from 'node:child_process'
import { existsSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:net'
import { join, resolve } from 'node:path'
import { createRequire } from 'node:module'

import { DEFAULT_VERSION } from '../../src/config.js'
import { ensureJar, findJava } from '../../scripts/server.js'

const require = createRequire(import.meta.url)
const mineflayer = require('mineflayer')

const DIR = resolve('var/testserver')

export function eulaAccepted () {
  return existsSync('server/eula.txt') && /eula=true/.test(readFileSync('server/eula.txt', 'utf8'))
}

function freePort () {
  return new Promise((resolve, reject) => {
    const probe = createServer()
    probe.once('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address()
      probe.close(() => resolve(port))
    })
  })
}

export async function startServer ({ version = DEFAULT_VERSION } = {}) {
  const { java: major } = await ensureJar(version, DIR)
  rmSync(join(DIR, 'world'), { recursive: true, force: true })
  const port = await freePort()
  writeFileSync(join(DIR, 'eula.txt'), 'eula=true\n')
  writeFileSync(join(DIR, 'server.properties'), Object.entries({
    'server-ip': '127.0.0.1',
    'server-port': port,
    'online-mode': false,
    'enforce-secure-profile': false,
    'level-name': 'world',
    'level-type': 'minecraft\\:flat',
    'generate-structures': false,
    difficulty: 'peaceful',
    'spawn-protection': 0,
    'view-distance': 6,
    'simulation-distance': 6,
    'max-players': 4,
    motd: 'skill tests'
  }).map(([k, v]) => `${k}=${v}`).join('\n') + '\n')

  const child = spawn(findJava(major), ['-Xms512M', '-Xmx1G', '-jar', 'server.jar', 'nogui'], {
    cwd: DIR, stdio: ['pipe', 'pipe', 'pipe']
  })
  const lines = []
  const waiters = new Set()
  let buffer = ''
  const onData = (chunk) => {
    buffer += chunk.toString('utf8')
    let i
    while ((i = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, i)
      buffer = buffer.slice(i + 1)
      lines.push(line)
      if (process.env.SHOW_SERVER) console.log('[server]', line)
      for (const w of waiters) if (w.regex.test(line)) w.resolve(line)
    }
  }
  child.stdout.on('data', onData)
  child.stderr.on('data', onData)

  const waitFor = (regex, ms = 10000) => new Promise((resolve, reject) => {
    const w = {
      regex,
      resolve: (line) => {
        clearTimeout(timer)
        waiters.delete(w)
        resolve(line)
      }
    }
    const timer = setTimeout(() => {
      waiters.delete(w)
      reject(new Error(`server never printed ${regex}`))
    }, ms)
    waiters.add(w)
  })

  await waitFor(/Done \(/, 180000)
  return {
    port,
    lines,
    waitFor,
    // Run a console command; with `expect`, wait for its reply line.
    async command (cmd, expect = null, ms = 5000) {
      const reply = expect ? waitFor(expect, ms) : null
      child.stdin.write(cmd + '\n')
      return reply ?? new Promise((resolve) => setTimeout(resolve, 150))
    },
    stop () {
      return new Promise((resolve) => {
        child.once('exit', resolve)
        child.stdin.write('stop\n')
        setTimeout(() => child.kill('SIGKILL'), 20000)
      })
    }
  }
}

export function joinBot (port, username, version = DEFAULT_VERSION) {
  const bot = mineflayer.createBot({ host: '127.0.0.1', port, username, auth: 'offline', version })
  return new Promise((resolve, reject) => {
    bot.once('spawn', () => resolve(bot))
    bot.once('kicked', (reason) => reject(new Error(`kicked: ${JSON.stringify(reason)}`)))
    bot.once('error', reject)
  })
}

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

// Wait until fn() is truthy, polling.
export async function until (fn, ms = 5000, step = 100) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    const value = fn()
    if (value) return value
    await sleep(step)
  }
  throw new Error('condition never became true')
}
