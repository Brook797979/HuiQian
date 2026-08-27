import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/web-api': 'http://127.0.0.1:3001',
    },
  },
  test: {
    environment: 'jsdom',
  },
})
