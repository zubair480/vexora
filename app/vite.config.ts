import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

// The Vexo SDK is used straight from the kit folder, unchanged (no npm package, no copy).
const kitJs = fileURLToPath(new URL('../reference/vexo-open-kit/js/src/index.js', import.meta.url))
// The kit dashboard's Deepgram live-transcript link (band Opus packets in, text out), unchanged.
const kitDeepgram = fileURLToPath(new URL('../reference/vexo-open-kit/dashboard/speech/deepgram.js', import.meta.url))

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { 'vexo-open': kitJs, 'vexo-deepgram': kitDeepgram } },
  server: { fs: { allow: ['..'] } },   // let the dev server read the kit outside app/
})
