'use client';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { useMemo, useState } from 'react';
import * as Tabs from '@radix-ui/react-tabs';
import { ArrowLeft, Download, Eye, FileSignature, Pencil, Search, Sparkles } from 'lucide-react';
import { api } from '@/lib/api';
import { Busy, Hint, PageHeader, Section, useToast } from '@/components/ui';
import { OnlyOfficeEditor } from '@/components/onlyoffice';
import { BranchPicker } from '@/components/branch-picker';
import { AiAnalysis } from '@/components/ai-analysis';

export default function TemplatePage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const toast = useToast();
  const [values, setValues] = useState<Record<string, string>>({});
  const [found, setFound] = useState<any>(null);
  const [pdf, setPdf] = useState<string | null>(null);
  const [mode, setMode] = useState<'view' | 'edit'>('view');
  const [language, setLanguage] = useState('');

  const { data: t } = useQuery({ queryKey: ['template', id], queryFn: () => api.get(`/api/templates/${id}`) });
  const tokens: string[] = t?.tokens ?? [];
  const missing = useMemo(() => tokens.filter((k) => !(values[k] ?? '').trim()), [tokens, values]);

  // Values come from the model, not a hardcoded map. The map returned the field
  // name back for anything it had not been taught — «ADDRESS» in the box, and
  // the strict renderer then refusing on a field discovery had just found.
  const sample = useMutation({
    mutationFn: () => api.post(`/api/ai/sample/${id}`, { language }),
    onSuccess: (r: any) => {
      setValues(r.values);
      toast({ tone: 'good', title: 'Sample values generated',
              body: `${Object.keys(r.values).length} fields in ${r.language ?? 'the document language'}.` });
    },
    onError: (e: Error) => toast({ tone: 'bad', title: 'Could not generate values', body: e.message }),
  });

  const discover = useMutation({
    mutationFn: () => api.post(`/api/ai/discover/${id}`, { allow_pii: true }),
    onSuccess: (r: any) => {
      setFound(r);
      toast({ tone: r.missed?.length ? 'warn' : 'good',
              title: `${r.missed?.length ?? 0} possible missed field(s)`,
              body: r.false_positives?.length ? `${r.false_positives.length} token(s) look like false positives.` : undefined });
    },
    onError: (e: Error) => toast({ tone: 'bad', title: 'Discovery failed', body: e.message }),
  });

  const render = useMutation({
    mutationFn: () => api.blob(`/api/templates/${id}/merge/pdf`, { values, strict: true }),
    onSuccess: (blob) => {
      setPdf(URL.createObjectURL(blob));
      toast({ tone: 'good', title: 'Rendered', body: 'Original OOXML, LibreOffice layout.' });
    },
    onError: (e: Error) => toast({ tone: 'bad', title: 'Refused', body: e.message }),
  });

  const download = useMutation({
    mutationFn: () => api.blob(`/api/templates/${id}/merge/docx`, { values, strict: true }),
    onSuccess: (blob) => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${t?.name ?? 'letter'} - merged.docx`;
      a.click();
      toast({ tone: 'good', title: 'Merged .docx downloaded' });
    },
    onError: (e: Error) => toast({ tone: 'bad', title: 'Refused', body: e.message }),
  });

  const send = useMutation({
    mutationFn: () => api.post(`/api/templates/${id}/send`,
      { values, strict: true, recipient_email: 'test@example.com' }),
    onSuccess: (r: any) => r?.response?.id
      ? toast({ tone: 'good', title: 'Sent to PandaDoc', body: r.response.id })
      : toast({ tone: 'bad', title: 'PandaDoc refused', body: JSON.stringify(r?.response).slice(0, 200) }),
    onError: (e: Error) => toast({ tone: 'bad', title: 'Refused', body: e.message }),
  });

  if (!t) {
    return (
      <>
        <PageHeader title="Loading template…" />
        <Section className="space-y-4">
          <div className="skeleton h-10 w-1/3" />
          <div className="skeleton h-64 w-full" />
        </Section>
      </>
    );
  }

  return (
    <>
      <PageHeader title={t.name}
        sub={`${t.company?.legal_name ?? 'no legal entity'} · ${tokens.length} merge fields · v${t.versions.length}`}
        actions={
          <>
            <Link href="/templates" className="btn btn-sm"><ArrowLeft className="h-3.5 w-3.5" />All templates</Link>
            <a href={`/api/templates/${id}/source`} className="btn btn-sm"><Download className="h-3.5 w-3.5" />Original</a>
          </>
        } />

      <Section>
        <Tabs.Root defaultValue="document">
          <Tabs.List className="mb-5 flex gap-1 border-b border-line">
            {[['document', 'Document'], ['fields', 'Fields & output'], ['ai', 'AI analysis'], ['branches', 'Branches'], ['history', 'History']]
              .map(([v, label]) => (
                <Tabs.Trigger key={v} value={v}
                  className="border-b-2 border-transparent px-4 py-2.5 text-sm text-ink2
                             data-[state=active]:border-accent data-[state=active]:font-medium
                             data-[state=active]:text-accent">
                  {label}
                </Tabs.Trigger>
              ))}
          </Tabs.List>

          <Tabs.Content value="document" className="space-y-4">
            <div className="flex items-center gap-3">
              <div className="flex rounded-lg border border-line p-0.5">
                <button onClick={() => setMode('view')}
                  className={`btn-sm rounded-md ${mode === 'view' ? 'bg-accent text-white' : 'text-ink2'}`}>
                  <Eye className="mr-1 inline h-3.5 w-3.5" />View
                </button>
                <button onClick={() => setMode('edit')}
                  className={`btn-sm rounded-md ${mode === 'edit' ? 'bg-accent text-white' : 'text-ink2'}`}>
                  <Pencil className="mr-1 inline h-3.5 w-3.5" />Edit
                </button>
              </div>
              <p className="text-xs text-ink3">
                {mode === 'view'
                  ? 'The approved document, exactly as authored.'
                  : 'Saving creates a NEW version — the editor regenerates the package, so an edit is a different artifact from the approved one.'}
              </p>
            </div>
            <OnlyOfficeEditor templateId={id} mode={mode}
              onSaved={() => qc.invalidateQueries({ queryKey: ['template', id] })} />
          </Tabs.Content>

          <Tabs.Content value="fields">
            <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-6 max-xl:grid-cols-1">
              <div className="space-y-4">
                <div className="card">
                  <div className="flex items-center gap-3 border-b border-line px-5 py-3">
                    <h3 className="card-title">Merge fields</h3>
                    <span className="pill">{tokens.length}</span>
                    <input className="input ml-auto max-w-[150px] !py-1 text-xs"
                      value={language} onChange={(e) => setLanguage(e.target.value)}
                      placeholder="Language (optional)" aria-label="Sample value language" />
                    <Busy pending={sample.isPending} onClick={() => sample.mutate()}
                      className="btn btn-sm">
                      <Sparkles className="h-3.5 w-3.5" />Fill sample
                    </Busy>
                  </div>
                  <div className="grid grid-cols-2 gap-4 p-5 max-md:grid-cols-1">
                    {tokens.map((k) => (
                      <label key={k} className="space-y-1">
                        <span className="token">{k}</span>
                        <input className={`input ${(values[k] ?? '').trim() ? '' : 'border-bad/40'}`}
                          value={values[k] ?? ''} placeholder={`value for ${k}`}
                          onChange={(e) => setValues((v) => ({ ...v, [k]: e.target.value }))} />
                      </label>
                    ))}
                  </div>
                </div>

                <div className="card p-5">
                  <div className="mb-3 flex items-center gap-3">
                    <h3 className="card-title">Did the scanner miss anything?</h3>
                    <Busy pending={discover.isPending} onClick={() => discover.mutate()}
                      className="btn btn-sm ml-auto"><Search className="h-3.5 w-3.5" />Check</Busy>
                  </div>
                  {found ? (
                    <div className="space-y-2">
                      {(found.missed ?? []).map((m: any, i: number) => (
                        <div key={i} className="rounded-lg border border-warn/35 bg-warn/5 px-3 py-2 text-xs">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="pill pill-warn">{m.confidence}</span>
                            <code className="text-ink2">{m.text_in_document}</code>
                            <span className="text-ink3">→</span>
                            <span className="token">{m.suggested_field}</span>
                          </div>
                          <p className="mt-1 text-ink3">{m.why}</p>
                        </div>
                      ))}
                      {(found.false_positives ?? []).length > 0 && (
                        <div className="rounded-lg bg-surface2 px-3 py-2 text-xs text-ink2">
                          <b>Probably not fields:</b>{' '}
                          {found.false_positives.map((f: any) => f.token).join(', ')}
                        </div>
                      )}
                      {!found.missed?.length && (
                        <p className="text-xs text-good">Nothing missed — every merge point is already a field.</p>
                      )}
                    </div>
                  ) : (
                    <p className="text-xs text-ink3">
                      The scanner sees {'{{braces}}'} and ALL-CAPS. It is blind to blanks written as
                      underscores, values hardcoded where a placeholder belongs, and merge points in
                      sentence case or another language.
                    </p>
                  )}
                </div>

                <div className="card p-5">
                  <div className="flex flex-wrap gap-2">
                    <button className="btn btn-primary" disabled={render.isPending}
                      onClick={() => render.mutate()}>Render exact PDF</button>
                    <button className="btn" disabled={download.isPending}
                      onClick={() => download.mutate()}>Download merged .docx</button>
                    <button className="btn" disabled={send.isPending} onClick={() => send.mutate()}>
                      <FileSignature className="h-4 w-4" />Send for signature
                    </button>
                  </div>
                  {missing.length > 0 && (
                    <p className="mt-3 text-xs text-bad">
                      {missing.length} field(s) empty — the merge refuses rather than printing a blank
                      line on a binding offer.
                    </p>
                  )}
                </div>
              </div>

              <div className="card overflow-hidden">
                <div className="border-b border-line px-5 py-3 text-sm font-semibold">Exact output</div>
                {pdf
                  ? <iframe src={pdf} className="h-[76vh] w-full bg-white" />
                  : <div className="px-5 py-20 text-center text-sm text-ink3">Render to see the finished letter.</div>}
              </div>
            </div>
          </Tabs.Content>

          <Tabs.Content value="ai">
            <AiAnalysis templateId={id} filename={t.source_filename} />
          </Tabs.Content>

          <Tabs.Content value="branches">
            <BranchPicker templateId={id} initial={t.branch_ids ?? []} />
          </Tabs.Content>

          <Tabs.Content value="history" className="space-y-4">
            <Hint tone="warn">
              Every save is a new immutable version with its own checksum. An issued letter must stay
              reproducible years later, so nothing overwrites a published body.
            </Hint>
            <div className="card overflow-hidden">
              <table className="table">
                <tbody>
                  {t.versions.map((v: any) => (
                    <tr key={v.version} >
                      <td ><span className="pill">v{v.version}</span></td>
                      <td className="text-ink2">{v.note}</td>
                      <td className="px-4 py-3 text-right font-mono text-[11px] text-ink3">{v.sha256.slice(0, 16)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Tabs.Content>
        </Tabs.Root>
      </Section>
    </>
  );
}
