import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 允許 ngrok 的網域連線進來
    allowedHosts: [
      'flavorous-wilber-unfestering.ngrok-free.dev',
      
      // 💡 小秘訣：因為 ngrok 每次重開網址都會變，
      // 你也可以直接加入 '.ngrok-free.dev'，這樣以後不管網址前面怎麼變，都不用再回來改這行了！
      '.ngrok-free.dev' 
    ]
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
  },
})