import { useEffect, useRef, useState } from 'react'
import { useBlocker } from '@tanstack/react-router'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { SearchAddon } from '@xterm/addon-search'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import { Badge, Button, Input } from '@/components/ui'
import { useWsToken } from '@/lib/queries'
import { ptyPath, tokenProtocols, wsUrl, type PtySession } from '@/lib/ws'
import { buildTheme } from './theme'
import { LeaveTerminalConfirm } from './LeaveTerminalConfirm'
import { InterviewEndState } from './InterviewEndState'

export type TermStatus = 'connecting' | 'connected' | 'closed' | 'error'

const STATUS: Record<TermStatus, { tone: 'info' | 'ok' | 'neutral' | 'danger'; label: string }> = {
  connecting: { tone: 'info', label: 'connexion…' },
  connected: { tone: 'ok', label: 'connecté' },
  closed: { tone: 'neutral', label: 'fermé' },
  error: { tone: 'danger', label: 'erreur' },
}

const FONT_MIN = 10
const FONT_MAX = 22
const FONT_KEY = 'cockpit.terminal.fontSize'

function readFontSize(): number {
  try {
    const n = Number(localStorage.getItem(FONT_KEY))
    if (n >= FONT_MIN && n <= FONT_MAX) return n
  } catch {
    /* localStorage indisponible (SSR/test) → défaut */
  }
  return 13
}

function saveFontSize(n: number): void {
  try {
    localStorage.setItem(FONT_KEY, String(n))
  } catch {
    /* best-effort */
  }
}

// Thème xterm lu depuis les tokens CSS (@theme) — SOURCE UNIQUE, pas de hex dupliqué en TS (mapping dans
// ./theme). Palette 16 couleurs COMPLÈTE (8 normales + 8 brights). Le renderer DOM d'xterm v5 écrit le
// texte dans le DOM (→ visible pour la boucle visuelle), pas dans un canvas opaque.
function readTheme(el: HTMLElement): Record<string, string> {
  const cs = getComputedStyle(el)
  return buildTheme((name) => cs.getPropertyValue(name))
}

/** Frame de contrôle TEXTE émise par le daemon à la (ré)connexion : `{"t":"session","fresh":bool}`. `fresh`
 *  distingue une session NEUVE (le client rejoue son `initialCommand`) d'une RÉ-ATTACHE (scrollback rejoué,
 *  surtout pas de re-run). Retourne null si le texte n'est pas cette frame (→ sortie PTY texte brute). */
export function parseSessionFrame(data: string): { fresh: boolean } | null {
  try {
    const m = JSON.parse(data) as { t?: string; fresh?: unknown }
    if (m && m.t === 'session') return { fresh: Boolean(m.fresh) }
  } catch {
    /* pas du JSON → sortie PTY texte brute */
  }
  return null
}

/** Terminal PTY d'un projet, `session` détermine le flavor et donc la route WS (`ptyPath`) :
 *  `shell` → `/ws/terminal/{project}` (login `bash -l`, surface `claude login`) ; `interview` →
 *  `/ws/interview/{project}` (session DÉDIÉE dont le process EST `cockpit interview` — plus de commande
 *  tapée dans un shell partagé). xterm.js (addons fit + search + web-links). Frames BINAIRES = frappes ;
 *  frames TEXTE = contrôle `{"type":"resize",cols,rows}`. Le process tourne dans la racine du projet côté
 *  daemon ; à sa sortie le socket se ferme. La barre d'outils et l'état de fin BRANCHENT sur le flavor : en
 *  `shell`, hints de login + « Lancer Claude » + « Relancer » (recrée la session) ; en `interview`, pas de hints
 *  shell et, à la fermeture, un état de fin dédié (`InterviewEndState`) qui lit la roadmap et ouvre le chemin
 *  suivant (reprise ou lancement) plutôt qu'un « Relancer » trompeur. Commun aux deux : recherche dans le
 *  scrollback, taille de police (persistée), effacer. */
export function TerminalPane({ project, session = 'shell' }: { project: string; session?: PtySession }) {
  const hostRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const searchRef = useRef<SearchAddon | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const [status, setStatus] = useState<TermStatus>('connecting')
  const [fontSize, setFontSize] = useState<number>(readFontSize)
  const [reconnectKey, setReconnectKey] = useState(0)
  const [query, setQuery] = useState('')
  const token = useWsToken() // garde CSWSH : injecté au handshake ; le WS n'ouvre pas tant qu'il manque

  useEffect(() => {
    const host = hostRef.current
    if (!host || !token) return // pas de token → pas d'ouverture (handshake serait refusé 1008)
    setStatus('connecting')

    const term = new Terminal({
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      fontSize,
      cursorBlink: true,
      convertEol: true,
      scrollback: 5000,
      theme: readTheme(document.documentElement),
    })
    const fit = new FitAddon()
    const search = new SearchAddon()
    term.loadAddon(fit)
    term.loadAddon(search)
    term.loadAddon(new WebLinksAddon())
    term.open(host)
    termRef.current = term
    fitRef.current = fit
    searchRef.current = search
    const safeFit = () => {
      try {
        fit.fit()
      } catch {
        /* conteneur pas encore dimensionné → ignoré (le ResizeObserver rejouera) */
      }
    }
    safeFit()

    const ws = new WebSocket(wsUrl(ptyPath(project, session)), tokenProtocols(token))
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws
    const enc = new TextEncoder()
    const sendResize = () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
      }
    }

    ws.onopen = () => {
      setStatus('connected')
      // Cale la taille du PTY sur celle d'xterm avant que le serveur ne (re)joue la sortie. La bannière est
      // différée à la frame de session (le serveur dit d'abord si elle est neuve vs restaurée).
      safeFit()
      sendResize()
    }
    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(ev.data))
        return
      }
      if (typeof ev.data !== 'string') return
      // Frame de CONTRÔLE serveur (texte) : annonce de session (neuve vs ré-attachée). La sortie du PTY,
      // elle, est TOUJOURS binaire — un texte ne peut être qu'un contrôle.
      const ctl = parseSessionFrame(ev.data)
      if (!ctl) {
        term.write(ev.data)
        return
      }
      // Bannière cliente (repère stable pour la boucle visuelle + « où suis-je »), fraîche vs restaurée.
      // Le flavor de session pilote le libellé — l'interview est une session DÉDIÉE (son process EST
      // `cockpit interview`), le shell est le `bash -l` de login du projet. Aucun handoff tapé : le process
      // démarre de lui-même à la session neuve, et une ré-attache REPREND l'interview en cours.
      const flavor = session === 'interview' ? 'interview' : 'bash -l'
      term.writeln(
        ctl.fresh
          ? `\x1b[2m— session ${flavor} · ${project} —\x1b[0m`
          : `\x1b[2m— session ${flavor} restaurée · ${project} (scrollback rejoué) —\x1b[0m`,
      )
    }
    ws.onerror = () => setStatus('error')
    ws.onclose = () => setStatus((s) => (s === 'error' ? s : 'closed'))

    // Frappes → PTY en BINAIRE (une frame texte serait interprétée comme un contrôle JSON côté daemon).
    const onData = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(enc.encode(data))
    })
    // Redimensionnement xterm → contrôle TEXTE vers le PTY (TIOCSWINSZ).
    const onResize = term.onResize(sendResize)
    const ro = new ResizeObserver(() => safeFit())
    ro.observe(host)

    return () => {
      ro.disconnect()
      onData.dispose()
      onResize.dispose()
      ws.onclose = null // pas de setState après démontage
      ws.close()
      term.dispose()
      termRef.current = null
      fitRef.current = null
      searchRef.current = null
      wsRef.current = null
    }
    // fontSize hors deps : appliqué à chaud par l'effet dédié (pas de recréation du terminal à chaque cran).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, session, reconnectKey, token])

  // Taille de police appliquée À CHAUD (sans recréer le terminal) + persistée.
  useEffect(() => {
    const term = termRef.current
    if (term) {
      term.options.fontSize = fontSize
      try {
        fitRef.current?.fit()
      } catch {
        /* pas encore dimensionné */
      }
    }
    saveFontSize(fontSize)
  }, [fontSize])

  const connected = status === 'connected'
  // Garde de départ : quitter la vue Ops démonterait ce pane → `ws.close()` → le daemon tue le login shell
  // (SIGHUP) et toute commande en cours (ex. `cockpit interview`). Tant que la session est connectée, on
  // bloque la navigation SPA (confirmation) ET la fermeture d'onglet navigateur (`enableBeforeUnload` natif).
  const leaveBlocker = useBlocker({
    shouldBlockFn: () => connected,
    enableBeforeUnload: () => connected,
    withResolver: true,
  })
  const bumpFont = (delta: number) =>
    setFontSize((n) => Math.min(FONT_MAX, Math.max(FONT_MIN, n + delta)))
  const runSearch = (q: string) => {
    setQuery(q)
    if (q) searchRef.current?.findNext(q)
  }
  const sendToPty = (text: string) => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(new TextEncoder().encode(text))
      termRef.current?.focus()
    }
  }

  const st = STATUS[status]
  // Flavor : le terminal est générique (shell OU interview) — sa barre d'outils et son état de fin BRANCHENT
  // sur `session`. Le shell est le `bash -l` de login (surface `claude login`) ; l'interview est une session
  // DÉDIÉE dont le process EST `cockpit interview` — pas de hints shell, pas de « Lancer Claude », et à sa
  // fermeture (EOF propre) un état de fin qui ouvre le chemin suivant au lieu d'un « Relancer » trompeur.
  const isInterview = session === 'interview'
  // Fin d'interview : le PTY a quitté (fermeture propre) ou le transport a lâché. Dans cet état, l'organisateur
  // unique de la fin est le panneau `InterviewEndState` → le badge de statut brut devient redondant (masqué) et
  // le terminal résiduel (2 lignes de log de clôture) est BORNÉ (plus de canvas vide qui mange 40 % de hauteur).
  const interviewClosed = isInterview && (status === 'closed' || status === 'error')
  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {isInterview ? (
          <p className="mr-auto text-xs text-faint">
            Interview de conception (<span className="font-mono">cockpit interview</span>) — mène-la dans le
            terminal ; à la sortie, le socle se clôt.
          </p>
        ) : (
          <p className="mr-auto text-xs text-faint">
            Login shell (<span className="font-mono">bash -l</span>) — tape{' '}
            <span className="font-mono">claude</span> pour lier ton compte (1ʳᵉ fois : login).
          </p>
        )}
        <div className="w-full sm:w-52">
          <Input
            value={query}
            onChange={(e) => runSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') searchRef.current?.[e.shiftKey ? 'findPrevious' : 'findNext'](query)
            }}
            placeholder="rechercher…"
            aria-label="Rechercher dans le terminal"
            className="h-8"
          />
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={() => bumpFont(-1)}
            disabled={fontSize <= FONT_MIN} title="Réduire la police" aria-label="Réduire la police">
            A−
          </Button>
          <span className="w-6 text-center font-mono text-xs text-faint tabular-nums">{fontSize}</span>
          <Button variant="ghost" size="sm" onClick={() => bumpFont(1)}
            disabled={fontSize >= FONT_MAX} title="Agrandir la police" aria-label="Agrandir la police">
            A+
          </Button>
        </div>
        <Button variant="ghost" size="sm" onClick={() => termRef.current?.clear()}
          disabled={!connected} title="Effacer l'écran">
          Effacer
        </Button>
        {/* « Lancer Claude » et le « Relancer » générique sont des affordances SHELL : l'interview est déjà un
            process `cockpit interview` (y taper `claude` n'a aucun sens), et sa fermeture propre est gérée par
            l'état de fin dédié ci-dessous (reprise honnête ou hand-off vers le lancement), pas par un « Relancer »
            qui recréerait une interview sur un socle déjà clos. */}
        {!isInterview && (
          <Button variant="secondary" size="sm" onClick={() => sendToPty('claude\n')} disabled={!connected}
            title="Lancer Claude Code dans le shell">
            Lancer Claude
          </Button>
        )}
        {!isInterview && (status === 'closed' || status === 'error') && (
          <Button variant="primary" size="sm" onClick={() => setReconnectKey((k) => k + 1)}>
            Relancer
          </Button>
        )}
        {/* Statut brut masqué en fin d'interview : le panneau `InterviewEndState` porte déjà l'état (pas de
            « ● fermé » redondant au-dessus de « Interview terminée / incomplète »). */}
        {!interviewClosed && (
          <Badge tone={st.tone} dot>
            {st.label}
          </Badge>
        )}
      </div>
      {/* État de FIN de l'interview : à la fermeture du socket (le PTY `cockpit interview` a quitté), on ne laisse
          plus un terminal gelé — on lit la roadmap et on ouvre le chemin suivant (reprise ou lancement). */}
      {interviewClosed && (
        <InterviewEndState project={project} onReconnect={() => setReconnectKey((k) => k + 1)} />
      )}
      <div
        ref={hostRef}
        className={
          interviewClosed
            ? 'h-32 shrink-0 overflow-hidden rounded-card border border-border bg-bg p-3'
            : 'min-h-0 flex-1 overflow-hidden rounded-card border border-border bg-bg p-3'
        }
      />
      <LeaveTerminalConfirm blocker={leaveBlocker} />
    </div>
  )
}
