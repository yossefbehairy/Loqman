// Scale typography, never geometry. Run after Tailwind so arbitrary pixel utilities
// and named text tokens participate alongside component CSS. Relative em sizes and
// unitless line heights naturally follow their parent and must not scale twice.
export default function textSize() {
  return {
    postcssPlugin: 'loqman-text-size',
    OnceExit(root) {
      const file = root.source?.input.file?.replaceAll('\\', '/') ?? '';
      if (!file.includes('/src/rusebot/')) return;
      root.walkDecls(decl => {
        const typeToken = /^--text-[\w-]+$/.test(decl.prop) && !decl.prop.endsWith('--line-height');
        if (!typeToken && !['font-size', 'line-height'].includes(decl.prop)) return;
        if (/^\d*\.?\d+(px|rem)$/.test(decl.value)) {
          decl.value = `calc(${decl.value} * var(--ui-text-scale, 1))`;
        }
      });
    },
  };
}
