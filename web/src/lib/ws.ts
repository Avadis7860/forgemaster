// ws — URL WebSocket même-origine (servi par le daemon en prod, proxifié par Vite en dev). Partagé par
// le transcript de dispatch (`useDispatchStream`) et le terminal PTY (`TerminalPane`).
export function wsUrl(path: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}${path}`
}

/** Flavor de session PTY d'un projet. `shell` = login `bash -l` (surface `claude login`) ; `interview` =
 *  session dédiée dont le process EST `cockpit interview` (routes serveur distinctes, clés de registre
 *  distinctes → sessions indépendantes). */
export type PtySession = 'shell' | 'interview'

/** Chemin WS PTY d'un projet selon son flavor — PUR (testable sans DOM). `shell` → `/ws/terminal/{p}` ;
 *  `interview` → `/ws/interview/{p}`. Le projet est URL-encodé. */
export function ptyPath(project: string, session: PtySession): string {
  const p = encodeURIComponent(project)
  return session === 'interview' ? `/ws/interview/${p}` : `/ws/terminal/${p}`
}
