export const TEXT_SIZE_IDS = ['small', 'default', 'large'] as const;
export type TextSizeId = typeof TEXT_SIZE_IDS[number];
const KEY = 'rusebot-text-size';

export function readTextSize(): TextSizeId {
  try {
    const value = localStorage.getItem(KEY);
    return TEXT_SIZE_IDS.includes(value as TextSizeId) ? value as TextSizeId : 'default';
  } catch { return 'default'; }
}

export function applyTextSize(value: TextSizeId): void {
  document.documentElement.dataset.textSize = value;
  try { localStorage.setItem(KEY, value); } catch { /* Storage can be unavailable. */ }
}
