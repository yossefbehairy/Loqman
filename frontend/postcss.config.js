import postcss from 'postcss'
import tailwindV3 from 'tailwindcss'
import autoprefixer from 'autoprefixer'
import tailwindV4 from '@tailwindcss/postcss'

// The classic AgentOS UI is built with Tailwind 3. The ported RuseBot UI
// (src/rusebot/) is written for Tailwind 4, so its stylesheet is compiled by
// the v4 PostCSS plugin. Each stylesheet goes through exactly one pipeline.
const classic = postcss([tailwindV3(), autoprefixer()])
const rusebot = postcss([tailwindV4()])

const RUSEBOT_DIR = /[\\/]src[\\/]rusebot[\\/]/

const splitByEntry = {
  postcssPlugin: 'agentos-tailwind-split',
  async Once(root, { result }) {
    const file = root.source?.input?.file ?? result.opts.from ?? ''
    const pipeline = RUSEBOT_DIR.test(file) ? rusebot : classic
    // Processes the root in place; the nested run is awaited before this plugin finishes.
    const out = await pipeline.process(root, { from: file, map: false })
    for (const message of out.messages) result.messages.push(message)
  },
}

export default {
  plugins: [splitByEntry],
}
