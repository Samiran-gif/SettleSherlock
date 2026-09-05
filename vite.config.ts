import { defineConfig, loadEnv, type Connect, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { aiStatus, runAnalysis } from './server/analyze'

/**
 * Serves the AI route from the Node side of Vite, in both `dev` and `preview`.
 *
 * This is what keeps the API key server-side: the key is read from the Node
 * environment inside this plugin. It is never added to `define`, never prefixed
 * with VITE_, and therefore never reachable from the browser bundle.
 */
function apiPlugin(env: NodeJS.ProcessEnv): Plugin {
  const handler: Connect.NextHandleFunction = (req, res, next) => {
    const url = req.url ?? ''

    const json = (status: number, body: unknown) => {
      res.statusCode = status
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify(body))
    }

    if (url.startsWith('/api/ai-status')) {
      const s = aiStatus(env)
      // Deliberately reports only availability -- never the key itself.
      json(200, { available: s.available, reason: s.reason, model: s.model })
      return
    }

    if (url.startsWith('/api/analyze')) {
      if (req.method !== 'POST') {
        json(405, { error: 'method_not_allowed' })
        return
      }
      let raw = ''
      let tooLarge = false
      req.on('data', (chunk: Buffer) => {
        raw += chunk.toString('utf8')
        // A single transaction payload is a few KB; anything larger is a bug.
        if (raw.length > 256_000) {
          tooLarge = true
          req.destroy()
        }
      })
      req.on('end', () => {
        if (tooLarge) {
          json(413, { error: 'payload_too_large' })
          return
        }
        let payload: unknown
        try {
          payload = JSON.parse(raw)
        } catch {
          json(400, { error: 'invalid_json' })
          return
        }
        runAnalysis(payload, env)
          .then((r) => json(r.status, r.body))
          .catch(() => json(500, { error: 'unexpected' }))
      })
      return
    }

    next()
  }

  return {
    name: 'settlement-api',
    configureServer(server) {
      server.middlewares.use(handler)
    },
    configurePreviewServer(server) {
      server.middlewares.use(handler)
    },
  }
}

export default defineConfig(({ mode }) => {
  // Third arg '' loads every key, including un-prefixed ones. These stay inside
  // the Node process; nothing here is exposed to the client.
  const fileEnv = loadEnv(mode, process.cwd(), '')
  const env: NodeJS.ProcessEnv = { ...fileEnv, ...process.env }

  return {
    plugins: [react(), apiPlugin(env)],
    server: {
      port: 5173,
      proxy: {
        '/api/backend': {
          // Same source of truth as the analyze bridge in server/analyze.ts.
          target: env.SETTLESHERLOCK_BACKEND_URL?.trim() || 'http://127.0.0.1:8010',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api\/backend/, ''),
          // Without this, an unreachable backend surfaces as a bodyless HTTP
          // 500, which the UI cannot tell apart from a genuine server fault.
          // Report the outage as 503 + JSON so the client can say so plainly.
          configure: (proxy) => {
            proxy.on('error', (err, _req, res) => {
              const socket = res as unknown as { writableEnded?: boolean }
              if (!('writeHead' in res) || socket.writableEnded) return
              const code = (err as NodeJS.ErrnoException).code ?? 'UNKNOWN'
              res.writeHead(503, { 'Content-Type': 'application/json' })
              res.end(
                JSON.stringify({
                  error: 'backend_unreachable',
                  code,
                  detail: `Cannot reach the SettleSherlock backend (${code}).`,
                }),
              )
            })
          },
        },
      },
    },
    build: { outDir: 'dist', sourcemap: false },
  }
})

