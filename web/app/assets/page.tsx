'use client';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Pencil, Trash2, Building2, Check, X } from 'lucide-react';
import { api } from '@/lib/api';
import { CardSkeleton, Empty, Hint, PageHeader, Section, useToast } from '@/components/ui';

export default function AssetsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [assigning, setAssigning] = useState<string | null>(null);

  const { data, isPending } = useQuery({ queryKey: ['assets'], queryFn: () => api.get('/api/assets') });
  const { data: companies } = useQuery({ queryKey: ['companies'], queryFn: () => api.get('/api/companies') });
  const assets = data?.assets ?? [];

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['assets'] });
    qc.invalidateQueries({ queryKey: ['companies'] });
  };

  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append('file', file);
      form.append('name', file.name.replace(/\.\w+$/, ''));
      return api.upload('/api/assets', form);
    },
    onSuccess: () => { refresh(); toast({ tone: 'good', title: 'Added to the library' }); },
    onError: (e: Error) => toast({ tone: 'bad', title: 'Refused', body: e.message }),
  });

  const rename = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => api.patch(`/api/assets/${id}`, { name }),
    onSuccess: () => { refresh(); setEditing(null); toast({ tone: 'good', title: 'Renamed' }); },
    onError: (e: Error) => toast({ tone: 'bad', title: 'Rename failed', body: e.message }),
  });

  const assign = useMutation({
    mutationFn: ({ id, ids }: { id: string; ids: string[] }) =>
      api.put(`/api/assets/${id}/assign`, { company_ids: ids }),
    onSuccess: (r: any) => {
      refresh(); setAssigning(null);
      toast({ tone: 'good', title: `Assigned to ${r.total} entity(ies)`,
              body: `+${r.added.length} added, −${r.removed.length} removed` });
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/api/assets/${id}`),
    onSuccess: () => { refresh(); toast({ tone: 'good', title: 'Logo deleted' }); },
    onError: (e: Error) => toast({ tone: 'bad', title: 'Delete failed', body: e.message }),
  });

  return (
    <>
      <PageHeader title="Logos" sub="Brand assets, stored in MinIO and referenced by id"
        actions={
          <>
            <input ref={fileRef} type="file" accept="image/*" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ''; }} />
            <button className="btn btn-primary" onClick={() => fileRef.current?.click()}>Add logo</button>
          </>
        } />

      <Section className="space-y-4">
        <Hint>
          Assets are stored once and referenced, never copied into a template. Changing a
          brand&apos;s letterhead is one upload, not an edit to every letter that uses it.
        </Hint>

        {isPending ? (
          <CardSkeleton count={4} />
        ) : assets.length === 0 ? (
          <Empty title="No logos yet" body="Upload a brand mark to start the library." />
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-4">
            {assets.map((a: any) => {
              const used = a.assigned_to ?? [];
              return (
                <figure key={a.id} className="card group relative overflow-hidden">
                  <div className="grid h-28 place-items-center bg-surface2 p-4">
                    <img src={`/api/assets/${a.id}/file`} alt="" className="max-h-20 max-w-full object-contain" />
                  </div>

                  <figcaption className="border-t border-line p-4">
                    {editing === a.id ? (
                      <div className="mb-2 flex gap-1">
                        <input autoFocus className="input !py-1 text-sm" value={draft}
                          onChange={(e) => setDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') rename.mutate({ id: a.id, name: draft });
                            if (e.key === 'Escape') setEditing(null);
                          }} />
                        <button className="btn btn-sm" onClick={() => rename.mutate({ id: a.id, name: draft })}>
                          <Check className="h-3.5 w-3.5" />
                        </button>
                        <button className="btn btn-sm" onClick={() => setEditing(null)}>
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ) : (
                      <div className="truncate text-sm font-medium">{a.name}</div>
                    )}

                    <div className="mt-2 flex flex-wrap gap-1.5">
                      <span className="pill">{a.kind}</span>
                      <span className="pill">{(a.size_bytes / 1024).toFixed(1)} KB</span>
                      {used.length > 0 && <span className="pill pill-good">{used.length} entity(ies)</span>}
                    </div>

                    {used.length > 0 && (
                      <div className="mt-2 space-y-0.5 text-[11px] text-ink3">
                        {used.map((c: any) => <div key={c.id} className="truncate">{c.legal_name}</div>)}
                      </div>
                    )}

                    <div className="mt-3 flex gap-1 opacity-0 transition group-hover:opacity-100">
                      <button className="btn btn-sm flex-1"
                        onClick={() => { setEditing(a.id); setDraft(a.name); }}>
                        <Pencil className="h-3.5 w-3.5" />Rename
                      </button>
                      <button className="btn btn-sm flex-1"
                        onClick={() => setAssigning(assigning === a.id ? null : a.id)}>
                        <Building2 className="h-3.5 w-3.5" />Assign
                      </button>
                      <button className="btn btn-sm btn-danger"
                        onClick={() => {
                          // Name what breaks. "Delete this?" hides that three
                          // legal entities are about to lose their letterhead.
                          const warn = used.length
                            ? `\n\n${used.length} legal entity(ies) use it and will lose their letterhead:\n` +
                              used.map((c: any) => `  · ${c.legal_name}`).join('\n')
                            : '';
                          if (confirm(`Delete "${a.name}"?${warn}\n\nThe image is removed from MinIO. This cannot be undone.`)) {
                            remove.mutate(a.id);
                          }
                        }}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>

                    {assigning === a.id && (
                      <div className="mt-3 rounded-lg border border-line p-2">
                        <div className="mb-2 text-[11px] font-medium text-ink2">
                          Which legal entities use this letterhead?
                        </div>
                        <div className="max-h-40 space-y-0.5 overflow-y-auto">
                          {companies?.companies?.map((c: any) => (
                            <label key={c.id} className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-xs hover:bg-surface2">
                              <input type="checkbox" defaultChecked={c.logo_asset_id === a.id}
                                data-cid={c.id} />
                              <span className="truncate">{c.legal_name}</span>
                              <span className="ml-auto shrink-0 text-[10px] text-ink3">{c.branches}</span>
                            </label>
                          ))}
                        </div>
                        <button className="btn btn-primary btn-sm mt-2 w-full"
                          onClick={(e) => {
                            const box = (e.currentTarget.parentElement as HTMLElement);
                            const ids = [...box.querySelectorAll('input[data-cid]')]
                              .filter((i) => (i as HTMLInputElement).checked)
                              .map((i) => (i as HTMLElement).dataset.cid!);
                            assign.mutate({ id: a.id, ids });
                          }}>
                          Save assignment
                        </button>
                      </div>
                    )}
                  </figcaption>
                </figure>
              );
            })}
          </div>
        )}
      </Section>
    </>
  );
}
