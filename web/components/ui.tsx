'use client';
import clsx from 'clsx';
import { AlertTriangle, CheckCircle2, FileQuestion, Info, Loader2, X } from 'lucide-react';
import { createContext, useCallback, useContext, useState } from 'react';

/* ── page chrome ──────────────────────────────────────────────────────────── */

export function PageHeader({ title, sub, actions, breadcrumb }: {
  title: string; sub?: string; actions?: React.ReactNode; breadcrumb?: React.ReactNode;
}) {
  return (
    <header className="sticky top-0 z-30 border-b bg-bg/80 backdrop-blur-md">
      <div className="flex items-center gap-4 px-8 py-4 max-lg:px-4">
        <div className="min-w-0 flex-1">
          {breadcrumb && <div className="mb-1 text-2xs text-ink3">{breadcrumb}</div>}
          <h1 className="truncate text-[1.05rem] font-semibold tracking-[-0.015em]">{title}</h1>
          {sub && <p className="mt-0.5 truncate text-xs text-ink3">{sub}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
    </header>
  );
}

export function Section({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={clsx('mx-auto w-full max-w-[1400px] animate-in px-8 py-6 max-lg:px-4', className)}>
      {children}
    </div>
  );
}

/* ── states ───────────────────────────────────────────────────────────────── */

export function Empty({ title, body, action }: {
  title: string; body: string; action?: React.ReactNode;
}) {
  return (
    <div className="card flex flex-col items-center px-6 py-16 text-center">
      <div className="mb-4 grid h-12 w-12 place-items-center rounded-full bg-surface2">
        <FileQuestion className="h-5 w-5 text-ink3" />
      </div>
      <div className="text-sm font-medium">{title}</div>
      <p className="mx-auto mt-1.5 max-w-[42ch] text-xs leading-relaxed text-ink3">{body}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

/** Shaped like the thing it replaces, so the layout does not jump on arrival. */
export function TableSkeleton({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="card overflow-hidden" aria-busy="true" aria-label="Loading">
      <div className="flex gap-4 border-b bg-surface2 px-4 py-3">
        {Array.from({ length: cols }).map((_, i) => (
          <div key={i} className="skeleton h-3" style={{ width: i === 0 ? '30%' : '15%' }} />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex items-center gap-4 border-b px-4 py-3.5 last:border-b-0">
          {Array.from({ length: cols }).map((_, c) => (
            <div key={c} className="skeleton h-3.5" style={{ width: c === 0 ? '30%' : '15%' }} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function CardSkeleton({ count = 3 }: { count?: number }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-4" aria-busy="true">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="card overflow-hidden">
          <div className="skeleton h-28 rounded-none" />
          <div className="space-y-2 p-4">
            <div className="skeleton h-3.5 w-2/3" />
            <div className="skeleton h-3 w-1/3" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function StatGrid({ items }: { items: [string, React.ReactNode][] }) {
  return (
    <div className="mb-5 grid grid-cols-[repeat(auto-fit,minmax(130px,1fr))] gap-3">
      {items.map(([label, value]) => (
        <div key={label} className="stat">
          <span className="stat-value">{value ?? '—'}</span>
          <span className="stat-label">{label}</span>
        </div>
      ))}
    </div>
  );
}

export function Hint({ tone = 'info', title, children }: {
  tone?: 'info' | 'good' | 'warn' | 'bad'; title?: string; children: React.ReactNode;
}) {
  const border = { info: 'border-l-accent', good: 'border-l-good',
                   warn: 'border-l-warn', bad: 'border-l-bad' }[tone];
  const Icon = { info: Info, good: CheckCircle2, warn: AlertTriangle, bad: AlertTriangle }[tone];
  const fg = { info: 'text-accent', good: 'text-good', warn: 'text-warn', bad: 'text-bad' }[tone];
  return (
    <div className={clsx('flex gap-3 rounded-lg border-l-[3px] bg-surface2 px-4 py-3', border)}>
      <Icon className={clsx('mt-0.5 h-4 w-4 shrink-0', fg)} />
      <div className="min-w-0 text-xs leading-relaxed text-ink2">
        {title && <div className="mb-0.5 text-sm font-medium text-ink">{title}</div>}
        {children}
      </div>
    </div>
  );
}

/** Segmented control for a small, fixed set of mutually-exclusive options. */
export function ToggleGroup({ value, onChange, options, label }: {
  value: string; onChange: (v: string) => void;
  options: { value: string; label: string }[]; label?: string;
}) {
  return (
    <div className="flex rounded-lg border border-line p-0.5" role="radiogroup" aria-label={label}>
      {options.map((opt) => (
        <button key={opt.value} type="button" role="radio" aria-checked={value === opt.value}
          onClick={() => onChange(opt.value)}
          className={clsx(
            'flex-1 whitespace-nowrap rounded-md px-3 py-1.5 text-[0.8rem] font-medium transition-colors',
            value === opt.value ? 'bg-accent text-accent-ink' : 'text-ink2 hover:bg-surface2')}
          style={value === opt.value ? { color: 'rgb(var(--accent-ink))' } : undefined}>
          {opt.label}
        </button>
      ))}
    </div>
  );
}

/** A button that owns its own pending state, so every caller gets the same one. */
export function Busy({ pending, children, ...rest }: {
  pending?: boolean; children: React.ReactNode;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button {...rest} disabled={pending || rest.disabled}
      className={clsx(rest.className, pending && 'cursor-wait')}>
      {pending ? <Loader2 className="animate-spin" /> : null}
      {children}
    </button>
  );
}

/* ── toasts ───────────────────────────────────────────────────────────────── */

type Toast = { id: number; tone: 'good' | 'bad' | 'warn'; title: string; body?: string };
const Ctx = createContext<(t: Omit<Toast, 'id'>) => void>(() => {});
export const useToast = () => useContext(Ctx);

export function ToastHost({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((t: Omit<Toast, 'id'>) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x.slice(-3), { ...t, id }]);
    // Errors linger: they usually carry something you need to read.
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), t.tone === 'bad' ? 9000 : 4500);
  }, []);
  const Icon = { good: CheckCircle2, bad: AlertTriangle, warn: AlertTriangle };

  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-50 flex w-[380px] flex-col gap-2 max-sm:left-4 max-sm:w-auto"
           role="status" aria-live="polite">
        {items.map((t) => {
          const I = Icon[t.tone];
          const fg = t.tone === 'good' ? 'text-good' : t.tone === 'bad' ? 'text-bad' : 'text-warn';
          const bd = t.tone === 'good' ? 'border-l-good' : t.tone === 'bad' ? 'border-l-bad' : 'border-l-warn';
          return (
            <div key={t.id}
              className={clsx('card pointer-events-auto flex animate-slideIn gap-3 border-l-[3px] p-3.5', bd)}
              style={{ boxShadow: 'var(--shadow-lg)' }}>
              <I className={clsx('mt-0.5 h-4 w-4 shrink-0', fg)} />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium leading-snug">{t.title}</div>
                {t.body && <div className="mt-0.5 break-words text-xs leading-relaxed text-ink2">{t.body}</div>}
              </div>
              <button onClick={() => setItems((x) => x.filter((i) => i.id !== t.id))}
                aria-label="Dismiss" className="shrink-0 rounded p-0.5 text-ink3 hover:text-ink">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          );
        })}
      </div>
    </Ctx.Provider>
  );
}
