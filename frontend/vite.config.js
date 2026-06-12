import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// Allow overriding backend target via env (useful for host vs container runs)
const backendTarget = process.env.BACKEND_URL || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  // Read .env from project root (one level up from frontend/) instead of frontend/
  envDir: path.resolve(__dirname, '..'),
  server: {
    host: true,
    port: 3000,
    allowedHosts: 'all',
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
        secure: false,
      }
    }
  }
})
