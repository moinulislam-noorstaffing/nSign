'use client';
import clsx from 'clsx';
import { AlertTriangle, Plus, Trash2 } from 'lucide-react';
import {
  Compensation, Issue, MAX_TIERS, parseNumber,
} from '@/lib/offer';

/** The reviewed compensation — the values that will actually be printed.
 *
 * Whatever the AI suggested lands here as ordinary editable inputs. Nothing is
 * generated from the free text; it is generated from THIS, after a person has
 * looked at it.
 */
export function CompensationEditor({ value, onChange, issues }: {
  value: Compensation; onChange: (c: Compensation) => void; issues: Issue[];
}) {
  const set = (patch: Partial<Compensation>) => onChange({ ...value, ...patch });
  const bad = (field: string) => issues.some((i) => i.severity === 'error' && i.field === field);
  const cls = (field: string) => clsx('input', bad(field) && 'border-bad/50');

  const setTier = (index: number, patch: Partial<Compensation['tiers'][number]>) =>
    set({ tiers: value.tiers.map((t, i) => (i === index ? { ...t, ...patch } : t)) });

  const addTier = () => {
    const prevMax = parseNumber(value.tiers[value.tiers.length - 1]?.max ?? '');
    set({ tiers: [...value.tiers, { min: prevMax === null ? '' : (prevMax + 0.01).toFixed(2), max: '', rate: '' }] });
  };

  const base = parseNumber(value.baseAmount);
  const errors = issues.filter((i) => i.severity === 'error');
  const warnings = issues.filter((i) => i.severity === 'warning');

  return (
    <div className="space-y-4 rounded-lg border bg-surface2 p-4">
      <div className="text-xs font-medium text-ink2">
        Review what will be printed. These values, not the text above, go into the letter.
      </div>

      <div className="grid grid-cols-[160px_1fr] gap-3 max-sm:grid-cols-1">
        <label className="space-y-1">
          <span className="label">Base pay</span>
          <select className="input" value={value.baseType}
            onChange={(e) => set({ baseType: e.target.value as Compensation['baseType'] })}>
            <option value="hourly">Hourly</option>
            <option value="salary">Salary (per year)</option>
          </select>
        </label>
        <label className="space-y-1">
          <span className="label">Amount ($ {value.baseType === 'hourly' ? 'per hour' : 'per year'})</span>
          <input className={cls('base.amount')} inputMode="decimal" value={value.baseAmount}
            placeholder={value.baseType === 'hourly' ? '22' : '65000'}
            onChange={(e) => set({ baseAmount: e.target.value })} />
        </label>
      </div>

      <div className="space-y-2">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={value.hasCommission}
            onChange={(e) => set({ hasCommission: e.target.checked })} />
          Commission on Gross Profit
        </label>
        {value.hasCommission && (
          <div className="space-y-2 pl-6">
            <div className="grid grid-cols-[1fr_1fr_90px_36px] gap-2 text-[0.72rem] font-medium text-ink3 max-sm:hidden">
              <span>From ($)</span><span>To ($, blank = no cap)</span><span>Rate (%)</span><span />
            </div>
            {value.tiers.map((t, i) => (
              <div key={i} className="grid grid-cols-[1fr_1fr_90px_36px] items-center gap-2 max-sm:grid-cols-2">
                <input className={cls(`commission.tiers[${i + 1}].min`)} inputMode="decimal" value={t.min}
                  aria-label={`Tier ${i + 1} from`} placeholder="0"
                  onChange={(e) => setTier(i, { min: e.target.value })} />
                <input className={cls(`commission.tiers[${i + 1}].max`)} inputMode="decimal" value={t.max}
                  aria-label={`Tier ${i + 1} to`} placeholder="149,999.99"
                  onChange={(e) => setTier(i, { max: e.target.value })} />
                <input className={cls(`commission.tiers[${i + 1}].rate_pct`)} inputMode="decimal" value={t.rate}
                  aria-label={`Tier ${i + 1} rate`} placeholder="5"
                  onChange={(e) => setTier(i, { rate: e.target.value })} />
                <button type="button" className="btn btn-ghost btn-sm" aria-label={`Remove tier ${i + 1}`}
                  disabled={value.tiers.length === 1}
                  onClick={() => set({ tiers: value.tiers.filter((_, k) => k !== i) })}>
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
            <button type="button" className="btn btn-sm" onClick={addTier} disabled={value.tiers.length >= MAX_TIERS}>
              <Plus className="h-3.5 w-3.5" />Add tier
            </button>
          </div>
        )}
      </div>

      <div className="space-y-2">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={value.hasOvertime}
            onChange={(e) => set({ hasOvertime: e.target.checked })} />
          Overtime
        </label>
        {value.hasOvertime && (
          <div className="flex items-end gap-2 pl-6">
            <label className="space-y-1">
              <span className="label">Rate ($ per hour)</span>
              <input className={cls('overtime.amount')} inputMode="decimal" value={value.overtime}
                placeholder="33" onChange={(e) => set({ overtime: e.target.value })} />
            </label>
            {value.baseType === 'hourly' && base !== null && base > 0 && (
              <button type="button" className="btn btn-sm"
                onClick={() => set({ overtime: String(Number((base * 1.5).toFixed(2))) })}>
                1.5 × base
              </button>
            )}
          </div>
        )}
      </div>

      {(errors.length > 0 || warnings.length > 0) && (
        <ul className="space-y-1.5 text-xs" aria-live="polite">
          {errors.map((i, n) => (
            <li key={`e${n}`} className="flex gap-2 text-bad">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{i.message}
            </li>
          ))}
          {warnings.map((i, n) => (
            <li key={`w${n}`} className="flex gap-2 text-warn">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{i.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
