const RTL_LETTER = /[֐-ࣿיִ-﷿ﹰ-﻿\u{10800}-\u{10FFF}\u{1E800}-\u{1EFFF}]/u;

export function textDirection(value: string): 'rtl' | 'ltr' {
  const strong = /\p{Letter}/u.exec(value);
  return strong && RTL_LETTER.test(strong[0]) ? 'rtl' : 'ltr';
}
