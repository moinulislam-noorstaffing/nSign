'use client';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { ShieldCheck } from 'lucide-react';
import { api } from '@/lib/api';
import * as Tabs from '@radix-ui/react-tabs';
import { Empty, Hint, PageHeader, Section } from '@/components/ui';
import { AiChat } from '@/components/ai-chat';
import { AiCompose } from '@/components/ai-compose';

export default function AiStudioPage() {
  const { data: status } = useQuery({ queryKey: ['ai-status'], queryFn: () => api.get('/api/ai/status') });
  const { data: runs } = useQuery({ queryKey: ['ai-runs'], queryFn: () => api.get('/api/ai/runs?limit=50') });

  return (
    <>
      <PageHeader title="AI studio"
        sub={status?.configured ? `${status.model} · authoring assistance only` : 'not configured'} />

      <Section className="space-y-5">
        <Tabs.Root defaultValue="chat">
          <Tabs.List className="mb-5 flex gap-1 border-b border-line">
            {[['chat', 'Author by conversation'], ['create', 'One-shot brief'], ['governance', 'Governance & audit']].map(([v, l]) => (
              <Tabs.Trigger key={v} value={v}
                className="border-b-2 border-transparent px-4 py-2.5 text-sm text-ink2
                           data-[state=active]:border-accent data-[state=active]:font-medium
                           data-[state=active]:text-accent">{l}</Tabs.Trigger>
            ))}
          </Tabs.List>

          <Tabs.Content value="chat"><AiChat /></Tabs.Content>
          <Tabs.Content value="create"><AiCompose /></Tabs.Content>
          <Tabs.Content value="governance" className="space-y-5">
        <div className="card p-5">
          <div className="mb-3 flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-good" />
            <h3 className="card-title">What the model is and is not allowed to do</h3>
          </div>
          <ul className="space-y-2 text-sm text-ink2">
            {status?.guarantees?.map((g: string) => (
              <li key={g} className="flex gap-2">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-good" />{g}
              </li>
            ))}
          </ul>
        </div>

        <div className="grid grid-cols-4 gap-3 max-md:grid-cols-2">
          {[['Runs', status?.runs], ['Accepted', status?.accepted],
            ['Tokens used', status?.tokens_used], ['Canonical fields', status?.canonical_fields?.length]]
            .map(([label, value]) => (
              <div key={String(label)} className="card p-4">
                <div className="text-2xl font-semibold">{value ?? '—'}</div>
                <div className="text-xs text-ink3">{label}</div>
              </div>
            ))}
        </div>

        <Hint tone="warn">
          A suggestion that shaped a legal document has to be reconstructable years later. Every
          call is recorded here with its model, token cost, prompt digest and whether a person
          accepted it — the digest proves what was sent without keeping a copy of a document that
          may contain personal data.
        </Hint>

        {(runs?.runs?.length ?? 0) === 0 ? (
          <Empty title="No runs yet"
            body="Open a template and analyse it. Everything the model proposes lands here." />
        ) : (
          <div className="card overflow-hidden">
            <table className="table">
              <thead><tr>
                  <th >When</th>
                  <th >Operation</th>
                  <th >Outcome</th>
                  <th >Model</th>
                  <th className="text-right">Tokens</th>
                  <th className="text-right">Accepted</th>
                </tr>
              </thead>
              <tbody>
                {runs.runs.map((r: any) => (
                  <tr key={r.id} >
                    <td className="text-xs text-ink3">
                      {new Date(r.created_at).toLocaleString()}
                    </td>
                    <td >
                      {r.template_id
                        ? <Link href={`/templates/${r.template_id}`} className="hover:text-accent">{r.operation}</Link>
                        : r.operation}
                    </td>
                    <td className="text-xs text-ink2">
                      {r.result?.category
                        ? <>{r.result.category}{r.result.subdivision !== 'none' && ` / ${r.result.subdivision}`}</>
                        : r.result?.placeholders_used
                          ? `${r.result.placeholders_used.length} placeholders drafted`
                          : '—'}
                    </td>
                    <td className="text-xs text-ink3">{r.model}</td>
                    <td className="text-right text-xs">{r.tokens_used}</td>
                    <td className="text-right">
                      <span className={`pill ${r.accepted ? '!text-good' : '!text-warn'}`}>
                        {r.accepted ? `by ${r.accepted_by}` : 'suggestion'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
          </Tabs.Content>
        </Tabs.Root>
      </Section>
    </>
  );
}
