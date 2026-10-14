// Watch-only 3D view of her world in the browser. Same as prismarine-viewer's mineflayer()
// helper, but bound to loopback (theirs listens on every interface) and with no way to act:
// block clicks are ignored.
import { createServer } from 'node:http'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const express = require('express')
const { Server } = require('socket.io')
const { WorldView } = require('prismarine-viewer/viewer/lib/worldView') // not viewer/index: that pulls in node-canvas
const { setupRoutes } = require('prismarine-viewer/lib/common')
const { getVersion } = require('prismarine-viewer/viewer/lib/version')

// The viewer ships graphics per version (up to 1.21.4 in 1.33.0); newer worlds can't be drawn.
export function viewerSupports (version) {
  return getVersion(version) !== null
}

export function startViewer (bot, { host, port, firstPerson, viewDistance }) {
  const app = express()
  setupRoutes(app)
  const http = createServer(app)
  const io = new Server(http)

  io.on('connection', (socket) => {
    socket.emit('version', bot.version)
    const worldView = new WorldView(bot.world, viewDistance, bot.entity.position, socket)
    worldView.init(bot.entity.position)
    const onMove = () => {
      const packet = { pos: bot.entity.position, yaw: bot.entity.yaw, addMesh: true }
      if (firstPerson) packet.pitch = bot.entity.pitch
      socket.emit('position', packet)
      worldView.updatePosition(bot.entity.position)
    }
    bot.on('move', onMove)
    worldView.listenToBot(bot)
    socket.on('disconnect', () => {
      bot.removeListener('move', onMove)
      worldView.removeListenersFromBot(bot)
    })
  })

  http.listen(port, host)
  return {
    close () {
      io.close()
      http.close()
    }
  }
}
