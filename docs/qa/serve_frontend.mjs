// Separate Vite server proxies only to the disposable QA backend.
import { createServer } from '../../frontend/node_modules/vite/dist/node/index.js'
const live = process.argv.includes('--live')
const server = await createServer({
  root: new URL('../../frontend/', import.meta.url).pathname,
  server: {
    host: '127.0.0.1', port: 54173, strictPort: true,
    proxy: { '/api': { target: live ? 'http://127.0.0.1:58001' : 'http://127.0.0.1:58000', changeOrigin: true, ws: true } },
  },
})
await server.listen()
server.printUrls()
