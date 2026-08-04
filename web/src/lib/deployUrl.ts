// deployUrl — URL cliquable d'un produit déployé, **relative au point de vue de l'opérateur**.
//
// Le backend persiste `deployment.url` comme référence loopback ON-HOST (`http://127.0.0.1:<port>`) :
// honnête pour la CLI/curl exécutés SUR l'hôte du daemon, mais faux comme lien cliquable quand on consulte
// le forgemaster depuis une AUTRE machine (le navigateur résout `127.0.0.1` sur la machine du viewer, pas sur
// l'hôte). Le host joignable d'un produit, depuis le navigateur, est celui par lequel on a atteint le
// forgemaster — exactement le raisonnement de `ws.ts` (`window.location.host`). On recompose donc le lien à
// partir du hostname courant + le port publié, jamais du `url` loopback stocké.
//
// http (pas https) : le produit compose publie un port brut en clair sur l'hôte (LAN direct). Le mapping
// TLS/sous-domaine par reverse-proxy est un horizon multi-tenant (non couvert ici, ancré sur le besoin actuel).
export function deployUrl(port: number): string {
  return `http://${window.location.hostname}:${port}`
}
