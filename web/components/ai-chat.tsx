'use client';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Check, CircleDashed, CircleDot, Sparkles } from 'lucide-react';
import clsx from 'clsx';
import { api, ApiError } from '@/lib/api';
import { Busy, Hint, useToast } from '@/components/ui';

type Msg = { role: 'user' | 'assistant'; content: string };

const STATUS = {
  drafted: { icon: Check, cls: 'text-good', label: 'drafted' },
  needs_input: { icon: CircleDot, cls: 'text-warn', label: 'needs input' },
  omitted: { icon: CircleDashed, cls: 'text-ink3', label: 'omitted' },
} as const;

const OPENERS = [
  'A part-time hourly caregiver in Pennsylvania, paid weekly.',
  'An outside sales rep on a recoverable draw against 8% commission.',
  'A 1099 contractor engaged per project at a flat service fee.',
  'Una carta de oferta para un terapeuta a tiempo completo en Florida.',
];

/**
 * Conversational authoring.
 *
 * A single prompt box forces someone to specify a legal document in one shot.
 * A conversation lets the model ask what it actually needs and lets the author
 * change one clause without rewriting the brief — and nothing is committed
 * until the section-by-section outline has been read.
 */
export function AiChat() {
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();

  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [draft, setDraft] = useState<any>(null);
  const [donor, setDonor] = useState('');
  const [logo, setLogo] = useState('');
  const [company, setCompany] = useState('');
  const endRef = useRef<HTMLDivElement>(null);

  const { data: donors } = useQuery({ queryKey: ['donors'], queryFn: () => api.get('/api/ai/donors') });
  const { data: assets } = useQuery({ queryKey: ['assets'], queryFn: () => api.get('/api/assets') });
  const { data: companies } = useQuery({ queryKey: ['companies'], queryFn: () => api.get('/api/companies') });

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, draft]);

  const send = useMutation({
    mutationFn: (next: Msg[]) =>
      api.post('/api/ai/chat', {
        messages: next, reference_template_id: donor, allow_pii: true,
      }),
    onSuccess: (r) => {
      setDraft(r.draft);
      setMessages((m) => [...m, { role: 'assistant', content: r.draft.reply }]);
    },
    onError: (e: ApiError) => toast({ tone: 'bad', title: 'The model could not reply', body: e.message }),
  });

  const commit = useMutation({
    mutationFn: () => api.post('/api/ai/chat/commit', {
      donor_template_id: donor, draft, logo_asset_id: logo,
      company_id: company, transcript: messages,
    }),
    onSuccess: (r: any) => {
      qc.invalidateQueries({ queryKey: ['templates'] });
      toast({ tone: 'good', title: 'Template created',
              body: `${r.composition.parts_inherited} parts inherited · ${r.tokens.length} merge fields` });
      router.push(`/templates/${r.template.id}`);
    },
    onError: (e: ApiError) => toast({ tone: 'bad', title: 'Could not create it', body: e.message }),
  });

  const submit = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || !donor || send.isPending) return;
    const next: Msg[] = [...messages, { role: 'user', content: trimmed }];
    setMessages(next);
    setInput('');
    send.mutate(next);
  };

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_380px] gap-5 max-xl:grid-cols-1">
      {/* conversation ------------------------------------------------------ */}
      <div className="card flex h-[70vh] min-h-[520px] flex-col">
        <div className="card-head">
          <Sparkles className="h-4 w-4 text-accent" />
          <h3 className="card-title">Describe the letter you need</h3>
          {draft?.language && <span className="pill ml-auto">{draft.language}</span>}
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {messages.length === 0 && (
            <div className="space-y-4">
              <Hint>
                Talk to it the way you would brief a colleague. It asks what it genuinely needs,
                keeps a full draft between turns, and shows you every section before anything is
                created. Write in any language — the letter follows you, while merge field names
                stay in English because they are the contract with the rest of the system.
              </Hint>
              <div className="space-y-1.5">
                <div className="text-2xs font-semibold uppercase tracking-[0.07em] text-ink3">
                  Try
                </div>
                {OPENERS.map((o) => (
                  <button key={o} onClick={() => submit(o)} disabled={!donor}
                    className="block w-full rounded-lg border border-line px-3 py-2 text-left text-xs
                               text-ink2 transition hover:border-accent hover:text-ink disabled:opacity-50">
                    {o}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <div key={i} className={clsx('flex', m.role === 'user' ? 'justify-end' : 'justify-start')}>
              <div className={clsx('max-w-[85%] rounded-xl px-3.5 py-2.5 text-sm leading-relaxed',
                m.role === 'user' ? 'bg-accent text-[rgb(var(--accent-ink))]' : 'bg-surface2 text-ink')}>
                {m.content}
              </div>
            </div>
          ))}

          {draft?.questions?.length > 0 && !send.isPending && (
            <div className="space-y-1.5">
              {draft.questions.map((q: string) => (
                <button key={q} onClick={() => setInput(q)}
                  className="block w-full rounded-lg border border-warn/40 bg-warn/5 px-3 py-2
                             text-left text-xs text-ink2 transition hover:border-warn">
                  {q}
                </button>
              ))}
            </div>
          )}

          {send.isPending && (
            <div className="flex gap-1.5 px-1" aria-label="Thinking">
              {[0, 1, 2].map((i) => (
                <span key={i} className="h-1.5 w-1.5 animate-pulse rounded-full bg-ink3"
                      style={{ animationDelay: `${i * 150}ms` }} />
              ))}
            </div>
          )}
          <div ref={endRef} />
        </div>

        <div className="border-t p-3">
          <div className="flex items-end gap-2">
            <textarea value={input} onChange={(e) => setInput(e.target.value)} rows={2}
              disabled={!donor}
              placeholder={donor ? 'Describe it, or ask for a change…' : 'Choose a stationery donor first'}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(input); }
              }}
              className="input resize-none" />
            <Busy pending={send.isPending} onClick={() => submit(input)}
              disabled={!input.trim() || !donor} className="btn btn-primary h-[42px] px-3"
              aria-label="Send">
              {!send.isPending && <ArrowUp className="h-4 w-4" />}
            </Busy>
          </div>
          <p className="mt-1.5 text-2xs text-ink3">Enter sends · Shift+Enter for a new line</p>
        </div>
      </div>

      {/* draft ------------------------------------------------------------- */}
      <div className="space-y-4">
        <div className="card p-4">
          <h3 className="card-title mb-3">Source material</h3>
          <label className="mb-3 block">
            <span className="label">Stationery donor <span className="text-bad">*</span></span>
            <select className="input" value={donor} onChange={(e) => setDonor(e.target.value)}>
              <option value="">Choose an approved letter…</option>
              {donors?.donors?.map((d: any) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
            <span className="hint-text">Header, footer, styles and page setup are inherited byte-for-byte,
              and its legal clauses are the ones copied verbatim.</span>
          </label>
          <div className="grid grid-cols-2 gap-2">
            <label>
              <span className="label">Logo</span>
              <select className="input" value={logo} onChange={(e) => setLogo(e.target.value)}>
                <option value="">Donor&apos;s</option>
                {assets?.assets?.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </label>
            <label>
              <span className="label">Entity</span>
              <select className="input" value={company} onChange={(e) => setCompany(e.target.value)}>
                <option value="">Unassigned</option>
                {companies?.companies?.map((c: any) => (
                  <option key={c.id} value={c.id}>{c.legal_name}</option>))}
              </select>
            </label>
          </div>
        </div>

        {draft && (
          <div className="card">
            <div className="card-head">
              <h3 className="card-title">Final preview</h3>
              <span className={clsx('pill ml-auto', draft.ready ? 'pill-good' : 'pill-warn')}>
                {draft.ready ? 'complete' : 'in progress'}
              </span>
            </div>

            <div className="space-y-3 p-4">
              <div>
                <div className="text-sm font-medium">{draft.title}</div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  <span className="pill">{draft.category}</span>
                  {draft.subdivision !== 'none' && <span className="pill">{draft.subdivision}</span>}
                  <span className="pill">{draft.blocks?.length ?? 0} blocks</span>
                </div>
              </div>

              <div className="space-y-1">
                {draft.outline?.map((o: any, i: number) => {
                  const s = STATUS[o.status as keyof typeof STATUS] ?? STATUS.omitted;
                  const Icon = s.icon;
                  return (
                    <div key={i} className="flex gap-2 rounded-md px-1 py-1.5 hover:bg-surface2">
                      <Icon className={clsx('mt-0.5 h-3.5 w-3.5 shrink-0', s.cls)} />
                      <div className="min-w-0">
                        <div className="text-xs font-medium">{o.section}</div>
                        <div className="text-2xs leading-relaxed text-ink3">{o.summary}</div>
                      </div>
                    </div>
                  );
                })}
              </div>

              {draft.placeholders_used?.length > 0 && (
                <div>
                  <div className="label">Merge fields</div>
                  <div className="flex flex-wrap gap-1">
                    {draft.placeholders_used.map((p: string) => (
                      <span key={p} className="token">{p}</span>))}
                  </div>
                </div>
              )}

              {draft.changes_made?.length > 0 && (
                <div>
                  <div className="label">Changed this turn</div>
                  <ul className="space-y-0.5 text-2xs text-ink2">
                    {draft.changes_made.map((c: string, i: number) => (
                      <li key={i} className="flex gap-1.5">
                        <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-accent" />{c}
                      </li>))}
                  </ul>
                </div>
              )}

              <Busy pending={commit.isPending} onClick={() => commit.mutate()}
                disabled={!draft.blocks?.length}
                className="btn btn-primary w-full">
                Create this template
              </Busy>
              {!draft.ready && (
                <p className="text-2xs text-warn">
                  Some sections still need input. You can create it anyway and edit in ONLYOFFICE,
                  or answer the open questions first.
                </p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
