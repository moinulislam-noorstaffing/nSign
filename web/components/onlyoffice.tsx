'use client';
import { useEffect, useRef, useState } from 'react';

declare global { interface Window { DocsAPI?: any } }

/**
 * ONLYOFFICE editor surface.
 *
 * The document server is a genuine .docx editor, so what you see is Word's own
 * model rather than an HTML approximation. Two things it is NOT:
 *
 *  * byte-preserving — it discards the OOXML package and regenerates it on
 *    save, so an edited letter is a NEW version, never a mutation of the
 *    approved one; and
 *  * the merge engine — merging happens server-side by splicing bytes, which
 *    keeps 25 of 26 parts identical.
 *
 * Hence the default is view mode, and edit is a deliberate act.
 */
export function OnlyOfficeEditor({ templateId, mode, onSaved }: {
  templateId: string; mode: 'view' | 'edit'; onSaved?: () => void;
}) {
  const holder = useRef<HTMLDivElement>(null);
  const editor = useRef<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const res = await fetch(`/api/editor/config/${templateId}?mode=${mode}`, { cache: 'no-store' });
        if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
        const { config, documentServerUrl } = await res.json();
        if (cancelled) return;

        if (!window.DocsAPI) {
          await new Promise<void>((resolve, reject) => {
            const s = document.createElement('script');
            s.src = `${documentServerUrl}/web-apps/apps/api/documents/api.js`;
            s.onload = () => resolve();
            s.onerror = () => reject(new Error(
              `Could not load the editor from ${documentServerUrl}. ` +
              `That address must resolve from YOUR browser, not from inside Docker.`));
            document.head.appendChild(s);
          });
        }
        if (cancelled || !holder.current) return;

        holder.current.innerHTML = '<div id="oo-editor" style="height:100%"></div>';
        editor.current = new window.DocsAPI.DocEditor('oo-editor', {
          ...config,
          events: {
            onError: (e: any) => setError(e?.data?.errorDescription ?? 'The editor reported an error'),
            // Fires after Document Server has posted the saved file to the
            // callback, so the new version already exists by the time we refetch.
            onDocumentStateChange: (e: any) => { if (!e?.data) onSaved?.(); },
          },
        });
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();

    return () => {
      cancelled = true;
      try { editor.current?.destroyEditor?.(); } catch { /* already gone */ }
    };
  }, [templateId, mode, onSaved]);

  if (error) {
    return (
      <div className="rounded-lg border-l-[3px] border-l-bad bg-surface2 px-4 py-3 text-sm text-ink2">
        <div className="font-medium text-ink">The editor could not start</div>
        <div className="mt-1 text-xs">{error}</div>
      </div>
    );
  }
  return <div ref={holder} className="h-[78vh] w-full overflow-hidden rounded-lg border border-line bg-white" />;
}
