'use client';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Hint, PageHeader, Section, TableSkeleton } from '@/components/ui';

export default function CompaniesPage() {
  const { data, isPending } = useQuery({ queryKey: ['companies'], queryFn: () => api.get('/api/companies') });
  return (
    <>
      <PageHeader title="Legal entities" sub="What COMPANY NAME resolves to on the letter" />
      <Section className="space-y-4">
        <Hint tone="warn">
          Seeded from <code>employer_fein</code> on the branch export — the legal name is a best guess
          from the data and is <b>not confirmed</b>. A wrong legal name on a binding offer is the
          failure this exists to prevent, so a human confirms each one.
        </Hint>
        {isPending ? <TableSkeleton rows={5} cols={4} /> : (
        <div className="card overflow-hidden">
          <table className="table">
            <thead><tr>
                <th >FEIN</th>
                <th >Legal name</th>
                <th className="text-right">Branches</th>
                <th className="text-right">Confirmed</th>
              </tr>
            </thead>
            <tbody>
              {data?.companies?.map((c: any) => (
                <tr key={c.id} >
                  <td className="font-mono text-xs">{c.fein}</td>
                  <td className="font-medium">{c.legal_name}</td>
                  <td className="text-right"><span className="pill">{c.branches}</span></td>
                  <td className="text-right">
                    <span className={`pill ${c.confirmed ? '!text-good' : '!text-warn'}`}>
                      {c.confirmed ? 'confirmed' : 'unconfirmed'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
      </Section>
    </>
  );
}
