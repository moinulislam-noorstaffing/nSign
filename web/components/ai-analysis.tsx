'use client';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Sparkles, ShieldAlert, Check } from 'lucide-react';
import { api, ApiError } from '@/lib/api';
import { Hint, useToast } from '@/components/ui';

const CONF = { high: 'text-good', medium: 'text-warn', low: 'text-bad' } as const;

/**
 * AI analysis of one template.
 *
 * Everything here is a SUGGESTION. Applying it is a separate click, recorded
 * against a named person — because the thing being classified determines which
 * letter a candidate receives.
 */
export function AiAnalysis({ templateId, filename }: { templateId: string; filename?: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [result, setResult] = useState<any>(null);
  const [pii, setPii] = useState<any[] | null>(null);

  const { data: status } = useQuery({ queryKey: ['ai-status'], queryFn: () => api.get('/api/ai/status') });

  const analyse = useMutation({
    mutationFn: (allowPii: boolean) =>
      api.post(`/api/ai/analyse/${templateId}`, { allow_pii: allowPii }),
    onSuccess: (r) => { setResult(r); setPii(null); qc.invalidateQueries({ queryKey: ['ai-runs'] }); },
    onError: async (e: ApiError) => {
      // 409 means the pre-scan found personal data and refused BEFORE any
      // network call. Show what it found and let a person decide.
      if (e.status === 409) {
        const res = await fetch(`/api/ai/analyse/${templateId}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ allow_pii: false }),
        });
        const body = await res.json();
        setPii(body.pii ?? []);
      } else {
        toast({ tone: 'bad', title: 'Analysis failed', body: e.message });
      }
    },
  });

  const accept = useMutation({
    mutationFn: () => api.post(`/api/ai/runs/${result.run_id}/accept`, { accepted_by: 'HR' }),
    onSuccess: (r: any) => {
      qc.invalidateQueries({ queryKey: ['template', templateId] });
      qc.invalidateQueries({ queryKey: ['templates'] });
      toast({ tone: 'good', title: 'Applied',
              body: r.applied?.length ? r.applied.join(', ') : 'Recorded, nothing changed.' });
    },
  });

  if (!status?.configured) {
    return <Hint tone="warn">OPENAI_API_KEY is not set, so analysis is unavailable.</Hint>;
  }

  return (
    <div className="space-y-4">
      <Hint>
        <b>AI classifies and reviews. It never produces a document.</b> Nothing on the merge,
        render or send path calls a model — that is structural, not a convention. Output is
        constrained by schema to the agreed field and category lists, so it cannot invent one.
      </Hint>

      <div className="flex flex-wrap items-center gap-3">
        <button className="btn btn-primary" disabled={analyse.isPending}
          onClick={() => analyse.mutate(false)}>
          <Sparkles className="h-4 w-4" />
          {analyse.isPending ? 'Analysing…' : 'Analyse this template'}
        </button>
        <span className="text-xs text-ink3">
          {status.model} · {status.runs} run(s), {status.accepted} accepted
        </span>
      </div>

      {pii && (
        <div className="card border-bad/40 p-5">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-bad">
            <ShieldAlert className="h-4 w-4" />Personal data found — nothing was sent
          </div>
          <p className="mb-3 text-xs text-ink2">
            This template looks like a last-executed copy with a real person&apos;s details still in
            it. The call was refused before any network request. Redact the document, or send it
            deliberately.
          </p>
          <div className="mb-4 space-y-1">
            {pii.map((f, i) => (
              <div key={i} className="flex gap-3 text-xs">
                <span className="pill pill-bad">{f.kind}</span>
                <code className="text-ink2">{f.value}</code>
              </div>
            ))}
          </div>
          <button className="btn btn-sm" onClick={() => { setPii(null); analyse.mutate(true); }}>
            Send anyway
          </button>
        </div>
      )}

      {result && (
        <div className="space-y-4">
          <div className="card p-5">
            <div className="mb-3 flex items-center gap-3">
              <h3 className="card-title">Classification</h3>
              <span className="pill">{result.analysis.category}</span>
              {result.analysis.subdivision !== 'none' && (
                <span className="pill">{result.analysis.subdivision}</span>
              )}
              <span className={`text-xs ${CONF[result.analysis.category_confidence as keyof typeof CONF]}`}>
                {result.analysis.category_confidence} confidence
              </span>
              <button className="btn btn-primary btn-sm ml-auto" disabled={accept.isPending}
                onClick={() => accept.mutate()}>
                <Check className="h-3.5 w-3.5" />Accept &amp; apply
              </button>
            </div>
            <p className="text-sm text-ink2">{result.analysis.category_reason}</p>

            {filename && !filename.toLowerCase().includes(result.analysis.category) && (
              <div className="mt-3 rounded-lg border-l-[3px] border-l-warn bg-surface2 px-3 py-2 text-xs text-ink2">
                The filename <code>{filename}</code> does not agree with this classification.
                One of the two is wrong, and which letter a candidate receives depends on it.
              </div>
            )}
          </div>

          <div className="card overflow-hidden">
            <div className="border-b border-line px-5 py-3 text-sm font-semibold">Field mapping</div>
            <table className="table">
              <tbody>
                {result.analysis.field_mappings.map((m: any, i: number) => (
                  <tr key={i} >
                    <td className="px-5 py-2.5"><span className="token">{m.found_in_document}</span></td>
                    <td className="px-5 py-2.5">
                      {m.canonical_field
                        ? <span className="token">{m.canonical_field}</span>
                        : <span className="pill pill-bad">no honest match</span>}
                    </td>
                    <td className="px-5 py-2.5">
                      <span className={`text-xs ${CONF[m.confidence as keyof typeof CONF]}`}>{m.confidence}</span>
                    </td>
                    <td className="px-5 py-2.5 text-xs text-ink3">{m.issue}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="grid grid-cols-2 gap-4 max-lg:grid-cols-1">
            <div className="card p-5">
              <h3 className="mb-3 text-sm font-semibold">Clauses detected</h3>
              <div className="flex flex-wrap gap-2">
                {result.analysis.clauses_present.length
                  ? result.analysis.clauses_present.map((c: string) => (
                      <span key={c} className="pill">{c.replace(/_/g, ' ')}</span>))
                  : <span className="text-xs text-ink3">none identified</span>}
              </div>
              <p className="mt-3 text-xs text-ink3">
                Presence only. It does not judge whether a clause is adequate — that is a lawyer&apos;s
                job, not a model&apos;s.
              </p>
            </div>

            <div className="card p-5">
              <h3 className="mb-3 text-sm font-semibold">Hardcoded values</h3>
              <div className="flex flex-wrap gap-2">
                {(result.hardcoded ?? []).map((h: any, i: number) => (
                  <span key={i} className="pill pill-warn">{h.value}</span>))}
              </div>
              <p className="mt-3 text-xs text-ink3">
                A literal figure or date in a template is either a statutory threshold or a
                placeholder someone forgot. Worth a look either way.
              </p>
            </div>
          </div>

          {result.analysis.observations.length > 0 && (
            <div className="card overflow-hidden">
              <div className="border-b border-line px-5 py-3 text-sm font-semibold">Observations</div>
              {result.analysis.observations.map((o: any, i: number) => (
                <div key={i} className="border-b border-line/60 px-5 py-3 last:border-0">
                  <div className="flex items-center gap-2">
                    <span className={`pill ${o.severity === 'high' ? '!text-bad' : o.severity === 'medium' ? '!text-warn' : ''}`}>
                      {o.severity}
                    </span>
                    <span className="text-sm font-medium">{o.title}</span>
                  </div>
                  <p className="mt-1 text-xs text-ink2">{o.detail}</p>
                </div>
              ))}
            </div>
          )}

          <p className="text-xs text-ink3">
            {result.model} · {result.usage?.total_tokens} tokens · run <code>{result.run_id}</code>
          </p>
        </div>
      )}
    </div>
  );
}
