import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development the page runs on :5173 and forwards /api to the Python service on :8000.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      // The charting library is the largest dependency. Its own file is cached across releases and loads in parallel with the app.
      output: { manualChunks: (id) => (id.includes('node_modules/recharts') || id.includes('node_modules/d3-') || id.includes('node_modules/victory-vendor') ? 'charts' : id.includes('node_modules/react') ? 'react' : undefined) },
    },
  },
});
