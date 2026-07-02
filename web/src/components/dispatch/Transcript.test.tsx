import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Transcript } from './Transcript'
import type { JobFrame, TranscriptEvent } from '@/lib/schemas'

type Ev = Exclude<TranscriptEvent, JobFrame>

describe('Transcript', () => {
  it('rend un tour assistant : texte, chips d’outils (nom + résumé) et usage', () => {
    const events: Ev[] = [
      {
        type: 'assistant',
        text: 'J’analyse le schéma.',
        tools: [{ name: 'Read', input_summary: 'src/ingest/schema.py' }],
        usage: { output_tokens: 1840 },
      },
    ]
    render(<Transcript events={events} />)
    expect(screen.getByText('J’analyse le schéma.')).not.toBeNull()
    expect(screen.getByText('Read')).not.toBeNull()
    expect(screen.getByText('· src/ingest/schema.py')).not.toBeNull()
    expect(screen.getByText('1840 tokens')).not.toBeNull()
  })

  it('distingue un résultat d’outil ok (pastille ok) d’une erreur (pastille danger)', () => {
    const events: Ev[] = [
      {
        type: 'tool_result',
        results: [
          { ok: true, summary: '4 passed in 0.62s' },
          { ok: false, summary: 'FAILED test_schema.py' },
        ],
      },
    ]
    const { container } = render(<Transcript events={events} />)
    expect(screen.getByText('4 passed in 0.62s')).not.toBeNull()
    expect(screen.getByText('FAILED test_schema.py')).not.toBeNull()
    expect(container.querySelector('.bg-ok-500')).not.toBeNull()
    expect(container.querySelector('.bg-danger-500')).not.toBeNull()
  })
})
