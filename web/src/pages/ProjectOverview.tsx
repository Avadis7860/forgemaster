import { useParams } from '@tanstack/react-router'
import { useProject } from '@/lib/queries'
import { ApiError } from '@/lib/api'
import { Alert, Badge, Card, Eyebrow, LoadingState, SectionTitle } from '@/components/ui'

/** Vue projet (V1 : identité). Les onglets Roadmap/Dispatch/Gate/Terminal arrivent en V2→V5. */
export function ProjectOverview() {
  const { project } = useParams({ from: '/$project' })
  const { data, isPending, isError, error } = useProject(project)

  if (isPending) return <LoadingState label="Chargement du projet…" />
  if (isError) {
    return (
      <div className="p-6">
        <Alert tone="danger" title="Projet introuvable">
          {error instanceof ApiError ? error.detail : String(error)}
        </Alert>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <SectionTitle
        eyebrow="Projet"
        title={data.slug}
        actions={<Badge tone="accent">{data.backend}</Badge>}
      />

      <Card raised className="divide-y divide-border">
        <Row label="Nom">{data.name ?? <span className="text-faint">—</span>}</Row>
        <Row label="Dépôt SoT">
          <code className="font-mono text-xs text-muted">{data.sot_path}</code>
        </Row>
        <Row label="Miroir">
          {data.mirror_remote ?? <span className="text-faint">— (internal-first)</span>}
        </Row>
        <Row label="Créé le">{data.created_at}</Row>
      </Card>

      <Card className="px-4 py-3">
        <Eyebrow>À venir</Eyebrow>
        <p className="mt-1 text-sm text-muted">
          Onglets Roadmap · Dispatch · Gate · Terminal — livrés dans les prochaines vagues.
        </p>
      </Card>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3">
      <span className="text-sm text-muted">{label}</span>
      <span className="text-right text-sm text-fg">{children}</span>
    </div>
  )
}
