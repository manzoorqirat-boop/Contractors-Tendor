import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development the page runs on :5173 and forwards /api to the Python service on :8000.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: { outDir: 'dist', sourcemap: false },
});
