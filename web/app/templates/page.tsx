'use client';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useRef, useState } from 'react';
import { Upload, FileText, Trash2 } from 'lucide-react';
import { api } from '@/lib/api';
import { Empty, Hint, PageHeader, Section, TableSkeleton, useToast } from '@/components/ui';

export default function TemplatesPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  const { data, isPending } = useQuery({ queryKey: ['templates'], queryFn: () => api.get('/api/templates') });
  const { data: companies } = useQuery({ queryKey: ['companies'], queryFn: () => api.get('/api/companies') });
  const templates = data?.templates ?? [];

  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/api/templates/${id}`),
    onSuccess: (r: any) => {
      qc.invalidateQueries({ queryKey: ['templates'] });
      qc.invalidateQueries({ queryKey: ['coverage'] });
      toast({ tone: 'good', title: 'Template deleted',
              body: `${r.objects_deleted ?? 0} stored document(s) removed from MinIO too.` });
    },
    onError: (e: Error) => toast({ tone: 'bad', title: 'Delete failed', body: e.message }),
  });

  const importDoc = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append('file', file);
      form.append('name', file.name.replace(/\.docx$/i, ''));
      return api.upload('/api/templates/import', form);
    },
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['templates'] });
      if (res.hidden_text?.length) {
        toast({ tone: 'bad', title: 'Hidden text in this file',
                body: 'Invisible in Word. Read it before trusting the document.' });
      }
      toast({ tone: 'good', title: 'Imported',
              body: `${res.tokens.length} merge fields · ${res.kept.bold_runs} bold runs, ${res.kept.tables} tables, ${res.kept.images} images` });
    },
    onError: (e: Error) => toast({ tone: 'bad', title: 'Import failed', body: e.message }),
  });

  return (
    <>
      <PageHeader title="Offer templates" sub="The approved .docx is the artifact; everything else is derived"
        actions={
          <>
            <input ref={fileRef} type="file" accept=".docx" className="hidden"
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                setBusy(true);
                await importDoc.mutateAsync(f).catch(() => {});
                setBusy(false);
                e.target.value = '';
              }} />
            <button className="btn btn-primary" disabled={busy} onClick={() => fileRef.current?.click()}>
              <Upload className="h-4 w-4" />{busy ? 'Importing…' : 'Import .docx'}
            </button>
          </>
        } />

      <Section className="space-y-4">
        <Hint>
          Values are spliced into the original OOXML and every other byte is copied through, so the
          letter your lawyers approved is the letter that gets signed. Rendering goes through
          LibreOffice — never an HTML re-flow.
        </Hint>

        {isPending ? (
          <TableSkeleton rows={4} cols={5} />
        ) : templates.length === 0 ? (
          <Empty title="No templates yet"
            body="Import one of the eight approved .docx letters to begin." />
        ) : (
          <div className="card overflow-hidden">
            <table className="table">
              <thead><tr>
                  <th >Template</th>
                  <th >Legal entity</th>
                  <th >Category</th>
                  <th >Fields</th>
                  <th className="text-right">Branches</th>
                  <th className="w-12 px-4 py-3" />
                </tr>
              </thead>
              <tbody>
                {templates.map((t: any) => (
                  <tr key={t.id} >
                    <td >
                      <Link href={`/templates/${t.id}`} className="flex items-center gap-2 font-medium hover:text-accent">
                        <FileText className="h-4 w-4 text-ink3" />{t.name}
                        {t.generated && <span className="pill pill-accent ml-2">generated</span>}
                      </Link>
                      <div className="mt-0.5 pl-6 text-[11px] text-ink3">{t.source_filename}</div>
                    </td>
                    <td >{t.company?.legal_name ?? <span className="pill">unassigned</span>}</td>
                    <td >{t.category || <span className="text-ink3">—</span>}</td>
                    <td >
                      <span className="pill">v{t.versions.length}</span>
                    </td>
                    <td className="text-right">
                      <span className={t.branch_count ? 'pill' : 'pill pill-bad'}>{t.branch_count}</span>
                    </td>
                    <td className="text-right">
                      <button
                        aria-label={`Delete ${t.name}`}
                        className="rounded-md p-1.5 text-ink3 transition hover:bg-bad/10 hover:text-bad"
                        disabled={remove.isPending}
                        onClick={() => remove.mutate(t.id)}>
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs text-ink3">{companies?.companies?.length ?? 0} legal entities seeded from the branch export.</p>
      </Section>
    </>
  );
}
