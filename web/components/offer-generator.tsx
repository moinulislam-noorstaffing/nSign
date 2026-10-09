"use client";
import { CompensationEditor } from "@/components/compensation-editor";
import { Busy, Hint, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import {
  Compensation,
  DOCX_MIME,
  FormErrors,
  FormState,
  Issue,
  SERVER_FIELD,
  base64ToBlob,
  checkCompensation,
  checkFields,
  emptyCompensation,
  friendlyError,
  fromApi,
  issuesOf,
  requiredKeys,
  toApi,
} from "@/lib/offer";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useRouter } from "next/navigation";
import { ClipboardPaste, Download, FileText, Pencil, Sparkles, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

function todayIso() {
  return new Date().toLocaleDateString("en-CA", {
    timeZone: "America/New_York",
  });
}

function plusDays(iso: string, days: number) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Obviously fake data, for trying the form out. Never a real person. */
const SAMPLE_COMPENSATION =
  "$25/hour plus commission on gross profit: 3% from 0 to 99,999.99, 4% from 100,000 to 249,999.99. Overtime at $37.50/hour.";

const SAMPLE_COMP_VALUES = {
  base: { type: "hourly", amount: 25 },
  commission: {
    basis: "Gross Profit",
    tiers: [
      { min: 0, max: 99999.99, rate_pct: 3 },
      { min: 100000, max: 249999.99, rate_pct: 4 },
    ],
  },
  overtime: { amount: 37.5 },
  pay_frequency_mentioned: null,
};

const INITIAL: FormState = {
  documentDate: "",
  onshoreOffshore: "onshore",
  employeeName: "",
  address1: "",
  address2: "",
  city: "",
  state: "",
  zip: "",
  email: "",
  phone: "",
  companyId: "",
  jobTitle: "",
  workArrangement: "in_office",
  officeLocation: "",
  startDate: "",
  reportTo: "",
  employmentType: "W2",
  compensation: "",
  payFrequency: "weekly",
};

// Shown only when the letter could NOT be saved to Offer templates — the
// happy path navigates to the saved template's page instead of staying here.
type Result = {
  filename: string;
  docxUrl: string;
  pdfUrl: string | null;
  pdfError: string | null;
  report: any;
  warnings: Issue[];
  snapshot: string;
};

function Field({
  label,
  error,
  hint,
  children,
}: {
  label: React.ReactNode;
  error?: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <label className="space-y-1" data-invalid={error ? "true" : undefined}>
      <span className="label">{label}</span>
      {children}
      {error ? (
        <span className="hint-text !text-bad">{error}</span>
      ) : hint ? (
        <span className="hint-text">{hint}</span>
      ) : null}
    </label>
  );
}

export function OfferGenerator() {
  const [f, setF] = useState<FormState>(() => ({
    ...INITIAL,
    documentDate: todayIso(),
  }));
  const [serverErrors, setServerErrors] = useState<FormErrors>({});
  const set = <K extends keyof FormState>(key: K, v: FormState[K]) => {
    setF((prev) => ({ ...prev, [key]: v }));
    setServerErrors((prev) => (prev[key] ? { ...prev, [key]: undefined } : prev));
  };

  const [templateId, setTemplateId] = useState("");
  const [logoId, setLogoId] = useState("");
  const [comp, setComp] = useState<Compensation | null>(null);
  const [runId, setRunId] = useState("");
  const [parsedText, setParsedText] = useState<string | null>(null);
  const [parseNotes, setParseNotes] = useState<Issue[]>([]);
  const [serverCompIssues, setServerCompIssues] = useState<Issue[]>([]);
  const [attempted, setAttempted] = useState(false);
  const [banner, setBanner] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const router = useRouter();

  const { data: companies } = useQuery({
    queryKey: ["companies"],
    queryFn: () => api.get("/api/companies"),
  });
  const { data: templatesRes } = useQuery({
    queryKey: ["templates"],
    queryFn: () => api.get("/api/templates"),
  });
  const allTemplates: any[] = templatesRes?.templates ?? [];
  // Finished letters live in the same list, but they are not templates to fill.
  const templates = allTemplates.filter((t) => !t.generated);
  const selectedTemplate = allTemplates.find((t) => t.id === templateId);
  const selectedCompany = companies?.companies?.find(
    (c: any) => c.id === f.companyId,
  );

  const { data: assetsRes } = useQuery({
    queryKey: ["assets"],
    queryFn: () => api.get("/api/assets"),
  });
  const logos: any[] = (assetsRes?.assets ?? []).filter(
    (a: any) => a.kind === "logo",
  );
  const selectedLogo = logos.find((l) => l.id === logoId);
  const logoFileRef = useRef<HTMLInputElement>(null);

  const inspect = useQuery({
    queryKey: ["offer-inspect", templateId],
    queryFn: () => api.get(`/api/offers/templates/${templateId}/inspect`),
    enabled: !!templateId,
    retry: false,
  });
  const templateFreq: string | null = inspect.data?.pay_frequency ?? null;

  useEffect(() => {
    return () => {
      if (result?.docxUrl) URL.revokeObjectURL(result.docxUrl);
      if (result?.pdfUrl) URL.revokeObjectURL(result.pdfUrl);
    };
  }, [result]);

  const importTemplate = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      form.append("name", file.name.replace(/\.docx$/i, ""));
      return api.upload("/api/templates/import", form);
    },
    onSuccess: (res: any) => {
      qc.invalidateQueries({ queryKey: ["templates"] });
      setTemplateId(res.template.id);
      if (res.hidden_text?.length) {
        toast({
          tone: "bad",
          title: "Hidden text in this file",
          body: "Invisible in Word. Read it before trusting the document.",
        });
      }
      toast({ tone: "good", title: "Template uploaded" });
    },
    onError: (e: unknown) =>
      toast({ tone: "bad", title: "Upload failed", body: friendlyError(e) }),
  });

  const importLogo = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      form.append("name", file.name.replace(/\.[^.]+$/, ""));
      form.append("kind", "logo");
      return api.upload("/api/assets", form);
    },
    onSuccess: (res: any) => {
      qc.invalidateQueries({ queryKey: ["assets"] });
      setLogoId(res.asset.id);
      setServerErrors((p) => ({ ...p, logo: undefined }));
      toast({ tone: "good", title: "Logo uploaded" });
    },
    onError: (e: unknown) =>
      toast({ tone: "bad", title: "Logo upload failed", body: friendlyError(e) }),
  });

  const parse = useMutation({
    mutationFn: () =>
      api.post("/api/offers/compensation/parse", {
        text: f.compensation,
        job_title: f.jobTitle,
        employment_type: f.employmentType,
      }),
    onSuccess: (r: any) => {
      setComp(fromApi(r.compensation));
      setRunId(r.run_id ?? "");
      setParsedText(f.compensation.trim());
      setParseNotes(
        (r.issues ?? []).filter((i: Issue) => i.severity === "warning"),
      );
      setServerCompIssues([]);
      toast({
        tone: "good",
        title: "Compensation parsed",
        body: "Review the values below before generating.",
      });
    },
    onError: (e: unknown) =>
      toast({
        tone: "bad",
        title: "Could not parse the compensation",
        body: `${friendlyError(e)} You can enter it manually instead.`,
      }),
  });

  const generate = useMutation({
    mutationFn: ({ payload }: { payload: unknown; snapshot: string }) =>
      api.post("/api/offers/generate", payload),
    onSuccess: (r: any, vars) => {
      qc.invalidateQueries({ queryKey: ["templates"] });
      setBanner("");
      setServerErrors({});
      setServerCompIssues([]);
      if (r.saved?.id) {
        // Same place every generated letter lives: open it there to review,
        // download or edit it — exactly like a template committed from
        // Author by conversation.
        toast({
          tone: "good",
          title: "Offer letter generated",
          body: `${r.saved.reused ? "Already in" : "Saved to"} Offer templates as ${r.saved.name}`,
        });
        router.push(`/templates/${r.saved.id}`);
        return;
      }
      // Saving to Offer templates failed (rare — see the "saved" warning
      // below). The letter still exists only in this response, so it is
      // shown here rather than lost behind a navigation with nothing to show.
      try {
        const docx = base64ToBlob(r.docx_b64, DOCX_MIME);
        const pdf = r.pdf_b64 ? base64ToBlob(r.pdf_b64, "application/pdf") : null;
        setResult({
          filename: r.filename,
          docxUrl: URL.createObjectURL(docx),
          pdfUrl: pdf ? URL.createObjectURL(pdf) : null,
          pdfError: r.pdf_error ?? null,
          report: r.report,
          warnings: r.warnings ?? [],
          snapshot: vars.snapshot,
        });
        toast({ tone: "warn", title: "Generated, but not saved to Offer templates",
                body: "Download it now — see the warning below." });
      } catch {
        setBanner(
          "The server sent a file this browser could not read. Try generating again.",
        );
      }
    },
    onError: (e: unknown) => {
      const mapped: FormErrors = {};
      const compIssues: Issue[] = [];
      for (const i of issuesOf(e)) {
        if (i.severity !== "error") continue;
        const key = SERVER_FIELD[i.field];
        if (key) mapped[key] = i.message;
        else if (i.field === "template") mapped.template = i.message;
        else if (i.field === "logo_asset_id") mapped.logo = i.message;
        else if (/^(base|commission|overtime|pay_frequency|compensation)/.test(i.field))
          compIssues.push(i);
      }
      setServerErrors(mapped);
      setServerCompIssues(compIssues);
      setBanner(friendlyError(e));
      toast({ tone: "bad", title: "Not generated", body: friendlyError(e) });
    },
  });

  const clientErrors = useMemo(() => checkFields(f, templateId), [f, templateId]);
  const compIssues = useMemo(() => {
    const live = comp ? checkCompensation(comp, templateFreq) : [];
    const seen = new Set<string>();
    return [...live, ...serverCompIssues].filter((i) => {
      const k = `${i.severity}:${i.message}`;
      return seen.has(k) ? false : (seen.add(k), true);
    });
  }, [comp, templateFreq, serverCompIssues]);
  const compHasErrors = compIssues.some((i) => i.severity === "error");

  const err = (k: keyof FormErrors) =>
    serverErrors[k] ?? (attempted ? clientErrors[k] : undefined);
  const inp = (k: keyof FormErrors) =>
    clsx("input", err(k) && "border-bad/50");

  const required = requiredKeys(f);
  const filled =
    required.filter((k) => f[k].trim()).length +
    (templateId ? 1 : 0) +
    (comp && !compHasErrors ? 1 : 0);
  const total = required.length + 2;

  // Certain problems with the chosen logo, known before any request is made.
  const logoProblem =
    selectedLogo?.mime === "image/webp"
      ? "WebP logos are not supported inside a Word document. Use PNG, JPEG or GIF."
      : logoId && inspect.data && inspect.data.logo?.found === false
        ? "This template has no logo in its header to replace. Choose the template's own logo."
        : undefined;
  const snapshot = JSON.stringify([f, comp, templateId, logoId]);
  const stale = !!result && result.snapshot !== snapshot;
  const compTextChanged =
    !!comp && parsedText !== null && parsedText !== f.compensation.trim();
  const templateBlocked = !!inspect.data && inspect.data.usable === false;

  const handleGenerate = () => {
    setAttempted(true);
    setBanner("");
    const blockers =
      Object.keys(clientErrors).length +
      (!comp || compHasErrors ? 1 : 0) +
      (templateBlocked ? 1 : 0) +
      (logoProblem ? 1 : 0);
    if (blockers > 0 || !comp) {
      toast({
        tone: "warn",
        title: "Not ready yet",
        body: !comp
          ? "Parse or enter the compensation, then fix the highlighted fields."
          : "Fix the highlighted fields first.",
      });
      setTimeout(
        () =>
          document
            .querySelector('[data-invalid="true"]')
            ?.scrollIntoView({ behavior: "smooth", block: "center" }),
        60,
      );
      return;
    }
    generate.mutate({
      snapshot,
      payload: {
        template_id: templateId,
        company_id: f.companyId,
        include_pdf: true,
        compensation_run_id: runId,
        logo_asset_id: logoId,
        compensation: toApi(comp),
        fields: {
          document_date: f.documentDate,
          onshore_offshore: f.onshoreOffshore,
          employee_name: f.employeeName,
          address1: f.address1,
          address2: f.address2,
          city: f.city,
          state: f.state,
          zip: f.zip,
          email: f.email,
          phone: f.phone,
          job_title: f.jobTitle,
          work_arrangement: f.workArrangement,
          office_location: f.workArrangement === "remote" ? "" : f.officeLocation,
          start_date: f.startDate,
          report_to: f.reportTo,
          employment_type: f.employmentType,
        },
      },
    });
  };

  const prefill = () => {
    const hasData = (
      ["employeeName", "email", "address1", "compensation"] as const
    ).some((k) => f[k].trim());
    if (hasData && !window.confirm("Replace what you have typed with sample data?"))
      return;
    const today = todayIso();
    setF((prev) => ({
      ...prev,
      documentDate: today,
      onshoreOffshore: "onshore",
      employeeName: "Alex Morgan",
      address1: "42 Example Lane",
      address2: "Suite 5",
      city: "Riverton",
      state: "CA",
      zip: "90001",
      email: "alex.morgan@example.com",
      phone: "(401) 555-0123",
      companyId: companies?.companies?.[0]?.id ?? prev.companyId,
      jobTitle: "Account Coordinator",
      workArrangement: "hybrid",
      officeLocation: "Riverton, California",
      startDate: plusDays(today, 14),
      reportTo: "Jordan Lee",
      employmentType: "W2",
      compensation: SAMPLE_COMPENSATION,
    }));
    if (!templateId && templates.length > 0) setTemplateId(templates[0].id);
    setComp(fromApi(SAMPLE_COMP_VALUES));
    setRunId("");
    setParsedText(SAMPLE_COMPENSATION);
    setParseNotes([]);
    setServerCompIssues([]);
    setServerErrors({});
    setAttempted(false);
    setBanner("");
    toast({
      tone: "good",
      title: "Sample data filled in",
      body: "Fake details for trying the form. Replace them before a real letter.",
    });
  };

  const startManual = () => {
    if (comp && !window.confirm("Replace the values below with an empty form?"))
      return;
    setComp(emptyCompensation());
    setRunId("");
    setParsedText(null);
    setParseNotes([]);
    setServerCompIssues([]);
  };

  const startParse = () => {
    if (
      comp &&
      !window.confirm("Replace the values below with a fresh AI suggestion?")
    )
      return;
    parse.mutate();
  };

  const edits = result
    ? (Object.values(result.report?.replaced ?? {}) as number[]).reduce(
        (a, b) => a + b,
        0,
      )
    : 0;
  const resultMessages: Issue[] = result
    ? [
        ...((result.report?.notes ?? []) as string[]).map(
          (message): Issue => ({ severity: "warning", field: "", message }),
        ),
        ...result.warnings,
      ]
    : [];

  return (
    <div className="mx-auto max-w-[920px] space-y-5">
      <div className="flex items-center gap-3">
        <p className="text-xs text-ink3">
          {filled}/{total} required items ready
        </p>
        <button type="button" className="btn btn-sm ml-auto" onClick={prefill}>
          <ClipboardPaste className="h-3.5 w-3.5" />
          Prefill
        </button>
      </div>
        <Hint tone="info" title="How this works">
          The letter is your uploaded template with only your values spliced
          in. Everything else, including the formatting, is copied through
          byte for byte. AI is used in one place only: suggesting the
          compensation structure, which you review before anything is
          generated.
        </Hint>

        {/* ── Offer letter template ────────────────────────────────────── */}
        <div className="card p-5" data-invalid={err("template") ? "true" : undefined}>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <h3 className="card-title">Offer letter template</h3>
            <input
              ref={fileRef}
              type="file"
              accept=".docx"
              className="hidden"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (file) await importTemplate.mutateAsync(file).catch(() => {});
                e.target.value = "";
              }}
            />
            <button
              type="button"
              className="btn btn-sm ml-auto"
              disabled={importTemplate.isPending}
              onClick={() => fileRef.current?.click()}
            >
              <Upload className="h-3.5 w-3.5" />
              {importTemplate.isPending ? "Uploading…" : "Upload .docx"}
            </button>
          </div>
          <Field label="Template to use" error={err("template")}>
            <select
              className={inp("template")}
              value={templateId}
              onChange={(e) => {
                setTemplateId(e.target.value);
                setServerErrors((p) => ({ ...p, template: undefined }));
              }}
            >
              <option value="">Select a template…</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </Field>
          {templateId && inspect.isPending && (
            <p className="mt-3 text-xs text-ink3">Checking the template…</p>
          )}
          {inspect.isError && (
            <div className="mt-3">
              <Hint tone="bad" title="Could not check this template">
                {friendlyError(inspect.error)}
              </Hint>
            </div>
          )}
          {templateBlocked && (
            <div className="mt-3">
              <Hint tone="bad" title="This template cannot be filled">
                {(inspect.data.problems ?? []).join(" ")} The generator looks
                for these labels in the letter:{" "}
                {(inspect.data.expected_labels ?? []).join(", ")}.
              </Hint>
            </div>
          )}
          {inspect.data?.usable && (
            <div className="mt-3 space-y-2">
              <p className="text-xs text-ink2">
                <span className="font-medium text-ink">
                  {selectedTemplate?.name}
                </span>{" "}
                is ready
                {templateFreq ? ` · pays ${templateFreq}` : ""}
                {inspect.data.compensation?.commission_table_rows
                  ? ` · commission table with ${inspect.data.compensation.commission_table_rows - 1} row(s)`
                  : ""}
              </p>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(inspect.data.slots as Record<string, number>)
                  .filter(([, n]) => n > 0)
                  .map(([k, n]) => (
                    <span key={k} className="pill">
                      {k.replace(/_/g, " ")} ×{n}
                    </span>
                  ))}
              </div>
            </div>
          )}
        </div>

        {/* ── Letterhead logo ──────────────────────────────────────────── */}
        <div className="card p-5" data-invalid={err("logo") || logoProblem ? "true" : undefined}>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <h3 className="card-title">Letterhead logo</h3>
            <input
              ref={logoFileRef}
              type="file"
              accept="image/png,image/jpeg,image/gif"
              className="hidden"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (file) await importLogo.mutateAsync(file).catch(() => {});
                e.target.value = "";
              }}
            />
            <button
              type="button"
              className="btn btn-sm ml-auto"
              disabled={importLogo.isPending}
              onClick={() => logoFileRef.current?.click()}
            >
              <Upload className="h-3.5 w-3.5" />
              {importLogo.isPending ? "Uploading…" : "Upload logo"}
            </button>
          </div>
          <div className="flex flex-wrap items-start gap-4">
            <div className="min-w-[220px] flex-1">
              <Field
                label="Logo to use"
                error={err("logo") ?? logoProblem}
                hint="Replaces the logo in the template's header, fitted to the same space. PNG, JPEG or GIF."
              >
                <select
                  className={inp("logo")}
                  value={logoId}
                  onChange={(e) => {
                    setLogoId(e.target.value);
                    setServerErrors((p) => ({ ...p, logo: undefined }));
                  }}
                >
                  <option value="">Keep the template's own logo</option>
                  {logos.map((l) => (
                    <option key={l.id} value={l.id} disabled={l.mime === "image/webp"}>
                      {l.name}
                      {l.mime === "image/webp" ? " (WebP, not supported)" : ""}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            {selectedLogo && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={`/api/assets/${selectedLogo.id}/file`}
                alt={selectedLogo.name}
                className="h-16 max-w-[240px] rounded border bg-white object-contain p-1.5"
              />
            )}
          </div>
        </div>

        {/* ── Employee & role ──────────────────────────────────────────── */}
        <div className="card p-5">
          <h3 className="card-title mb-4">Employee &amp; role</h3>
          <div className="grid grid-cols-2 gap-4 max-md:grid-cols-1">
            <Field
              label="Document date"
              error={err("documentDate")}
              hint="Defaults to today (system date, Eastern time)."
            >
              <input
                type="date"
                className={inp("documentDate")}
                value={f.documentDate}
                onChange={(e) => set("documentDate", e.target.value)}
              />
            </Field>
            <Field
              label="Company name"
              error={err("companyId")}
              hint={
                selectedCompany && !selectedCompany.confirmed
                  ? "This legal name is not confirmed yet (Legal entities page)."
                  : undefined
              }
            >
              <select
                className={inp("companyId")}
                value={f.companyId}
                onChange={(e) => set("companyId", e.target.value)}
              >
                <option value="">Select legal entity…</option>
                {companies?.companies?.map((c: any) => (
                  <option key={c.id} value={c.id}>
                    {c.legal_name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Employee name" error={err("employeeName")}>
              <input
                className={inp("employeeName")}
                value={f.employeeName}
                placeholder="Jane Doe"
                onChange={(e) => set("employeeName", e.target.value)}
              />
            </Field>
            <Field label="Email" error={err("email")}>
              <input
                type="email"
                className={inp("email")}
                value={f.email}
                placeholder="jane@example.com"
                onChange={(e) => set("email", e.target.value)}
              />
            </Field>
            <Field label="Phone" error={err("phone")}>
              <input
                type="tel"
                className={inp("phone")}
                value={f.phone}
                placeholder="(401) 555-0123"
                onChange={(e) => set("phone", e.target.value)}
              />
            </Field>
            <Field label="Job title" error={err("jobTitle")}>
              <input
                className={inp("jobTitle")}
                value={f.jobTitle}
                placeholder="Recruiter"
                onChange={(e) => set("jobTitle", e.target.value)}
              />
            </Field>
            <Field
              label="Employment type"
              error={err("employmentType")}
              hint={
                f.employmentType === "1099"
                  ? "This template reads as an employee offer (at-will, full-time). Confirm a 1099 letter should use it."
                  : undefined
              }
            >
              <select
                className={inp("employmentType")}
                value={f.employmentType}
                onChange={(e) => set("employmentType", e.target.value)}
              >
                <option value="W2">W2</option>
                <option value="1099">1099 (Contractor)</option>
              </select>
            </Field>
            <Field label="Start date" error={err("startDate")}>
              <input
                type="date"
                className={inp("startDate")}
                value={f.startDate}
                onChange={(e) => set("startDate", e.target.value)}
              />
            </Field>
            <Field label="Report to / Manager" error={err("reportTo")}>
              <input
                className={inp("reportTo")}
                value={f.reportTo}
                placeholder="Manager name"
                onChange={(e) => set("reportTo", e.target.value)}
              />
            </Field>
          </div>
        </div>

        {/* ── Address ──────────────────────────────────────────────────── */}
        <div className="card p-5">
          <h3 className="card-title mb-4">Address</h3>
          <div className="grid grid-cols-2 gap-4 max-md:grid-cols-1">
            <Field label="Address 1" error={err("address1")}>
              <input
                className={inp("address1")}
                value={f.address1}
                placeholder="12 Yarmouth Street"
                onChange={(e) => set("address1", e.target.value)}
              />
            </Field>
            <Field
              label={
                <>
                  Address 2 <span className="text-ink3">(optional)</span>
                </>
              }
              error={err("address2")}
            >
              <input
                className={inp("address2")}
                value={f.address2}
                placeholder="Apt / Suite"
                onChange={(e) => set("address2", e.target.value)}
              />
            </Field>
            <Field label="City" error={err("city")}>
              <input
                className={inp("city")}
                value={f.city}
                placeholder="Providence"
                onChange={(e) => set("city", e.target.value)}
              />
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="State" error={err("state")}>
                <input
                  className={inp("state")}
                  value={f.state}
                  placeholder="RI"
                  maxLength={2}
                  onChange={(e) => set("state", e.target.value.toUpperCase())}
                />
              </Field>
              <Field label="Zip code" error={err("zip")}>
                <input
                  className={inp("zip")}
                  value={f.zip}
                  placeholder="02907"
                  onChange={(e) => set("zip", e.target.value)}
                />
              </Field>
            </div>
          </div>
        </div>

        {/* ── Work arrangement ─────────────────────────────────────────── */}
        <div className="card p-5">
          <h3 className="card-title mb-4">Work arrangement</h3>
          <div className="grid grid-cols-2 gap-4 max-md:grid-cols-1">
            <Field
              label="Work arrangement"
              error={err("workArrangement")}
              hint={
                f.workArrangement === "remote"
                  ? "Remote: the “at our … office” phrase is removed from the letter. That wording is not in an approved template, so confirm it."
                  : undefined
              }
            >
              <select
                className={inp("workArrangement")}
                value={f.workArrangement}
                onChange={(e) => set("workArrangement", e.target.value)}
              >
                <option value="in_office">In office</option>
                <option value="hybrid">Hybrid</option>
                <option value="remote">Remote</option>
              </select>
            </Field>
            <Field
              label={
                <>
                  Office location{" "}
                  {f.workArrangement === "remote" && (
                    <span className="text-ink3">(n/a — remote)</span>
                  )}
                </>
              }
              error={f.workArrangement === "remote" ? undefined : err("officeLocation")}
            >
              <input
                className={inp("officeLocation")}
                value={f.officeLocation}
                placeholder="Providence, Rhode Island"
                disabled={f.workArrangement === "remote"}
                onChange={(e) => set("officeLocation", e.target.value)}
              />
            </Field>
            <Field
              label="Onshore or offshore"
              error={err("onshoreOffshore")}
              hint="Recorded with the request. The Benefits section is static in this template, so it does not change the letter."
            >
              <select
                className={inp("onshoreOffshore")}
                value={f.onshoreOffshore}
                onChange={(e) => set("onshoreOffshore", e.target.value)}
              >
                <option value="onshore">Onshore</option>
                <option value="offshore">Offshore</option>
              </select>
            </Field>
            {/* <label className="space-y-1">
              <span className="label">Weekly or biweekly</span>
              <ToggleGroup label="Pay frequency" value={f.payFrequency}
                onChange={(v) => set('payFrequency', v)}
                options={[
                  { value: 'weekly', label: 'Weekly' },
                  { value: 'biweekly', label: 'Biweekly' },
                ]} />
            </label> */}
          </div>
        </div>

        {/* ── Compensation ─────────────────────────────────────────────── */}
        <div
          className="card p-5"
          data-invalid={
            (attempted && !comp) || (comp && compHasErrors) ? "true" : undefined
          }
        >
          <h3 className="card-title mb-4">Compensation</h3>
          <Field
            label="Describe the compensation"
            hint="Plain words are fine. The AI suggests a structure; you review and correct it below before anything is generated."
          >
            <textarea
              className="input min-h-[110px]"
              value={f.compensation}
              maxLength={4000}
              placeholder="e.g. $22/hour, commission on gross profit: 5% up to 150k, 6% 150k-300k, 7% above. Overtime at $33/hour."
              onChange={(e) => set("compensation", e.target.value)}
            />
          </Field>
          <div className="mt-3 flex flex-wrap gap-2">
            <Busy
              type="button"
              className="btn btn-sm"
              pending={parse.isPending}
              disabled={!f.compensation.trim()}
              onClick={startParse}
            >
              <Sparkles className="h-3.5 w-3.5" />
              Parse with AI
            </Busy>
            <button type="button" className="btn btn-sm" onClick={startManual}>
              <Pencil className="h-3.5 w-3.5" />
              Enter manually
            </button>
          </div>
          {attempted && !comp && (
            <p className="hint-text !text-bad">
              Parse the description or enter the compensation manually.
            </p>
          )}
          {comp && (
            <div className="mt-4 space-y-3">
              {compTextChanged && (
                <p className="hint-text !text-warn">
                  The description above changed after it was parsed. The values
                  below are what will be printed.
                </p>
              )}
              <CompensationEditor
                value={comp}
                issues={compIssues}
                onChange={(c) => {
                  setComp(c);
                  setServerCompIssues([]);
                }}
              />
              {parseNotes.length > 0 && (
                <Hint tone="warn" title="Notes on the AI suggestion">
                  <ul className="list-disc space-y-1 pl-4">
                    {parseNotes.map((n, i) => (
                      <li key={i}>{n.message}</li>
                    ))}
                  </ul>
                </Hint>
              )}
            </div>
          )}
        </div>

        {/* ── Generate ─────────────────────────────────────────────────── */}
        <div className="card space-y-3 p-5">
          <Busy
            type="button"
            className="btn btn-primary w-full"
            pending={generate.isPending}
            onClick={handleGenerate}
          >
            <Sparkles className="h-4 w-4" />
            {generate.isPending ? "Generating…" : "Generate offer letter"}
          </Busy>
          {banner && (
            <Hint tone="bad" title="The letter was not generated">
              {banner}
            </Hint>
          )}
        </div>

        {/* ── Result (only reached if it could not be saved to Offer templates;
             otherwise generating navigates straight to its template page) ──── */}
        {result && (
          <div className="card overflow-hidden">
            <div className="card-head flex-wrap">
              <h3 className="card-title">Generated letter</h3>
              {stale && (
                <span className="pill pill-warn">
                  Out of date — the form changed since this was generated
                </span>
              )}
              <div className="ml-auto flex gap-2">
                <a
                  className="btn btn-sm"
                  href={result.docxUrl}
                  download={result.filename}
                >
                  <Download className="h-3.5 w-3.5" />.docx
                </a>
                {result.pdfUrl && (
                  <a
                    className="btn btn-sm"
                    href={result.pdfUrl}
                    download={result.filename.replace(/\.docx$/i, ".pdf")}
                  >
                    <FileText className="h-3.5 w-3.5" />.pdf
                  </a>
                )}
              </div>
            </div>
            <div className="space-y-3 p-5">
              <p className="text-xs text-ink2">
                {edits} edits made ·{" "}
                {result.report?.static_paragraphs_verified ?? 0} static
                paragraphs verified unchanged
                {result.report?.removed?.length
                  ? ` · removed: ${result.report.removed.join(", ")}`
                  : ""}
                {result.report?.logo ? " · logo replaced" : ""}
              </p>
              {resultMessages.map((w, i) => (
                <Hint key={i} tone="warn">
                  {w.message}
                </Hint>
              ))}
              {result.pdfError && (
                <Hint tone="bad" title="PDF preview unavailable">
                  {result.pdfError} The .docx download is still valid.
                </Hint>
              )}
            </div>
            {result.pdfUrl && (
              <iframe
                src={result.pdfUrl}
                title="Generated offer letter"
                className="h-[80vh] w-full border-t bg-white"
              />
            )}
          </div>
        )}
    </div>
  );
}
