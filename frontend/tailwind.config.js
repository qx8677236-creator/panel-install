/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js}'],
  theme: {
    extend: {
      colors: {
        panel: {
          bg: '#f4f6f8',
          sidebar: '#1f2a38',
          green: '#20a53a',
          line: '#e7e9ed',
          text: '#2b2f36',
          muted: '#8b919a',
        },
      },
      fontFamily: {
        sans: ['"PingFang SC"', '"Hiragino Sans GB"', '"Microsoft YaHei"', 'system-ui', 'sans-serif'],
      },
      boxShadow: {
        card: '0 1px 2px rgba(16, 24, 40, 0.04)',
      },
    },
  },
  plugins: [],
}
