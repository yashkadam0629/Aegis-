/// <reference types="vitest" />
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VITE_API_TARGET || process.env.VITE_API_TARGET || 'http://127.0.0.1:8000'
  return {
    plugins: [react()],
    server: { port: 5173, proxy: { '/api': { target, changeOrigin: true } } },
    build: { chunkSizeWarningLimit: 1500 },
    test: { environment: 'jsdom', globals: true, setupFiles: ['./src/test/setup.ts'], css: false },
  }
})
