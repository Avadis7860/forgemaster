// href.ts — classification d'un lien pour le rendu d'ancre. Logique PURE (testable sans runtime Astro), câblée
// par la primitive `Button`. Elle existe pour tuer un footgun précis du drain avagency : un `Button external`
// forçait `target="_blank"` (+ annonce a11y « nouvel onglet ») MÊME sur un `mailto:`/`tel:`. Or `mailto:`/`tel:`
// ne « s'ouvrent » pas dans un onglet — ils passent la main au client mail/téléphone. Leur coller `_blank` +
// une annonce « ouvre un nouvel onglet » est un mensonge d'accessibilité. Ici, le nouvel onglet est réservé aux
// liens HTTP(S) explicitement externes ; `mailto:`/`tel:` sont TOUJOURS des actions directes, même contexte.

export type HrefScheme = 'mailto' | 'tel' | 'http' | 'https' | 'relative';

export interface HrefClass {
  scheme: HrefScheme;
  /** Vrai UNIQUEMENT pour un lien HTTP(S) marqué externe — jamais pour mailto/tel. */
  isNewTab: boolean;
  /** `_blank` si (et seulement si) `isNewTab`. */
  target?: '_blank';
  /** Durcissement obligatoire d'un `_blank` (anti tabnabbing). */
  rel?: 'noopener noreferrer';
}

/** Le schéma d'un href. Insensible à la casse sur le protocole ; tout le reste (chemins, ancres, protocol-relative)
 *  est « relative » (même document/onglet). */
export function schemeOf(href: string): HrefScheme {
  const h = href.trim().toLowerCase();
  if (h.startsWith('mailto:')) return 'mailto';
  if (h.startsWith('tel:')) return 'tel';
  if (h.startsWith('http://')) return 'http';
  if (h.startsWith('https://')) return 'https';
  return 'relative';
}

/** Classe un href pour le rendu. `external` n'a d'effet QUE sur un lien HTTP(S) : c'est la garde qui empêche
 *  un `mailto:`/`tel:` de recevoir `_blank`/`rel`/annonce nouvel-onglet. */
export function classifyHref(href: string, opts: { external?: boolean } = {}): HrefClass {
  const scheme = schemeOf(href);
  const isNewTab = Boolean(opts.external) && (scheme === 'http' || scheme === 'https');
  if (isNewTab) return { scheme, isNewTab, target: '_blank', rel: 'noopener noreferrer' };
  return { scheme, isNewTab };
}
