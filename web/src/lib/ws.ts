// ws — URL WebSocket même-origine (servi par le daemon en prod, proxifié par Vite en dev). Partagé par
// le transcript de dispatch (`useDispatchStream`) et le terminal PTY (`TerminalPane`).
export function wsUrl(path: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}${path}`
}
