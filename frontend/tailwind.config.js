/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      // 令牌只定义一次(src/index.css 里的 CSS 变量),这里只是把它们接到
      // Tailwind 的类名上,免得同一个颜色有两个真相。
      colors: {
        paper: "var(--paper)",
        "paper-raised": "var(--paper-raised)",
        "paper-sunk": "var(--paper-sunk)",
        ink: "var(--ink)",
        "ink-muted": "var(--ink-muted)",
        rule: "var(--rule)",
        start: "var(--start)",
        stop: "var(--stop)",
        continue: "var(--continue)",
        accent: "var(--accent)",
        danger: "var(--danger)",
      },
      fontFamily: {
        display: ['"Fraunces"', "ui-serif", "Georgia", "serif"],
        sans: ['"Public Sans"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
      },
      borderRadius: { DEFAULT: "var(--radius)" },
    },
  },
  plugins: [],
};
