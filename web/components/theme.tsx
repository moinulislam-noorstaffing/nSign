'use client';
import { Monitor, Moon, Sun } from 'lucide-react';
import { useEffect, useState } from 'react';
import clsx from 'clsx';

type Mode = 'light' | 'dark' | 'system';
const KEY = 'studio-theme';

function apply(mode: Mode) {
  const dark = mode === 'dark'
    || (mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
}

/**
 * Theme switch.
 *
 * `data-theme` on <html> is authoritative in BOTH directions — the stylesheet
 * defines light and dark under explicit selectors rather than relying only on
 * the media query, so choosing light on a dark OS actually gives you light.
 * A media-query-only implementation silently ignores the toggle.
 */
export function ThemeToggle() {
  const [mode, setMode] = useState<Mode>('system');

  useEffect(() => {
    const saved = (localStorage.getItem(KEY) as Mode) || 'system';
    setMode(saved);
    apply(saved);
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => {
      if ((localStorage.getItem(KEY) as Mode) === 'system') apply('system');
    };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  const choose = (next: Mode) => {
    setMode(next);
    localStorage.setItem(KEY, next);
    apply(next);
  };

  const OPTIONS: [Mode, typeof Sun, string][] = [
    ['light', Sun, 'Light'],
    ['dark', Moon, 'Dark'],
    ['system', Monitor, 'Match system'],
  ];

  return (
    <div className="flex gap-0.5 rounded-lg border border-line p-0.5"
         role="group" aria-label="Colour theme">
      {OPTIONS.map(([value, Icon, label]) => (
        <button key={value} onClick={() => choose(value)} title={label} aria-label={label}
          aria-pressed={mode === value}
          className={clsx('grid h-7 flex-1 place-items-center rounded-md transition',
            mode === value ? 'bg-accent text-white' : 'text-ink3 hover:bg-surface2 hover:text-ink')}>
          <Icon className="h-3.5 w-3.5" />
        </button>
      ))}
    </div>
  );
}

/** Runs before first paint, so there is no flash of the wrong theme. */
export const themeScript = `(function(){try{
var m=localStorage.getItem('${KEY}')||'system';
var d=m==='dark'||(m==='system'&&matchMedia('(prefers-color-scheme: dark)').matches);
document.documentElement.dataset.theme=d?'dark':'light';
}catch(e){}})();`;
