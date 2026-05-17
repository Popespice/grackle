export function generateId(): string {
  return Math.random().toString(36).slice(2);
}

export function capitalize(s: string): string {
  if (!s) return s;
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function formatDate(date: Date): string {
  return date.toISOString();
}
