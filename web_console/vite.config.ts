/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api/gui': {
        target: 'http://127.0.0.1:8642',
        changeOrigin: true,
        configure: (proxy, _options) => {
          // Strip the browser Origin header so the backend CORS middleware
          // doesn't reject proxied requests from the dev server.
          proxy.on('proxyReq', (proxyReq) => {
            proxyReq.removeHeader('Origin');
          });
          proxy.on('proxyRes', (proxyRes, req, _res) => {
            if (req.url?.includes('/stream/')) {
              proxyRes.headers['Cache-Control'] = 'no-cache';
              proxyRes.headers['X-Accel-Buffering'] = 'no';
            }
          });
        }
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts'
  }
});
