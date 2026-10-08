"use client";
import { Hint, PageHeader, Section, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles, Upload } from "lucide-react";
import { useMemo, useRef, useState } from "react";

function todayIso() {
  return new Date().toLocaleDateString("en-CA", {
    timeZone: "America/New_York",
  });
}

type FormState = {
  documentDate: string;
  onshoreOffshore: string;
  employeeName: string;
  address1: string;
  address2: string;
  city: string;
  state: string;
  zip: string;
  email: string;
  phone: string;
  companyId: string;
  jobTitle: string;
  workArrangement: string;
  officeLocation: string;
  startDate: string;
  reportTo: string;
  employmentType: string;
  compensation: string;
  payFrequency: string;
};

const INITIAL: FormState = {
  documentDate: todayIso(),
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

export default function NewOfferLetterPage() {
  const [f, setF] = useState<FormState>(INITIAL);
  const set = <K extends keyof FormState>(key: K, v: FormState[K]) =>
    setF((prev) => ({ ...prev, [key]: v }));
  const [templateId, setTemplateId] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const toast = useToast();

  const { data: companies } = useQuery({
    queryKey: ["companies"],
    queryFn: () => api.get("/api/companies"),
  });
  const { data: templatesRes } = useQuery({
    queryKey: ["templates"],
    queryFn: () => api.get("/api/templates"),
  });
  const templates: any[] = templatesRes?.templates ?? [];
  const selectedTemplate = templates.find((t) => t.id === templateId);

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
      toast({
        tone: "good",
        title: "Template uploaded",
        body: `${res.tokens.length} merge fields found.`,
      });
    },
    onError: (e: Error) =>
      toast({ tone: "bad", title: "Upload failed", body: e.message }),
  });

  const filled = useMemo(() => {
    const required = Object.entries(f).filter(
      ([k]) => k !== "address2" && k !== "officeLocation",
    );
    return required.filter(([, v]) => (v ?? "").toString().trim()).length;
  }, [f]);
  const total = Object.keys(f).length - 2; // address2 and officeLocation are conditional/optional

  return (
    <>
      <PageHeader
        title="Generate offer letter"
        sub={`${filled}/${total} fields filled · draft only, not yet wired to a template`}
      />
      <Section className="mx-auto max-w-[920px] space-y-5">
        <Hint tone="info" title="Frontend scaffold">
          This form captures the fields agreed with Ronald Sanchez. It is not
          yet connected to the merge engine, the compensation AI parser, or
          template selection — those need the chunking and conditional-merge
          work described separately before this can actually generate a letter.
        </Hint>

        {/* ── Offer letter template ────────────────────────────────────── */}
        <div className="card p-5">
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
          <label className="space-y-1">
            <span className="label">Template to use</span>
            <select
              className="input"
              value={templateId}
              onChange={(e) => setTemplateId(e.target.value)}
            >
              <option value="">Select a template…</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <p className="mt-3 text-xs text-ink2">
            {selectedTemplate ? (
              <>
                Using{" "}
                <span className="font-medium text-ink">{selectedTemplate.name}</span>
                {" · "}
                {selectedTemplate.source_filename}
              </>
            ) : (
              "No template selected. The letter will be generated from the template you choose."
            )}
          </p>
        </div>

        {/* ── Employee & role ──────────────────────────────────────────── */}
        <div className="card p-5">
          <h3 className="card-title mb-4">Employee &amp; role</h3>
          <div className="grid grid-cols-2 gap-4 max-md:grid-cols-1">
            <label className="space-y-1">
              <span className="label">Document date</span>
              <input
                type="date"
                className="input"
                value={f.documentDate}
                onChange={(e) => set("documentDate", e.target.value)}
              />
              <span className="hint-text">
                Defaults to today (system date, Eastern time).
              </span>
            </label>
            <label className="space-y-1">
              <span className="label">Company name</span>
              <select
                className="input"
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
            </label>
            <label className="space-y-1">
              <span className="label">Employee name</span>
              <input
                className="input"
                value={f.employeeName}
                placeholder="Jane Doe"
                onChange={(e) => set("employeeName", e.target.value)}
              />
            </label>
            <label className="space-y-1">
              <span className="label">Email</span>
              <input
                type="email"
                className="input"
                value={f.email}
                placeholder="jane@example.com"
                onChange={(e) => set("email", e.target.value)}
              />
            </label>
            <label className="space-y-1">
              <span className="label">Phone</span>
              <input
                type="tel"
                className="input"
                value={f.phone}
                placeholder="(401) 555-0123"
                onChange={(e) => set("phone", e.target.value)}
              />
            </label>
            <label className="space-y-1">
              <span className="label">Job title</span>
              <input
                className="input"
                value={f.jobTitle}
                placeholder="Recruiter"
                onChange={(e) => set("jobTitle", e.target.value)}
              />
            </label>
            <label className="space-y-1">
              <span className="label">Employment type</span>
              <select
                className="input"
                value={f.employmentType}
                onChange={(e) => set("employmentType", e.target.value)}
              >
                <option value="W2">W2</option>
                <option value="1099">1099 (Contractor)</option>
              </select>
            </label>
            <label className="space-y-1">
              <span className="label">Start date</span>
              <input
                type="date"
                className="input"
                value={f.startDate}
                onChange={(e) => set("startDate", e.target.value)}
              />
            </label>
            <label className="space-y-1">
              <span className="label">Report to / Manager</span>
              <input
                className="input"
                value={f.reportTo}
                placeholder="Manager name"
                onChange={(e) => set("reportTo", e.target.value)}
              />
            </label>
          </div>
        </div>

        {/* ── Address ──────────────────────────────────────────────────── */}
        <div className="card p-5">
          <h3 className="card-title mb-4">Address</h3>
          <div className="grid grid-cols-2 gap-4 max-md:grid-cols-1">
            <label className="space-y-1">
              <span className="label">Address 1</span>
              <input
                className="input"
                value={f.address1}
                placeholder="12 Yarmouth Street"
                onChange={(e) => set("address1", e.target.value)}
              />
            </label>
            <label className="space-y-1">
              <span className="label">
                Address 2 <span className="text-ink3">(optional)</span>
              </span>
              <input
                className="input"
                value={f.address2}
                placeholder="Apt / Suite"
                onChange={(e) => set("address2", e.target.value)}
              />
            </label>
            <label className="space-y-1">
              <span className="label">City</span>
              <input
                className="input"
                value={f.city}
                placeholder="Providence"
                onChange={(e) => set("city", e.target.value)}
              />
            </label>
            <div className="grid grid-cols-2 gap-4">
              <label className="space-y-1">
                <span className="label">State</span>
                <input
                  className="input"
                  value={f.state}
                  placeholder="RI"
                  onChange={(e) => set("state", e.target.value)}
                />
              </label>
              <label className="space-y-1">
                <span className="label">Zip code</span>
                <input
                  className="input"
                  value={f.zip}
                  placeholder="02907"
                  onChange={(e) => set("zip", e.target.value)}
                />
              </label>
            </div>
          </div>
        </div>

        {/* ── Work arrangement ─────────────────────────────────────────── */}
        <div className="card p-5">
          <h3 className="card-title mb-4">Work arrangement</h3>
          <div className="grid grid-cols-2 gap-4 max-md:grid-cols-1">
            <label className="space-y-1">
              <span className="label">Work arrangement</span>
              <select
                className="input"
                value={f.workArrangement}
                onChange={(e) => set("workArrangement", e.target.value)}
              >
                <option value="in_office">In office</option>
                <option value="hybrid">Hybrid</option>
                <option value="remote">Remote</option>
              </select>
            </label>
            <label className="space-y-1">
              <span className="label">
                Office location{" "}
                {f.workArrangement === "remote" && (
                  <span className="text-ink3">(n/a — remote)</span>
                )}
              </span>
              <input
                className="input"
                value={f.officeLocation}
                placeholder="Providence, Rhode Island"
                disabled={f.workArrangement === "remote"}
                onChange={(e) => set("officeLocation", e.target.value)}
              />
            </label>
            <label className="space-y-1">
              <span className="label">Onshore or offshore</span>
              <select
                className="input"
                value={f.onshoreOffshore}
                onChange={(e) => set("onshoreOffshore", e.target.value)}
              >
                <option value="onshore">Onshore</option>
                <option value="offshore">Offshore</option>
              </select>
              <span className="hint-text">
                Drives which Benefits block is used.
              </span>
            </label>
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
        <div className="card p-5">
          <h3 className="card-title mb-4">Compensation</h3>
          <label className="space-y-1">
            <span className="label">Compensation section</span>
            <textarea
              className="input min-h-[120px]"
              value={f.compensation}
              placeholder="e.g. $22/hour, commission on gross profit: 5% up to 150k, 6% 150k-300k, 7% above. Overtime at $33/hour."
              onChange={(e) => set("compensation", e.target.value)}
            />
            <span className="hint-text">
              Free text for now — the AI parser that maps this into the
              formatted wage/commission/overtime block is separate work, not
              wired here yet.
            </span>
          </label>
        </div>

        <div className="card p-5">
          <button
            type="button"
            disabled
            className="btn btn-primary w-full cursor-not-allowed opacity-50"
          >
            <Sparkles className="h-4 w-4" />
            Generate offer letter
          </button>
          <p className="hint-text mt-2 text-center">
            Disabled until template selection, conditional merge, and the
            compensation parser exist.
          </p>
        </div>
      </Section>
    </>
  );
}
