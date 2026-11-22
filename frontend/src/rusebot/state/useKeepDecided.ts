import { useCallback, useState } from 'react';

/**
 * Pending lists drop an item the moment it is decided. Keeping the decided
 * ones lets the card show its outcome instead of vanishing under the user's
 * cursor. `decide` registers the item (with its position) before the decision
 * runs, so the card neither unmounts nor jumps when the list refreshes, and
 * forgets it again if the decision fails.
 */
export function useKeepDecided<T extends { id: string }>(
  items: T[]
) {
  const [decided, setDecided] = useState<Record<string, { item: T; index: number }>>({});
  const [current, setCurrent] = useState<T[]>(items);
  if (current !== items && (current.length !== items.length || current.some((c, i) => c.id !== items[i]?.id))) {
    setCurrent(items);
  }
  const decide = useCallback(
    async <R,>(item: T, run: (confirm: (updated: T) => void) => Promise<R>): Promise<R> => {
      setDecided((prev) => ({ ...prev, [item.id]: { item, index: Math.max(0, current.findIndex((c) => c.id === item.id)) } }));
      try {
        return await run((updated) => setDecided((prev) => {
          const entry = prev[item.id];
          return entry ? { ...prev, [item.id]: { ...entry, item: updated } } : prev;
        }));
      } catch (e) {
        setDecided((prev) => {
          const next = { ...prev };
          delete next[item.id];
          return next;
        });
        throw e;
      }
    },
    [current]
  );
  const shown = [...items];
  const kept = Object.values(decided)
    .filter((d) => !items.some((i) => i.id === d.item.id))
    .sort((a, b) => a.index - b.index);
  for (const d of kept) shown.splice(Math.min(d.index, shown.length), 0, d.item);
  return { shown, decide, retained: decided };
}
