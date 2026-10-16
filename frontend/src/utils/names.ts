export function initials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter((w) => /[A-Za-z\u0600-\u06FF]/.test(w))
      .slice(0, 2)
      .map((w) => w[0])
      .join('')
      .toUpperCase() || '?'
  );
}
