import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Allow overriding backend target via env (useful for host vs container runs)
const backendTarget = process.env.BACKEND_URL || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 3000,
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
        secure: false,
      }
    }
  }
})
