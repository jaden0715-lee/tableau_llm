import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // 5173은 다른 프로젝트(KIS Option Dashboard)가 점유 → 5175 고정
  // Tableau Connected App 허용 목록·backend CORS도 localhost:5175 등록
  server: { port: 5175, strictPort: true },
})
