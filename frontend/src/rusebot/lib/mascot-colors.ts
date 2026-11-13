/*
 * The colour table is copied from RuseBot 0.1.127 src/lib/mascot.ts (Apache-2.0,
 * Copyright 2026 Milind Soni and OpenMausBot contributors); the mascot art and
 * motion code are not used.
 */
export type MascotColor =
  | 'green' | 'blue' | 'red' | 'orange' | 'purple' | 'cyan' | 'pink' | 'yellow' | 'teal' | 'coral' | 'silver' | 'chrome';

export const MASCOT_COLORS = {
  green: '#009957',
  blue: '#377FE6',
  red: '#D94B52',
  orange: '#E78531',
  purple: '#8057C8',
  cyan: '#0EA5C6',
  pink: '#D84F8B',
  yellow: '#D8A729',
  teal: '#01A492',
  coral: '#E5634E',
  silver: '#A8ADB4',
  chrome: '#B3BAC4',
} satisfies Record<MascotColor, string>;
