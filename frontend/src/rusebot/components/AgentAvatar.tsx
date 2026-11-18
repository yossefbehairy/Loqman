/*
 * Round initial badge tinted with a stable per-agent identity colour, so chats with
 * different agents can be told apart at a glance. The colour comes from the skin's
 * --identity-* palette (each skin tunes it for contrast), picked by hashing a stable
 * key such as the agent's profile id. Purely visual: no behaviour lives here.
 */
import type { CSSProperties } from 'react';
import { cn } from '../lib/cn';

const HUES = ['blue', 'orange', 'green', 'pink', 'purple', 'yellow', 'teal', 'coral', 'cyan'] as const;

/** FNV-1a: the same key always maps to the same hue across sessions. */
function identityHue(key: string, index?: number): (typeof HUES)[number] {
  // A position in the team list keeps the first agents on distinct colours; a hash alone collides early.
  if (index !== undefined && index >= 0) return HUES[index % HUES.length];
  let h = 0x811c9dc5;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return HUES[(h >>> 0) % HUES.length];
}

function initialOf(label: string): string {
  return Array.from(label.trim())[0]?.toUpperCase() || '?';
}

export function AgentAvatar({
  identity,
  label,
  index,
  size = 'md',
  className,
}: {
  /** Stable key for the colour (profile id). Without one the badge stays neutral. */
  identity?: string | null;
  /** Text the initial is taken from. */
  label: string;
  /** Position of the agent in the team list, when known: picks the colour in order. */
  index?: number;
  size?: 'sm' | 'md';
  className?: string;
}) {
  const hue = identity ? identityHue(identity, index) : null;
  const style = hue ? ({ '--avatar-hue': `var(--identity-${hue})` } as CSSProperties) : undefined;
  return (
    <span
      aria-hidden="true"
      data-agent-avatar={hue ?? 'neutral'}
      className={cn('agent-avatar', size === 'sm' ? 'agent-avatar-sm' : 'agent-avatar-md', className)}
      style={style}
    >
      {initialOf(label)}
    </span>
  );
}
