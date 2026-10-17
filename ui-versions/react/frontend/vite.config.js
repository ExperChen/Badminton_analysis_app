import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/static/react-ui/',
  build: {
    outDir: '../static/react-ui',
    emptyOutDir: true,
    manifest: true,
    rollupOptions: { input: 'src/main.jsx' }
  }
});
