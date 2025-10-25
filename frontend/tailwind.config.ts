import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        serif: ["\"Playfair Display\"", "Georgia", "serif"],
      },
      colors: {
        background: "#f7f7f7",
        foreground: "#111111",
        muted: "#e9e9e9",
        accent: "#1f1f1f",
      },
      borderRadius: {
        "2xl": "1.25rem",
      },
    },
  },
  plugins: [],
};

export default config;
