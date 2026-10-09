'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { Building2, FileText, Image as ImageIcon, LayoutGrid, Menu, Sparkles, X } from 'lucide-react';
import { useState } from 'react';
import clsx from 'clsx';
import { api } from '@/lib/api';
import { ThemeToggle } from '@/components/theme';

const GROUPS: [string, { href: string; label: string; icon: typeof FileText; badge?: string }[]][] = [
  ['Library', [
    { href: '/templates', label: 'Offer templates', icon: FileText, badge: 'templates' },
    { href: '/assets', label: 'Logos', icon: ImageIcon, badge: 'assets' },
  ]],
  ['Organisation', [
    { href: '/companies', label: 'Legal entities', icon: Building2, badge: 'companies' },
    { href: '/branches', label: 'Branch coverage', icon: LayoutGrid, badge: 'branches' },
  ]],
  ['Authoring', [
    { href: '/ai', label: 'AI studio', icon: Sparkles },
  ]],
];

export function Nav() {
  const path = usePathname();
  const [open, setOpen] = useState(false);

  const { data: health } = useQuery({
    queryKey: ['health'], queryFn: () => api.get('/api/health'), refetchInterval: 30_000,
  });
  const { data: templates } = useQuery({ queryKey: ['templates'], queryFn: () => api.get('/api/templates') });
  const { data: assets } = useQuery({ queryKey: ['assets'], queryFn: () => api.get('/api/assets') });
  const { data: companies } = useQuery({ queryKey: ['companies'], queryFn: () => api.get('/api/companies') });
  const { data: facets } = useQuery({ queryKey: ['facets'], queryFn: () => api.get('/api/branches/facets') });

  const counts: Record<string, number | undefined> = {
    templates: templates?.templates?.length,
    assets: assets?.assets?.length,
    companies: companies?.companies?.length,
    branches: facets?.total,
  };

  const services: [string, boolean | undefined][] = [
    ['ONLYOFFICE', health?.onlyoffice], ['Gotenberg', health?.gotenberg],
    ['Storage', health?.storage], ['PandaDoc', health?.pandadoc_key],
  ];

  return (
    <>
      <button onClick={() => setOpen(true)} aria-label="Open navigation"
        className="btn btn-ghost fixed left-3 top-3 z-40 hidden max-lg:inline-flex">
        <Menu className="h-4 w-4" />
      </button>

      {open && (
        <div className="fixed inset-0 z-40 hidden bg-black/40 backdrop-blur-sm max-lg:block"
             onClick={() => setOpen(false)} aria-hidden />
      )}

      <aside className={clsx(
        'sticky top-0 flex h-screen flex-col border-r bg-surface',
        'max-lg:fixed max-lg:z-50 max-lg:w-[248px] max-lg:transition-transform',
        open ? 'max-lg:translate-x-0' : 'max-lg:-translate-x-full')}>

        <div className="flex items-center gap-3 px-5 py-5">
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-accent text-2xs font-bold"
               style={{ color: 'rgb(var(--accent-ink))' }}>OL</div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold tracking-[-0.01em]">Offer Letter Studio</div>
            <div className="text-2xs text-ink3">HELIX · R&amp;D</div>
          </div>
          <button onClick={() => setOpen(false)} aria-label="Close navigation"
            className="btn btn-ghost btn-sm ml-auto hidden max-lg:inline-flex">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 pb-4">
          {GROUPS.map(([group, items]) => (
            <div key={group} className="mb-1">
              <div className="px-3 pb-1.5 pt-4 text-2xs font-semibold uppercase tracking-[0.07em] text-ink3">
                {group}
              </div>
              {items.map(({ href, label, icon: Icon, badge }) => {
                const active = path.startsWith(href);
                return (
                  <Link key={href} href={href} onClick={() => setOpen(false)}
                    aria-current={active ? 'page' : undefined}
                    className={clsx(
                      'relative mb-0.5 flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors',
                      active ? 'bg-accent/10 font-medium text-accent' : 'text-ink2 hover:bg-surface2 hover:text-ink')}>
                    {active && <span className="absolute left-0 h-4 w-0.5 rounded-r bg-accent" />}
                    <Icon className="h-[17px] w-[17px] shrink-0 opacity-90" />
                    <span className="truncate">{label}</span>
                    {badge && counts[badge] !== undefined && (
                      <span className="ml-auto rounded-full bg-surface2 px-1.5 text-2xs text-ink3">
                        {counts[badge]}
                      </span>
                    )}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="border-t px-4 py-3"><ThemeToggle /></div>

        <div className="border-t px-4 py-3">
          <div className="mb-2 text-2xs font-semibold uppercase tracking-[0.07em] text-ink3">Services</div>
          <div className="grid grid-cols-2 gap-x-2 gap-y-1.5">
            {services.map(([name, ok]) => (
              <div key={name} className="flex items-center gap-1.5 text-2xs text-ink3">
                <span className={clsx('h-1.5 w-1.5 shrink-0 rounded-full',
                  ok === undefined ? 'bg-ink3' : ok ? 'bg-good' : 'bg-bad')}
                  title={ok === undefined ? 'checking' : ok ? 'healthy' : 'unavailable'} />
                <span className="truncate">{name}</span>
              </div>
            ))}
          </div>
        </div>
      </aside>
    </>
  );
}
