'use client';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Hint, PageHeader, Section, TableSkeleton } from '@/components/ui';

export default function CoveragePage() {
  const { data, isPending } = useQuery({ queryKey: ['coverage'], queryFn: () => api.get('/api/coverage') });
  const pct = data?.total ? Math.round((100 * data.covered) / data.total) : 0;

  return (
    <>
      <PageHeader title="Branch coverage" sub="Which branches will produce a letter, and which will not" />
      <Section className="space-y-4">
        <Hint tone={pct === 100 ? 'good' : 'warn'}>
          A branch with no template is a hire with no letter — or worse, a generic one naming the
          wrong legal entity. {data?.total ? `${data.covered} of ${data.total} covered.` : ''}
        </Hint>

        <div className="grid grid-cols-4 gap-3 max-md:grid-cols-2">
          {[['Branches', data?.total], ['Legal entities', data?.by_employer?.length],
            ['Covered', data?.covered], ['Coverage', `${pct}%`]].map(([label, value]) => (
            <div key={String(label)} className="card p-4">
              <div className="text-2xl font-semibold">{value ?? '—'}</div>
              <div className="text-xs text-ink3">{label}</div>
            </div>
          ))}
        </div>

        {isPending ? <TableSkeleton rows={5} cols={4} /> : (
        <div className="card overflow-hidden">
          <table className="table">
            <thead><tr>
                <th >Legal entity (FEIN)</th>
                <th >Example</th>
                <th >Coverage</th>
                <th className="text-right">Branches</th>
              </tr>
            </thead>
            <tbody>
              {data?.by_employer?.map((e: any) => {
                const p = e.branches ? e.covered / e.branches : 0;
                return (
                  <tr key={e.fein} >
                    <td className="font-mono text-xs">{e.fein}</td>
                    <td className="text-ink2">{e.sample}</td>
                    <td >
                      <div className="flex items-center gap-3">
                        <div className="h-1.5 w-28 overflow-hidden rounded-full bg-surface2">
                          <div className={`h-full ${p === 1 ? 'bg-good' : p === 0 ? 'bg-bad' : 'bg-warn'}`}
                            style={{ width: `${Math.round(p * 100)}%` }} />
                        </div>
                        <span className="text-xs text-ink3">{e.covered}/{e.branches}</span>
                      </div>
                    </td>
                    <td className="text-right"><span className="pill">{e.branches}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        )}
      </Section>
    </>
  );
}
