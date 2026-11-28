/** The worker stores UTC. Older responses carry no offset; read those as UTC, not as local time. */
export function parseWorkerTime(iso: string): Date {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso);
  return new Date(!hasZone && /T\d/.test(iso) ? `${iso}Z` : iso);
}
