'use client';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { Sparkles, FileStack, Layers } from 'lucide-react';
import { api, ApiError } from '@/lib/api';
import { Hint, useToast } from '@/components/ui';

/**
 * Create a template from an existing one.
 *
 * The DONOR supplies stationery — letterhead, styles, numbering, page setup,
 * embedded fonts. The REFERENCE supplies wording, and its legal clauses are
 * copied verbatim rather than paraphrased. They are separable on purpose:
 * taking one entity's letterhead with another's clauses is the actual request
 * when a new legal entity is onboarded.
 */
export function AiCompose() {
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();
  const [result, setResult] = useState<any>(null);
  const [pii, setPii] = useState<any[] | null>(null);

  const { data: donors } = useQuery({ queryKey: ['donors'], queryFn: () => api.get('/api/ai/donors') });
  const { data: assets } = useQuery({ queryKey: ['assets'], queryFn: () => api.get('/api/assets') });
  const { data: companies } = useQuery({ queryKey: ['companies'], queryFn: () => api.get('/api/companies') });

  // No category, no subdivision, no state. What kind of letter this is comes out
  // of the description — a category chosen before the words exist is a guess.
  const [form, setForm] = useState({
    donor_template_id: '', reference_template_id: '', logo_asset_id: '',
    company_id: '', name: '', instructions: '',
  });
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const compose = useMutation({
    mutationFn: (allowPii: boolean) => api.post('/api/ai/compose', { ...form, allow_pii: allowPii }),
    onSuccess: (r) => {
      setResult(r); setPii(null);
      qc.invalidateQueries({ queryKey: ['templates'] });
      qc.invalidateQueries({ queryKey: ['ai-runs'] });
      toast({ tone: 'good', title: 'Template created',
              body: `${r.composition.parts_inherited} parts inherited from the donor.` });
    },
    onError: async (e: ApiError) => {
      if (e.status === 409) {
        const res = await fetch('/api/ai/compose', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...form, allow_pii: false }),
        });
        setPii((await res.json()).pii ?? []);
      } else {
        toast({ tone: 'bad', title: 'Composition failed', body: e.message });
      }
    },
  });

  const ready = form.donor_template_id && form.instructions.trim().length > 20;

  return (
    <div className="space-y-5">
      <Hint>
        <b>The donor is not a formality.</b> Building a .docx from nothing gives you no letterhead,
        no numbering definitions, no embedded fonts and a page geometry unrelated to the letters
        your lawyers approved. Keeping the donor&apos;s shell and replacing only the body means the
        output is the same stationery with different words on it.
      </Hint>

      <div className="grid grid-cols-2 gap-5 max-xl:grid-cols-1">
        <div className="card p-5">
          <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold">
            <Layers className="h-4 w-4 text-accent" />Source material
          </h3>

          <label className="mb-4 block">
            <span className="mb-1.5 block text-xs font-medium text-ink2">
              Stationery donor <span className="text-bad">*</span>
            </span>
            <select className="input" value={form.donor_template_id}
              onChange={(e) => set('donor_template_id', e.target.value)}>
              <option value="">Choose an approved letter…</option>
              {donors?.donors?.map((d: any) => (
                <option key={d.id} value={d.id}>
                  {d.name}{d.has_letterhead_logo ? ' · has letterhead' : ''}
                </option>
              ))}
            </select>
            <span className="mt-1 block text-[11px] text-ink3">
              Its header, footer, styles, numbering and page setup are inherited byte-for-byte.
            </span>
          </label>

          <label className="mb-4 block">
            <span className="mb-1.5 block text-xs font-medium text-ink2">Wording reference</span>
            <select className="input" value={form.reference_template_id}
              onChange={(e) => set('reference_template_id', e.target.value)}>
              <option value="">None — write fresh</option>
              {donors?.donors?.map((d: any) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
            <span className="mt-1 block text-[11px] text-ink3">
              Its legal clauses are copied <b>word for word</b>, never paraphrased.
            </span>
          </label>

          <label className="mb-4 block">
            <span className="mb-1.5 block text-xs font-medium text-ink2">Replace the letterhead logo</span>
            <select className="input" value={form.logo_asset_id}
              onChange={(e) => set('logo_asset_id', e.target.value)}>
              <option value="">Keep the donor&apos;s logo</option>
              {assets?.assets?.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </label>

          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-ink2">Legal entity</span>
            <select className="input" value={form.company_id}
              onChange={(e) => set('company_id', e.target.value)}>
              <option value="">Unassigned</option>
              {companies?.companies?.map((c: any) => (
                <option key={c.id} value={c.id}>{c.legal_name}</option>
              ))}
            </select>
          </label>
        </div>

        <div className="card p-5">
          <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold">
            <FileStack className="h-4 w-4 text-accent" />Describe the letter
          </h3>

          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-ink2">
              What should this letter say? <span className="text-bad">*</span>
            </span>
            <textarea className="input min-h-[240px] text-sm leading-relaxed" rows={12}
              value={form.instructions}
              placeholder={`A letter for an outside sales rep who earns a monthly recoverable draw against commission. Commission is 8% of collected gross margin, paid monthly in arrears. The draw is recoverable against future commissions and any unrecovered balance is forgiven on separation. They spend most of their week visiting client sites rather than in an office.

Keep the at-will and confidentiality language from the reference verbatim, and include an acceptance deadline and a dual signature block.`}
              onChange={(e) => set('instructions', e.target.value)} />
            <span className="mt-1.5 block text-[11px] text-ink3">
              Write it as you would brief a colleague. The compensation category, the clauses and
              the merge fields are all read out of what you write — nothing is chosen from a list.
            </span>
          </label>

          <label className="mt-4 block">
            <span className="mb-1.5 block text-xs font-medium text-ink2">
              Name <span className="font-normal text-ink3">— optional, one is inferred</span>
            </span>
            <input className="input" value={form.name} placeholder="Leave blank to let it name itself"
              onChange={(e) => set('name', e.target.value)} />
          </label>

          <button className="btn btn-primary mt-4 w-full" disabled={!ready || compose.isPending}
            onClick={() => compose.mutate(false)}>
            <Sparkles className="h-4 w-4" />
            {compose.isPending ? 'Composing, then proving it renders…' : 'Create template'}
          </button>
          {!ready && (
            <p className="mt-2 text-[11px] text-ink3">
              Pick a stationery donor and describe the letter in a few sentences.
            </p>
          )}
        </div>
      </div>

      {pii && (
        <div className="card border-bad/40 p-5">
          <div className="mb-2 text-sm font-semibold text-bad">
            Personal data in the reference — nothing was sent
          </div>
          <div className="mb-3 space-y-1">
            {pii.map((f, i) => (
              <div key={i} className="flex gap-3 text-xs">
                <span className="pill pill-bad">{f.kind}</span><code>{f.value}</code>
              </div>
            ))}
          </div>
          <button className="btn btn-sm" onClick={() => { setPii(null); compose.mutate(true); }}>
            Send anyway
          </button>
        </div>
      )}

      {result && (
        <div className="card p-5">
          <div className="mb-4 flex items-center gap-3">
            <h3 className="card-title">{result.template.name}</h3>
            <span className="pill pill-good">created</span>
            <button className="btn btn-primary btn-sm ml-auto"
              onClick={() => router.push(`/templates/${result.template.id}`)}>
              Open in the editor
            </button>
          </div>

          <div className="mb-4 rounded-lg border-l-[3px] border-l-accent bg-surface2 px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium text-ink2">Filed as</span>
              <span className="pill">{result.composition.category}</span>
              {result.composition.subdivision !== 'none' && (
                <span className="pill">{result.composition.subdivision}</span>
              )}
              <span className="text-[11px] text-ink3">read from your description</span>
            </div>
            <p className="mt-1.5 text-xs text-ink2">{result.composition.classification_reason}</p>
          </div>

          <div className="mb-4 grid grid-cols-4 gap-3 max-md:grid-cols-2">
            {[['Parts inherited', `${result.composition.parts_inherited} / 26`],
              ['Body blocks', result.composition.blocks],
              ['Logo replaced', result.composition.logo_replaced ? 'yes' : 'donor kept'],
              ['Page setup', result.composition.section_preserved ? 'preserved' : 'lost']]
              .map(([l, v]) => (
                <div key={String(l)} className="rounded-lg bg-surface2 p-3">
                  <div className="text-lg font-semibold">{v}</div>
                  <div className="text-[11px] text-ink3">{l}</div>
                </div>
              ))}
          </div>

          <div className="space-y-3 text-sm">
            <div>
              <span className="text-xs font-medium text-ink2">Placeholders</span>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {result.tokens.map((t: string) => <span key={t} className="token">{t}</span>)}
              </div>
            </div>
            {result.composition.clauses_copied_verbatim?.length > 0 && (
              <div>
                <span className="text-xs font-medium text-ink2">Clauses copied verbatim from the reference</span>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {result.composition.clauses_copied_verbatim.map((c: string) => (
                    <span key={c} className="pill">{c.replace(/_/g, ' ')}</span>))}
                </div>
              </div>
            )}
            {(result.composition.stray?.unknown_fields?.length > 0
              || result.composition.stray?.square_brackets?.length > 0) && (
              <div className="rounded-lg border-l-[3px] border-l-warn bg-surface2 px-3 py-2 text-xs text-ink2">
                <b>Fields outside the agreed vocabulary:</b>{' '}
                {[...(result.composition.stray.unknown_fields ?? []),
                  ...(result.composition.stray.square_brackets ?? [])].join(', ')}.
                They will still merge, but nothing else in the system knows them — add them to the
                canonical list or edit them out.
              </div>
            )}
            {result.composition.notes?.length > 0 && (
              <ul className="space-y-1 text-xs text-ink2">
                {result.composition.notes.map((n: string, i: number) => (
                  <li key={i} className="flex gap-2"><span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-warn" />{n}</li>
                ))}
              </ul>
            )}
          </div>

          <p className="mt-4 text-xs text-ink3">
            {result.model} · {result.usage?.total_tokens} tokens. The document was rendered through
            LibreOffice before it entered the library — a .docx that cannot render is not a template.
          </p>
        </div>
      )}
    </div>
  );
}
