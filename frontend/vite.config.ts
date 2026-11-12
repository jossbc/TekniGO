import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backendTarget = 'http://127.0.0.1:8000'

const devProxy = {
  '/technicians': { target: backendTarget, changeOrigin: true },
  '/direct-requests': { target: backendTarget, changeOrigin: true },
  '/users': { target: backendTarget, changeOrigin: true },
  '/auth': { target: backendTarget, changeOrigin: true },
  '/specialties': { target: backendTarget, changeOrigin: true },
  '/admin/': { target: backendTarget, changeOrigin: true },
  '/open-requests': { target: backendTarget, changeOrigin: true },
  '/health': { target: backendTarget, changeOrigin: true },
}

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy: devProxy },
  preview: { proxy: devProxy },
})
