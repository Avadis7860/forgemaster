/** Joint des classes en filtrant les valeurs falsy. Pas de dépendance (clsx-like minimal). */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}
