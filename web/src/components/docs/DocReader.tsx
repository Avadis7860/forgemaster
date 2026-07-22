import { lazy, Suspense, type ReactNode } from 'react'
import { Dialog, LoadingState } from '@/components/ui'
import { firstHeading } from './mdx'

// Même chunk lazy que les autres surfaces (react-markdown hors bundle principal).
const DocView = lazy(() => import('./DocView').then((m) => ({ default: m.DocView })))

/** DocReaderOverlay — le **lecteur focus** partagé de TOUTE lecture de document (capital, bundles, accueil).
 *  Le corps inline reste l'aperçu de survol ; ce lecteur est le mode LECTURE : un `Dialog` centré **large**
 *  (≈1100px × 92vh, override de la taille modale par défaut) qui rend `DocView variant="reader"` dans une
 *  **colonne de mesure confortable** (`max-w-[72ch]` centrée) — grande typo, plein de hauteur, scroll interne.
 *  Fermeture ✕ / Échap / scrim fournie par la primitive `Dialog`. `meta` = la ref/source atténuée sous le titre.
 *  Un seul composant → cohérence sur toutes les surfaces, une seule chose à polir. */
export function DocReaderOverlay({ open, onOpenChange, title, meta, content }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: ReactNode
  meta?: ReactNode
  content: string
}) {
  // Titre de barre = le titre HUMAIN du doc (1er `# `), sinon le `title` fourni (basename). Évite d'afficher
  // le slug de fichier en tête alors que le chemin est déjà dans `meta` (anti-redondance).
  const barTitle = firstHeading(content) ?? title
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={barTitle}
      className="h-[92vh] max-h-[92vh] w-[min(1100px,92vw)]"
    >
      <div className="mx-auto max-w-[72ch]">
        {meta && <p className="mb-6 font-mono text-xs text-faint">{meta}</p>}
        <Suspense fallback={<LoadingState label="Rendu Markdown…" />}>
          <DocView content={content} variant="reader" />
        </Suspense>
      </div>
    </Dialog>
  )
}
