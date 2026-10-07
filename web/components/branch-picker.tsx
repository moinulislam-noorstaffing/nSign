'use client';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { useToast } from '@/components/ui';

/**
 * Assigning one template across hundreds of branches.
 *
 * Rows lead with the branch CODE, never the name: `branches.name` holds the
 * EMPLOYER legal entity and repeats, so nine Harrisburg branches all read
 * "Global Empire LLC" and only the code tells them apart.
 */
export function BranchPicker({ templateId, initial }: { templateId: string; initial: number[] }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [selected, setSelected] = useState<Set<number>>(new Set(initial));
  const [fein, setFein] = useState('');
  const [state, setState] = useState('');
  const [q, setQ] = useState('');

  useEffect(() => { setSelected(new Set(initial)); }, [initial.join(',')]);

  const { data: facets } = useQuery({ queryKey: ['facets'], queryFn: () => api.get('/api/branches/facets') });
  const { data } = useQuery({
    queryKey: ['branches', q, fein, state],
    queryFn: () => api.get(`/api/branches?q=${encodeURIComponent(q)}&fein=${fein}&state=${state}`),
  });
  const branches = data?.branches ?? [];

  const save = useMutation({
    mutationFn: () => api.put(`/api/templates/${templateId}/branches`, { branch_ids: [...selected] }),
    onSuccess: (r: any) => {
      qc.invalidateQueries({ queryKey: ['template', templateId] });
      qc.invalidateQueries({ queryKey: ['coverage'] });
      // The DIFF, not the total: repointing a 94-branch entity by mis-click is
      // invisible if the only feedback is "94 assigned".
      toast({ tone: 'good', title: `Assigned to ${r.total} branch(es)`,
              body: `+${r.added.length} added, −${r.removed.length} removed` });
    },
  });

  const shown = useMemo(() => branches.map((b: any) => b.id), [branches]);

  return (
    <div className="card">
      <div className="flex items-center gap-3 border-b border-line px-5 py-3">
        <h3 className="card-title">Assigned branches</h3>
        <span className="pill">{selected.size}</span>
        <button className="btn btn-primary btn-sm ml-auto" disabled={save.isPending}
          onClick={() => save.mutate()}>Save assignment</button>
      </div>

      <div className="space-y-3 p-5">
        <div className="grid grid-cols-[2fr_1fr_2fr] gap-3 max-md:grid-cols-1">
          <select className="input" value={fein} onChange={(e) => setFein(e.target.value)}>
            <option value="">All legal entities</option>
            {facets?.employers?.map((e: any) => (
              <option key={e.fein} value={e.fein}>{e.sample} — {e.count} branches</option>
            ))}
          </select>
          <select className="input" value={state} onChange={(e) => setState(e.target.value)}>
            <option value="">All states</option>
            {facets?.states?.map((s: any) => <option key={s.state} value={s.state}>{s.state} ({s.count})</option>)}
          </select>
          <input className="input" placeholder="branch code or city"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>

        <div className="flex gap-2">
          <button className="btn btn-sm"
            onClick={() => setSelected((s) => new Set([...s, ...shown]))}>Select all shown</button>
          <button className="btn btn-sm"
            onClick={() => setSelected((s) => new Set([...s].filter((x) => !shown.includes(x))))}>Clear shown</button>
          <span className="ml-auto self-center text-xs text-ink3">{branches.length} shown</span>
        </div>

        <div className="max-h-[400px] overflow-y-auto rounded-lg border border-line">
          {branches.map((b: any) => (
            <label key={b.id} title={`${b.name} · FEIN ${b.fein}`}
              className="flex cursor-pointer items-center gap-3 border-b border-line/60 px-3 py-2
                         text-xs last:border-0 hover:bg-surface2">
              <input type="checkbox" checked={selected.has(b.id)}
                onChange={(e) => setSelected((s) => {
                  const next = new Set(s);
                  e.target.checked ? next.add(b.id) : next.delete(b.id);
                  return next;
                })} />
              <span className="flex-1 truncate font-mono">{b.code}</span>
              <span className="max-w-[34%] truncate text-ink3">{b.name}</span>
              <span className="shrink-0 text-ink3">{b.city}{b.state && `, ${b.state}`}</span>
            </label>
          ))}
          {branches.length === 0 && <div className="p-6 text-center text-xs text-ink3">No branches match.</div>}
        </div>
      </div>
    </div>
  );
}
