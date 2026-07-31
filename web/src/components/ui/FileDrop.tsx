import { useState, type DragEvent } from 'react'
import { cn } from '@/lib/cn'

/** Zone de dépôt de fichier (drag-drop + clic), alignée sur les tokens. **Contrôlée** : l'appelant tient le
 *  `file` courant et reçoit chaque sélection via `onFile`. Accessible : un vrai `<input type="file">` (le
 *  `<label>` le rend cliquable + focusable au clavier nativement) ; le drag-drop est un enrichissement. */
export function FileDrop({
  file,
  onFile,
  accept,
  disabled = false,
  hint,
}: {
  file: File | null
  onFile: (file: File | null) => void
  accept?: string
  disabled?: boolean
  hint?: string
}) {
  const [over, setOver] = useState(false)

  function onDrop(e: DragEvent<HTMLLabelElement>) {
    e.preventDefault()
    setOver(false)
    if (disabled) return
    const dropped = e.dataTransfer.files?.[0] ?? null
    if (dropped) onFile(dropped)
  }

  return (
    <label
      onDragOver={(e) => {
        e.preventDefault()
        if (!disabled) setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
      className={cn(
        'flex cursor-pointer flex-col items-center justify-center gap-1 rounded-card border border-dashed',
        'px-4 py-6 text-center text-sm transition-colors',
        'focus-within:outline-2 focus-within:outline-offset-1 focus-within:outline-accent-500',
        over ? 'border-accent-500 bg-surface' : 'border-border bg-bg hover:border-border-strong',
        disabled && 'cursor-not-allowed opacity-60',
      )}
    >
      <input
        type="file"
        accept={accept}
        disabled={disabled}
        className="sr-only"
        onChange={(e) => onFile(e.target.files?.[0] ?? null)}
      />
      {file ? (
        <span className="font-medium text-fg">{file.name}</span>
      ) : (
        <span className="text-muted">Glisse un fichier ici, ou clique pour choisir</span>
      )}
      {hint && <span className="text-xs text-faint">{hint}</span>}
    </label>
  )
}
