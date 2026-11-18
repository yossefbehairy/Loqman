import tailwind from '@tailwindcss/postcss'

// Tailwind CSS 4 compiles the only stylesheet (src/rusebot/styles.css, which
// begins with @import "tailwindcss"). Vendor prefixing is handled by Tailwind 4.
export default {
  plugins: [tailwind()],
}
