"""Offer Letter Studio — API.

R&D application. No authentication: it is reached only through the compose
network and a loopback-bound proxy. HELIX supplies identity when this lands
there; adding a second auth system here would only be something to throw away.

The contract this API keeps:

    The approved .docx is the artifact. Everything else is derived.

Merging splices bytes into the original OOXML and copies every other byte.
Rendering goes through LibreOffice (inside Gotenberg), never an HTML re-flow.
PandaDoc receives a finished PDF, because it detects zero merge fields in a
.docx and cannot be the template engine.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import pathlib
import re
import unicodedata
import uuid
from urllib.parse import quote
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai, docx_compose, docx_html, offer_letter, ooxml_merge, services
from app.config import settings
from app.db import (
    AiRun, Asset, Branch, Company, Template, TemplateBranch, TemplateVersion,
    create_all, get_session,
)

logger = logging.getLogger("studio")

app = FastAPI(title=settings.app_name, version="2.0.0")

# The web app and the API share an origin behind Caddy in production; this
# keeps `next dev` on :3000 working without pretending CORS is a design choice.
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@app.on_event("startup")
async def startup() -> None:
    await create_all()
    services.ensure_bucket()
    await _seed_branches()


async def _seed_branches() -> None:
    """Load the real branch export once: 240 branches, 19 legal entities."""
    path = pathlib.Path("/app/seed/branches.tsv")
    if not path.is_file():
        return
    from app.db import SessionLocal
    async with SessionLocal() as session:
        existing = await session.scalar(select(func.count()).select_from(Branch))
        if existing:
            return
        companies: dict[str, Company] = {}
        for line in path.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            code, name, fein, city, state, active = (p.strip() for p in parts[:6])
            if fein and fein not in companies:
                companies[fein] = Company(fein=fein, legal_name=name, short_name=name[:120])
                session.add(companies[fein])
        await session.flush()
        for line in path.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            code, name, fein, city, state, active = (p.strip() for p in parts[:6])
            session.add(Branch(
                code=code or name, name=name, fein=fein, city=city, state=state,
                is_active=active == "t",
                company_id=companies[fein].id if fein in companies else None,
            ))
        await session.commit()


#: Magic bytes for the formats a letterhead may legitimately be. Sniffed rather
#: than declared, because the declaration comes from the uploader.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _sniff_image(data: bytes) -> str | None:
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def content_disposition(filename: str, inline: bool = False) -> str:
    """Build a Content-Disposition that survives a non-ASCII filename.

    HTTP header values are latin-1. A template called "1099 Contractor — Flat
    Service Fee" contains an em-dash, and putting it in the header raises
    UnicodeEncodeError inside the ASGI server — which surfaces as a bare 500
    with no useful message, long after the document was produced correctly.

    RFC 5987: an ASCII-safe `filename` for old clients, plus a percent-encoded
    UTF-8 `filename*` that every current browser prefers.
    """
    ascii_name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    ascii_name = re.sub(r'[^\w.\- ]', "", ascii_name).strip() or "document"
    quoted = quote(filename, safe="")
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"


def fail(message: str, status: int = 400, **extra: Any) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message, **extra}, status_code=status)


@app.exception_handler(RequestValidationError)
async def _invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's default 422 is a nested list the UI would print as '[object Object]'."""
    issues = []
    for e in exc.errors():
        if e.get("type") == "json_invalid":
            issues.append({"severity": "error", "field": "body",
                           "message": "The request body is not valid JSON."})
            continue
        issues.append({"severity": "error",
                       "field": ".".join(str(p) for p in e.get("loc", ())[1:]) or "request",
                       "message": str(e.get("msg", "invalid"))})
    summary = "; ".join(f"{i['field']}: {i['message']}" for i in issues[:4])
    return fail(f"Invalid request: {summary}", 422, issues=issues)


@app.exception_handler(Exception)
async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Last resort. The reference ties what the user saw to the stack in the log."""
    ref = uuid.uuid4().hex[:8]
    logger.exception("Unhandled error ref=%s %s %s", ref, request.method, request.url.path)
    return fail(f"Unexpected server error (ref {ref}). The details are in the API log.", 500)


# ─────────────────────────────── health ────────────────────────────────────

@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "storage": await services.storage_healthy(),
        "onlyoffice": await services.onlyoffice_healthy(),
        "gotenberg": await services.gotenberg_healthy(),
        "pandadoc_key": bool(settings.pandadoc_api_key),
        "openai_key": bool(settings.openai_api_key),
        "onlyoffice_public_url": settings.onlyoffice_public_url,
    }


# ─────────────────────────────── companies ─────────────────────────────────

@app.get("/api/companies")
async def companies(session: AsyncSession = Depends(get_session)) -> Any:
    rows = (await session.scalars(select(Company).order_by(Company.legal_name))).all()
    counts = dict((await session.execute(
        select(Branch.company_id, func.count()).group_by(Branch.company_id))).all())
    return {"companies": [{
        "id": c.id, "fein": c.fein, "legal_name": c.legal_name,
        "short_name": c.short_name, "logo_asset_id": c.logo_asset_id,
        "confirmed": c.confirmed_at is not None,
        "branches": counts.get(c.id, 0),
    } for c in rows]}


class CompanyPatch(BaseModel):
    legal_name: str | None = None
    short_name: str | None = None
    logo_asset_id: str | None = None
    confirmed: bool | None = None


@app.patch("/api/companies/{company_id}")
async def company_patch(company_id: str, body: CompanyPatch,
                        session: AsyncSession = Depends(get_session)) -> Any:
    company = await session.get(Company, company_id)
    if not company:
        raise HTTPException(404, "no such company")
    if body.legal_name is not None:
        company.legal_name = body.legal_name
    if body.short_name is not None:
        company.short_name = body.short_name
    if body.logo_asset_id is not None:
        company.logo_asset_id = body.logo_asset_id or None
    if body.confirmed is not None:
        company.confirmed_at = func.now() if body.confirmed else None
    await session.commit()
    return {"ok": True}


# ─────────────────────────────── branches ──────────────────────────────────

@app.get("/api/branches")
async def branches(q: str = "", fein: str = "", state: str = "",
                   session: AsyncSession = Depends(get_session)) -> Any:
    stmt = select(Branch)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Branch.code.ilike(like) | Branch.name.ilike(like) | Branch.city.ilike(like))
    if fein:
        stmt = stmt.where(Branch.fein == fein)
    if state:
        stmt = stmt.where(Branch.state == state)
    rows = (await session.scalars(stmt.order_by(Branch.fein, Branch.code))).all()
    return {"branches": [{
        "id": b.id, "code": b.code, "name": b.name, "fein": b.fein,
        "city": b.city, "state": b.state, "company_id": b.company_id,
    } for b in rows]}


@app.get("/api/branches/facets")
async def facets(session: AsyncSession = Depends(get_session)) -> Any:
    employers = (await session.execute(
        select(Branch.fein, func.count(), func.min(Branch.name))
        .where(Branch.fein != "").group_by(Branch.fein).order_by(func.count().desc()))).all()
    states = (await session.execute(
        select(Branch.state, func.count()).where(Branch.state != "")
        .group_by(Branch.state).order_by(func.count().desc()))).all()
    total = await session.scalar(select(func.count()).select_from(Branch))
    return {
        "employers": [{"fein": f, "count": n, "sample": s} for f, n, s in employers],
        "states": [{"state": s, "count": n} for s, n in states],
        "total": total or 0,
    }


@app.get("/api/coverage")
async def coverage(session: AsyncSession = Depends(get_session)) -> Any:
    total = await session.scalar(select(func.count()).select_from(Branch)) or 0
    covered = await session.scalar(
        select(func.count(func.distinct(TemplateBranch.branch_id)))) or 0
    rows = (await session.execute(
        select(Branch.fein, func.min(Branch.name), func.count(func.distinct(Branch.id)),
               func.count(func.distinct(TemplateBranch.branch_id)))
        .select_from(Branch)
        .outerjoin(TemplateBranch, TemplateBranch.branch_id == Branch.id)
        .where(Branch.fein != "").group_by(Branch.fein).order_by(func.count(func.distinct(Branch.id)).desc()))).all()
    return {
        "total": total, "covered": covered,
        "by_employer": [{"fein": f, "sample": s, "branches": b, "covered": c}
                        for f, s, b, c in rows],
    }


# ─────────────────────────────── assets ────────────────────────────────────

@app.get("/api/assets")
async def assets(session: AsyncSession = Depends(get_session)) -> Any:
    rows = (await session.scalars(select(Asset).order_by(Asset.created_at.desc()))).all()
    # Which legal entities use each logo. Deleting a logo that three entities
    # depend on should say so before it happens, not after their letters lose
    # their letterhead.
    used = (await session.execute(
        select(Company.logo_asset_id, Company.id, Company.legal_name)
        .where(Company.logo_asset_id.isnot(None)))).all()
    by_asset: dict[str, list[dict[str, str]]] = {}
    for asset_id, company_id, legal_name in used:
        by_asset.setdefault(asset_id, []).append({"id": company_id, "legal_name": legal_name})
    return {"assets": [{
        "id": a.id, "name": a.name, "kind": a.kind, "mime": a.mime,
        "size_bytes": a.size_bytes, "sha256": a.sha256,
        "assigned_to": by_asset.get(a.id, []),
    } for a in rows]}


class AssetPatch(BaseModel):
    name: str | None = None
    kind: str | None = None


@app.patch("/api/assets/{asset_id}")
async def asset_patch(asset_id: str, body: AssetPatch,
                      session: AsyncSession = Depends(get_session)) -> Any:
    asset = await session.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "no such asset")
    if body.name is not None:
        if not body.name.strip():
            return fail("a logo needs a name")
        asset.name = body.name.strip()
    if body.kind is not None:
        asset.kind = body.kind
    await session.commit()
    return {"ok": True, "asset": {"id": asset.id, "name": asset.name, "kind": asset.kind}}


class AssetAssignIn(BaseModel):
    #: Companies that should use this logo. Replaces the current set, so
    #: unticking an entity clears its letterhead rather than silently keeping it.
    company_ids: list[str] = []


@app.put("/api/assets/{asset_id}/assign")
async def asset_assign(asset_id: str, body: AssetAssignIn,
                       session: AsyncSession = Depends(get_session)) -> Any:
    asset = await session.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "no such asset")
    before = set((await session.scalars(
        select(Company.id).where(Company.logo_asset_id == asset_id))).all())
    after = set(body.company_ids)

    for company_id in before - after:
        company = await session.get(Company, company_id)
        if company:
            company.logo_asset_id = None
    for company_id in after:
        company = await session.get(Company, company_id)
        if company:
            company.logo_asset_id = asset_id
    await session.commit()
    return {"ok": True, "added": sorted(after - before),
            "removed": sorted(before - after), "total": len(after)}


@app.post("/api/assets")
async def asset_create(file: UploadFile = File(...), name: str = Form(""),
                       kind: str = Form("logo"),
                       session: AsyncSession = Depends(get_session)) -> Any:
    data = await file.read()
    # Sniff the bytes; never trust file.content_type. An HTML file announced as
    # image/png sailed through the old check, and SVG is not on the allowlist at
    # all because it is a script container served from the app's own origin.
    sniffed = _sniff_image(data)
    if sniffed is None:
        return fail(
            "Not a supported image. PNG, JPEG, GIF and WebP only — SVG is refused "
            "because it can carry script and would run on this origin."
        )
    if len(data) > settings.max_logo_bytes:
        return fail("logo is over 4 MB — it goes into every letter; shrink it")
    asset = Asset(name=name or file.filename or "logo", kind=kind,
                  mime=sniffed,
                  filename="", size_bytes=len(data), sha256="")
    session.add(asset)
    await session.flush()
    suffix = pathlib.Path(file.filename or "logo.png").suffix or ".png"
    key = services.asset_key(asset.id, suffix)
    digest = await services.put_object(key, data, asset.mime)
    asset.filename, asset.sha256 = key, digest
    await session.commit()
    return {"ok": True, "asset": {"id": asset.id, "name": asset.name}}


@app.get("/api/assets/{asset_id}/file")
async def asset_file(asset_id: str, session: AsyncSession = Depends(get_session)) -> Any:
    asset = await session.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "no such asset")
    return Response(
        await services.get_object(asset.filename), media_type=asset.mime,
        headers={
            # Inert by construction: the browser may not re-sniff it into HTML,
            # and nothing in it may execute or be framed.
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox; frame-ancestors 'none'",
            "Cache-Control": "private, max-age=300",
        },
    )


@app.delete("/api/assets/{asset_id}")
async def asset_delete(asset_id: str, session: AsyncSession = Depends(get_session)) -> Any:
    asset = await session.get(Asset, asset_id)
    key = asset.filename if asset else None
    # companies.logo_asset_id is ON DELETE SET NULL, so an entity using this
    # logo loses its letterhead rather than pointing at a missing object.
    await session.execute(delete(Asset).where(Asset.id == asset_id))
    await session.commit()
    if key:
        try:
            await services.delete_object(key)
        except Exception:
            pass
    return {"ok": True}


# ─────────────────────────────── templates ─────────────────────────────────

#: Stored in `notes` so a finished letter can be told apart from a reusable
#: template without a schema change (create_all cannot alter an existing table).
GENERATED_MARKER = "generated-offer-letter"


def _template_json(t: Template, tokens: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": t.id, "name": t.name, "category": t.category, "subdivision": t.subdivision,
        "generated": t.notes == GENERATED_MARKER,
        "company_id": t.company_id,
        "company": {"id": t.company.id, "legal_name": t.company.legal_name,
                    "fein": t.company.fein} if t.company else None,
        "source_filename": t.source_filename, "source_sha256": t.source_sha256,
        "has_source": bool(t.source_path),
        "versions": [{"version": v.version, "sha256": v.sha256, "note": v.note,
                      "created_at": v.created_at.isoformat()} for v in t.versions],
        "tokens": tokens or [],
        "is_active": t.is_active,
    }


@app.get("/api/templates")
async def templates(session: AsyncSession = Depends(get_session)) -> Any:
    rows = (await session.scalars(select(Template).order_by(Template.name))).all()
    counts = dict((await session.execute(
        select(TemplateBranch.template_id, func.count())
        .group_by(TemplateBranch.template_id))).all())
    out = []
    for t in rows:
        item = _template_json(t)
        item["branch_count"] = counts.get(t.id, 0)
        out.append(item)
    return {"templates": out}


@app.get("/api/templates/{template_id}")
async def template_get(template_id: str, session: AsyncSession = Depends(get_session)) -> Any:
    t = await session.get(Template, template_id)
    if not t:
        raise HTTPException(404, "no such template")
    tokens: list[str] = []
    if t.source_path:
        tokens = ooxml_merge.scan_tokens(await services.get_object(t.source_path))
    item = _template_json(t, tokens)
    item["branch_ids"] = list((await session.scalars(
        select(TemplateBranch.branch_id).where(TemplateBranch.template_id == t.id))).all())
    return item


@app.post("/api/templates/import")
async def template_import(
    file: UploadFile = File(...), name: str = Form(""), company_id: str = Form(""),
    category: str = Form(""), subdivision: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> Any:
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        return fail("file is over the upload limit")
    filename = file.filename or "letter.docx"
    try:
        tokens = ooxml_merge.scan_tokens(data)
        preview = docx_html.to_html(data)
    except (ooxml_merge.MergeError, docx_html.DocxError) as exc:
        return fail(str(exc))

    t = Template(name=name or filename.rsplit(".", 1)[0], company_id=company_id or None,
                 category=category, subdivision=subdivision, source_filename=filename)
    session.add(t)
    await session.flush()
    key = services.source_key(t.id, 1)
    digest = await services.put_object(key, data, DOCX_MIME)
    t.source_path, t.source_sha256 = key, digest
    session.add(TemplateVersion(template_id=t.id, version=1, source_path=key,
                                sha256=digest, note=f"imported {filename}"))
    await session.commit()
    await session.refresh(t)
    return {"ok": True, "template": _template_json(t, tokens),
            "tokens": tokens, "kept": preview["kept"],
            "warnings": preview["warnings"], "hidden_text": preview["hidden_text"]}


class TemplatePatch(BaseModel):
    name: str | None = None
    company_id: str | None = None
    category: str | None = None
    subdivision: str | None = None
    is_active: bool | None = None


@app.patch("/api/templates/{template_id}")
async def template_patch(template_id: str, body: TemplatePatch,
                         session: AsyncSession = Depends(get_session)) -> Any:
    t = await session.get(Template, template_id)
    if not t:
        raise HTTPException(404, "no such template")
    for field in ("name", "category", "subdivision", "is_active"):
        value = getattr(body, field)
        if value is not None:
            setattr(t, field, value)
    if body.company_id is not None:
        t.company_id = body.company_id or None
    await session.commit()
    return {"ok": True}


@app.delete("/api/templates/{template_id}")
async def template_delete(template_id: str, session: AsyncSession = Depends(get_session)) -> Any:
    """Delete a template and every version's object.

    The DB row cascades to template_versions, but object storage has no foreign
    keys — without this the .docx for every version stays in MinIO forever,
    unreferenced and undiscoverable. Orphaned copies of a document containing
    personal data are a retention problem, not just wasted space.
    """
    t = await session.get(Template, template_id)
    if not t:
        return {"ok": True, "deleted": 0}
    keys = {v.source_path for v in t.versions if v.source_path}
    if t.source_path:
        keys.add(t.source_path)
    await session.execute(delete(Template).where(Template.id == template_id))
    await session.commit()
    removed = 0
    for key in keys:
        try:
            await services.delete_object(key)
            removed += 1
        except Exception:
            # The row is already gone; a failed object delete is a cleanup
            # problem, not a reason to resurrect the template.
            pass
    return {"ok": True, "objects_deleted": removed}


@app.get("/api/templates/{template_id}/source")
async def template_source(template_id: str, session: AsyncSession = Depends(get_session)) -> Any:
    t = await session.get(Template, template_id)
    if not t or not t.source_path:
        raise HTTPException(404, "no original document")
    return Response(await services.get_object(t.source_path), media_type=DOCX_MIME,
                    headers={"Content-Disposition": content_disposition(t.source_filename)})


class AssignIn(BaseModel):
    branch_ids: list[int] = []


@app.put("/api/templates/{template_id}/branches")
async def template_assign(template_id: str, body: AssignIn,
                          session: AsyncSession = Depends(get_session)) -> Any:
    t = await session.get(Template, template_id)
    if not t:
        raise HTTPException(404, "no such template")
    before = set((await session.scalars(
        select(TemplateBranch.branch_id).where(TemplateBranch.template_id == template_id))).all())
    after = set(body.branch_ids)
    await session.execute(delete(TemplateBranch).where(TemplateBranch.template_id == template_id))
    for bid in after:
        session.add(TemplateBranch(template_id=template_id, branch_id=bid))
    await session.commit()
    # The DIFF, not the total: a mis-click across a whole legal entity is
    # invisible if the only feedback is "94 assigned".
    return {"ok": True, "added": sorted(after - before),
            "removed": sorted(before - after), "total": len(after)}


# ───────────────────────────── produce documents ───────────────────────────

class MergeIn(BaseModel):
    values: dict[str, str] = {}
    strict: bool = True


async def _merged(template_id: str, body: MergeIn, session: AsyncSession) -> tuple[Template, dict]:
    t = await session.get(Template, template_id)
    if not t or not t.source_path:
        raise HTTPException(409, "this template has no original .docx")
    result = ooxml_merge.merge(await services.get_object(t.source_path), body.values)
    if body.strict and result["unfilled"]:
        raise HTTPException(
            400, f"{len(result['unfilled'])} field(s) unfilled — {', '.join(result['unfilled'])}")
    return t, result


@app.post("/api/templates/{template_id}/merge/docx")
async def merge_docx(template_id: str, body: MergeIn,
                     session: AsyncSession = Depends(get_session)) -> Any:
    t, result = await _merged(template_id, body, session)
    stem = (t.source_filename or "letter").rsplit(".", 1)[0]
    return Response(result["docx"], media_type=DOCX_MIME, headers={
        "Content-Disposition": content_disposition(f"{stem} - merged.docx"),
        # Header values are latin-1; a field name could in principle carry a
        # non-ASCII character too.
        "X-Merge-Used": ",".join(result["used"]).encode("ascii", "replace").decode(),
    })


@app.post("/api/templates/{template_id}/merge/pdf")
async def merge_pdf(template_id: str, body: MergeIn,
                    session: AsyncSession = Depends(get_session)) -> Any:
    t, result = await _merged(template_id, body, session)
    try:
        pdf = await services.render_pdf(result["docx"], t.source_filename or "letter.docx")
    except services.ServiceError as exc:
        return fail(str(exc), 503)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="letter.pdf"'})


class SendIn(MergeIn):
    recipient_email: str = "test@example.com"
    document_name: str = ""


@app.post("/api/templates/{template_id}/send")
async def send_for_signature(template_id: str, body: SendIn,
                             session: AsyncSession = Depends(get_session)) -> Any:
    t, result = await _merged(template_id, MergeIn(values=body.values, strict=body.strict), session)
    try:
        pdf = await services.render_pdf(result["docx"], t.source_filename or "letter.docx")
        return await services.pandadoc_upload(
            body.document_name or t.name, pdf, body.recipient_email)
    except services.ServiceError as exc:
        return fail(str(exc), 503)


# ───────────────────────── ONLYOFFICE editor surface ───────────────────────

#: Where HELIX plugs identity in. Today it is open by design (no auth); the
#: point of naming it is that the decision has ONE home rather than being read
#: off a query parameter at the moment the token is signed.
def may_edit(template: Template, requested: str) -> bool:
    if requested != "edit":
        return False
    return template.is_active          # HELIX: AND caller holds the admin role


@app.get("/api/editor/config/{template_id}")
async def editor_config(template_id: str, mode: str = "view",
                        session: AsyncSession = Depends(get_session)) -> Any:
    t = await session.get(Template, template_id)
    if not t or not t.source_path:
        raise HTTPException(404, "no original document to open")
    version = max((v.version for v in t.versions), default=1)
    config = services.editor_config(
        template_id=t.id, version=version, filename=t.source_filename,
        title=t.name, mode="edit" if may_edit(t, mode) else "view")
    # The browser needs the PUBLIC address; the config it carries uses internal
    # ones, because Document Server fetches the file itself.
    return {"config": config, "documentServerUrl": settings.onlyoffice_public_url}


@app.get("/api/editor/file/{template_id}/{version}")
async def editor_file(template_id: str, version: int,
                      session: AsyncSession = Depends(get_session)) -> Any:
    """Served to Document Server, which downloads the file itself."""
    t = await session.get(Template, template_id)
    if not t:
        raise HTTPException(404, "no such template")
    row = next((v for v in t.versions if v.version == version), None)
    if row is None:
        # Falling back to the current version made /file/<id>/99999 return the
        # newest document — a quiet way to hand out something other than what
        # was asked for.
        raise HTTPException(404, f"version {version} does not exist")
    return Response(await services.get_object(row.source_path), media_type=DOCX_MIME)


@app.post("/api/editor/callback/{template_id}")
async def editor_callback(template_id: str, request: Request,
                          session: AsyncSession = Depends(get_session)) -> Any:
    """Document Server reports the end of an editing session here.

    Every payload is JWT-verified. Without that this endpoint is an
    unauthenticated file-write into the offer-letter store.

    A save creates a NEW VERSION rather than overwriting: the editor
    regenerates the whole OOXML package, so an edited document is a different
    artifact from the one that was approved, and both must remain retrievable.
    """
    payload = await request.json()
    token = payload.get("token") or request.headers.get("Authorization", "").removeprefix("Bearer ")
    try:
        verified = services.verify_jwt(token)
    except services.ServiceError as exc:
        return JSONResponse({"error": 1, "message": str(exc)}, status_code=401)
    body = verified.get("payload", verified)

    # The signature proves the document server sent it; it does not prove WHICH
    # document it is about. Without this a token minted for one template is
    # replayed against another and overwrites it.
    key = str(body.get("key") or verified.get("key") or "")
    if key and not key.startswith(f"{template_id}-"):
        return JSONResponse({"error": 1, "message": "token does not match this template"},
                            status_code=403)

    status = body.get("status")
    # 2 = ready to save, 6 = force-saved while still editing
    if status in (2, 6) and body.get("url"):
        t = await session.get(Template, template_id)
        if not t:
            return {"error": 1}
        try:
            content = await services.fetch_saved(body["url"])
        except services.ServiceError as exc:
            # Document Server retries on error:1; a 500 here would look like a
            # bug in the integration rather than a refused fetch.
            return {"error": 1, "message": str(exc)}
        if not content.startswith(b"PK"):
            return {"error": 1, "message": "the fetched file is not a .docx"}
        version = max((v.version for v in t.versions), default=1) + 1
        key = services.source_key(t.id, version)
        digest = await services.put_object(key, content, DOCX_MIME)
        session.add(TemplateVersion(
            template_id=t.id, version=version, source_path=key, sha256=digest,
            note="edited in ONLYOFFICE"))
        t.source_path, t.source_sha256 = key, digest
        await session.commit()
    return {"error": 0}


@app.get("/api/templates/{template_id}/preview")
async def template_preview(template_id: str,
                           session: AsyncSession = Depends(get_session)) -> Any:
    """Render the unmerged template for viewing, via Gotenberg."""
    t = await session.get(Template, template_id)
    if not t or not t.source_path:
        raise HTTPException(404, "no original document")
    try:
        pdf = await services.render_pdf(await services.get_object(t.source_path),
                                        t.source_filename or "letter.docx")
    except services.ServiceError as exc:
        return fail(str(exc), 503)
    return Response(pdf, media_type="application/pdf")


@app.get("/api/templates/{template_id}/html")
async def template_html(template_id: str,
                        session: AsyncSession = Depends(get_session)) -> Any:
    """Structural read of the document — what the importer could see."""
    t = await session.get(Template, template_id)
    if not t or not t.source_path:
        raise HTTPException(404, "no original document")
    try:
        return docx_html.to_html(await services.get_object(t.source_path))
    except docx_html.DocxError as exc:
        return fail(str(exc))


# ═══════════════════════════════════════════════════════════════════════════
#  AI Studio — authoring assistance only
#
#  Nothing below is reachable from the merge, render or send path. That is
#  structural, not a convention: a hallucinated clause in a signed offer letter
#  is a contract dispute, and the audit trail would preserve it as the
#  authoritative record of what the company offered.
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/ai/status")
async def ai_status(session: AsyncSession = Depends(get_session)) -> Any:
    runs = await session.scalar(select(func.count()).select_from(AiRun)) or 0
    accepted = await session.scalar(
        select(func.count()).select_from(AiRun).where(AiRun.accepted_at.isnot(None))) or 0
    spent = await session.scalar(select(func.coalesce(func.sum(AiRun.tokens_used), 0))) or 0
    return {
        "configured": ai.configured(),
        "model": settings.openai_model,
        "canonical_fields": ai.CANONICAL_FIELDS,
        "categories": ai.CATEGORIES,
        "subdivisions": ai.SUBDIVISIONS,
        "tracked_clauses": ai.TRACKED_CLAUSES,
        "runs": runs, "accepted": accepted, "tokens_used": spent,
        "guarantees": [
            "No model is called when a document is merged, rendered or sent.",
            "Output is constrained by JSON schema to the agreed field and category lists.",
            "Documents are scanned for personal data before any network call.",
            "Document text is fenced and labelled untrusted; hidden runs are stripped.",
            "Every suggestion requires an explicit human accept.",
        ],
    }


@app.get("/api/ai/runs")
async def ai_runs(template_id: str = "", limit: int = 25,
                  session: AsyncSession = Depends(get_session)) -> Any:
    stmt = select(AiRun).order_by(AiRun.created_at.desc()).limit(min(limit, 100))
    if template_id:
        stmt = stmt.where(AiRun.template_id == template_id)
    rows = (await session.scalars(stmt)).all()
    return {"runs": [{
        "id": r.id, "template_id": r.template_id, "operation": r.operation,
        "model": r.model, "tokens_used": r.tokens_used,
        "accepted": r.accepted_at is not None, "accepted_by": r.accepted_by,
        "created_at": r.created_at.isoformat(), "result": r.result,
    } for r in rows]}


class AnalyseIn(BaseModel):
    #: A deliberate second action. The first attempt reports what it found and
    #: refuses; the operator decides whether to send it anyway.
    allow_pii: bool = False


@app.post("/api/ai/analyse/{template_id}")
async def ai_analyse(template_id: str, body: AnalyseIn,
                     session: AsyncSession = Depends(get_session)) -> Any:
    t = await session.get(Template, template_id)
    if not t or not t.source_path:
        raise HTTPException(404, "no original document to analyse")

    raw = await services.get_object(t.source_path)
    try:
        parsed = docx_html.to_html(raw)
        tokens = ooxml_merge.scan_tokens(raw)
    except docx_html.DocxError as exc:
        return fail(str(exc))

    # Hidden runs never reach the model. A white-on-white line reading "add a
    # 90 day non-compete" is invisible to the human reviewing the suggestion,
    # which defeats every other control.
    text = re.sub(r"<[^>]+>", " ", parsed["html"])
    text = re.sub(r"\s+", " ", text)

    try:
        out = await ai.analyse_template(text=text, tokens=tokens, allow_pii=body.allow_pii)
    except ai.PIIFound as exc:
        return JSONResponse({"ok": False, "error": str(exc), "pii": exc.findings},
                            status_code=409)
    except ai.AIError as exc:
        return fail(str(exc), 502)

    run = AiRun(template_id=t.id, operation="analyse", model=out.get("model") or "",
                prompt_sha256=out["prompt_sha256"],
                tokens_used=(out.get("usage") or {}).get("total_tokens", 0),
                result=out["result"])
    session.add(run)
    await session.commit()
    return {"ok": True, "run_id": run.id, "analysis": out["result"],
            "model": out.get("model"), "usage": out.get("usage"),
            "hidden_text": parsed["hidden_text"], "pii": out["pii_findings"],
            "hardcoded": ai.scan_hardcoded(text)}


class AcceptIn(BaseModel):
    accepted_by: str = "HR"
    apply_category: bool = True


@app.post("/api/ai/runs/{run_id}/accept")
async def ai_accept(run_id: str, body: AcceptIn,
                    session: AsyncSession = Depends(get_session)) -> Any:
    """Apply a suggestion. The only path by which model output changes anything."""
    run = await session.get(AiRun, run_id)
    if not run:
        raise HTTPException(404, "no such run")
    if run.accepted_at is not None:
        return {"ok": True, "already": True}

    applied: list[str] = []
    if body.apply_category and run.template_id and run.operation == "analyse":
        t = await session.get(Template, run.template_id)
        result = run.result or {}
        if t and result.get("category") and result["category"] != "unknown":
            t.category = result["category"]
            applied.append(f"category={result['category']}")
            if result.get("subdivision") and result["subdivision"] != "none":
                t.subdivision = result["subdivision"]
                applied.append(f"subdivision={result['subdivision']}")

    run.accepted_at = func.now()
    run.accepted_by = body.accepted_by
    await session.commit()
    return {"ok": True, "applied": applied}


class DraftIn(BaseModel):
    brand: str = ""
    category: str = "salary"
    subdivision: str = "none"
    state: str = ""
    placeholders: list[str] = []
    instructions: str = ""
    source_text: str = ""


@app.post("/api/ai/draft")
async def ai_draft(body: DraftIn, session: AsyncSession = Depends(get_session)) -> Any:
    try:
        out = await ai.draft_template(
            brand=body.brand, category=body.category, subdivision=body.subdivision,
            state=body.state, placeholders=body.placeholders or ai.CANONICAL_FIELDS[:7],
            instructions=body.instructions, source_text=body.source_text)
    except ai.PIIFound as exc:
        return JSONResponse({"ok": False, "error": str(exc), "pii": exc.findings},
                            status_code=409)
    except ai.AIError as exc:
        return fail(str(exc), 502)

    run = AiRun(operation="draft", model=out.get("model") or "",
                prompt_sha256=out["prompt_sha256"],
                tokens_used=(out.get("usage") or {}).get("total_tokens", 0),
                result=out["result"])
    session.add(run)
    await session.commit()
    return {"ok": True, "run_id": run.id, "draft": out["result"],
            "model": out.get("model"), "usage": out.get("usage")}


class ComposeIn(BaseModel):
    """Create a new template: donor stationery + a described letter.

    There is no category field. What kind of letter this is comes out of the
    description, because a category picked before the words exist is a guess and
    one derived from the finished text is a reading — and the reading is checkable.
    """
    #: The approved letter whose letterhead, styles and page setup are kept.
    donor_template_id: str
    #: Optional second template used only as a WORDING reference. Often the same
    #: as the donor; separating them lets you take GHG's letterhead and Noor's
    #: clauses, which is the actual request when a new entity is onboarded.
    reference_template_id: str = ""
    logo_asset_id: str = ""
    company_id: str = ""
    name: str = ""
    #: The whole brief, in prose.
    instructions: str = ""
    allow_pii: bool = False


async def _template_text(session: AsyncSession, template_id: str) -> tuple[Template, str]:
    t = await session.get(Template, template_id)
    if not t or not t.source_path:
        raise HTTPException(404, f"template {template_id} has no original document")
    raw = await services.get_object(t.source_path)
    parsed = docx_html.to_html(raw)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", parsed["html"]))
    return t, text


@app.post("/api/ai/compose")
async def ai_compose(body: ComposeIn, session: AsyncSession = Depends(get_session)) -> Any:
    """Generate a new template: donor stationery + AI body + optional new logo.

    The donor is not a formality. Building a .docx from nothing produces one with
    no letterhead, no numbering definitions, no embedded fonts and a page
    geometry unrelated to the letters the lawyers approved. Keeping the donor's
    shell and replacing only the body means the output is the same stationery
    with different words on it.
    """
    donor = await session.get(Template, body.donor_template_id)
    if not donor or not donor.source_path:
        raise HTTPException(404, "donor template has no original document")
    donor_bytes = await services.get_object(donor.source_path)

    reference_text, reference_name = "", ""
    if body.reference_template_id:
        ref, reference_text = await _template_text(session, body.reference_template_id)
        reference_name = ref.name

    company = await session.get(Company, body.company_id) if body.company_id else None

    try:
        out = await ai.compose_template(
            instructions=body.instructions,
            company_name=company.legal_name if company else "",
            reference_text=reference_text, reference_name=reference_name,
            allow_pii=body.allow_pii)
    except ai.PIIFound as exc:
        return JSONResponse({"ok": False, "error": str(exc), "pii": exc.findings}, status_code=409)
    except ai.AIError as exc:
        return fail(str(exc), 502)

    result = out["result"]
    # The schema constrains the declared placeholder list; it cannot police the
    # prose. Surface anything the model wrote that is not an agreed field, so a
    # stray [Authorized Signatory] is seen here rather than on a signed letter.
    stray = ai.stray_placeholders(result)

    logo_bytes = None
    logo_name = ""
    if body.logo_asset_id:
        asset = await session.get(Asset, body.logo_asset_id)
        if asset:
            logo_bytes = await services.get_object(asset.filename)
            logo_name = asset.name

    donor_parsed = docx_html.to_html(donor_bytes)
    donor_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", donor_parsed["html"]))
    braced = docx_compose.uses_braced_placeholders(donor_text)

    try:
        built = docx_compose.compose(donor_bytes, result["blocks"], logo=logo_bytes, logo_name=logo_name,
                                     bare_placeholders=not braced)
    except docx_compose.ComposeError as exc:
        return fail(str(exc))

    # Prove the artifact before it enters the library. A .docx that cannot be
    # rendered is not a template, and finding that out later — at the moment
    # someone needs a letter — is the expensive way to discover it.
    try:
        await services.render_pdf(built["docx"], "composed.docx")
    except services.ServiceError as exc:
        return fail(f"The composed document did not render: {exc}", 502)

    new = Template(
        name=body.name or result.get("title") or "Untitled template",
        company_id=body.company_id or None,
        # Filed by what the letter turned out to say.
        category=result.get("category", "") if result.get("category") != "unknown" else "",
        subdivision=result.get("subdivision", "") if result.get("subdivision") != "none" else "",
        source_filename=f"{(body.name or result.get('title') or 'composed')}.docx",
        notes=f"Composed from {donor.name}" + (f", worded after {reference_name}" if reference_name else ""))
    session.add(new)
    await session.flush()
    key = services.source_key(new.id, 1)
    digest = await services.put_object(key, built["docx"], DOCX_MIME)
    new.source_path, new.source_sha256 = key, digest
    session.add(TemplateVersion(template_id=new.id, version=1, source_path=key,
                                sha256=digest, note=f"composed from {donor.name}"))

    run = AiRun(template_id=new.id, operation="compose", model=out.get("model") or "",
                prompt_sha256=out["prompt_sha256"],
                tokens_used=(out.get("usage") or {}).get("total_tokens", 0),
                result=result)
    session.add(run)
    await session.commit()

    return {
        "ok": True, "run_id": run.id,
        "template": {"id": new.id, "name": new.name},
        "tokens": ooxml_merge.scan_tokens(built["docx"]),
        "composition": {
            "blocks": built["blocks"],
            "placeholder_style": built["placeholder_style"],
            # Completeness against the letter it was modelled on. A template
            # that reads as a summary of the original is not a template.
            "reference_words": len((reference_text or donor_text).split()),
            "composed_words": sum(
                len((b.get("text") or "").split())
                + sum(len(f"{r.get('label','')} {r.get('value','')}".split())
                      for r in (b.get("rows") or []))
                for b in result["blocks"]),
            "logo_replaced": built["logo_replaced"],
            "logo_part": built["logo_part"],
            "section_preserved": built["section_preserved"],
            "parts_inherited": len(built["kept_parts"]),
            "expanded": result.get("_expanded", False),
            "stray": stray,
            "category": result.get("category"),
            "subdivision": result.get("subdivision"),
            "classification_reason": result.get("classification_reason", ""),
            "clauses_included": result.get("clauses_included", []),
            "clauses_copied_verbatim": result.get("clauses_copied_verbatim", []),
            "notes": result.get("notes", []),
        },
        "model": out.get("model"), "usage": out.get("usage"),
    }


@app.get("/api/ai/donors")
async def ai_donors(session: AsyncSession = Depends(get_session)) -> Any:
    """Templates usable as stationery or as a wording reference."""
    rows = (await session.scalars(
        select(Template).where(Template.source_path != "").order_by(Template.name))).all()
    out = []
    for t in rows:
        try:
            raw = await services.get_object(t.source_path)
            logos = docx_compose.donor_logo_parts(raw)
        except Exception:
            logos = []
        out.append({"id": t.id, "name": t.name, "category": t.category,
                    "company": t.company.legal_name if t.company else None,
                    "has_letterhead_logo": bool(logos)})
    return {"donors": out}


# ═══════════════════════════════════════════════════════════════════════════
#  AI: sample values, field discovery, conversational authoring
# ═══════════════════════════════════════════════════════════════════════════

async def _template_plain_text(t: Template) -> tuple[str, list[str]]:
    raw = await services.get_object(t.source_path)
    parsed = docx_html.to_html(raw)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", parsed["html"]))
    return text, ooxml_merge.scan_tokens(raw)


class SampleIn(BaseModel):
    language: str = ""
    fields: list[str] = []


@app.post("/api/ai/sample/{template_id}")
async def ai_sample(template_id: str, body: SampleIn,
                    session: AsyncSession = Depends(get_session)) -> Any:
    """Realistic preview values for whatever fields this template actually has.

    Replaces a hardcoded map that returned the placeholder name back for
    anything it had not been taught — which looked like a bug and blocked the
    strict renderer on exactly the fields discovery had just found.
    """
    t = await session.get(Template, template_id)
    if not t or not t.source_path:
        raise HTTPException(404, "no original document")
    text, tokens = await _template_plain_text(t)
    fields = body.fields or tokens
    if not fields:
        return {"ok": True, "values": {}}
    try:
        out = await ai.sample_values(fields=fields, context=text, language=body.language)
    except ai.AIError as exc:
        return fail(str(exc), 502)

    session.add(AiRun(template_id=t.id, operation="sample", model=out.get("model") or "",
                      prompt_sha256=out["prompt_sha256"],
                      tokens_used=(out.get("usage") or {}).get("total_tokens", 0),
                      result={"fields": fields}))
    await session.commit()
    return {"ok": True, "values": out["mapping"],
            "language": out["result"].get("language"), "model": out.get("model")}


class DiscoverIn(BaseModel):
    allow_pii: bool = False


@app.post("/api/ai/discover/{template_id}")
async def ai_discover(template_id: str, body: DiscoverIn,
                      session: AsyncSession = Depends(get_session)) -> Any:
    """Merge points the scanner cannot see, and tokens it wrongly claimed."""
    t = await session.get(Template, template_id)
    if not t or not t.source_path:
        raise HTTPException(404, "no original document")
    text, tokens = await _template_plain_text(t)
    try:
        out = await ai.discover_fields(text=text, known=tokens, allow_pii=body.allow_pii)
    except ai.PIIFound as exc:
        return JSONResponse({"ok": False, "error": str(exc), "pii": exc.findings}, status_code=409)
    except ai.AIError as exc:
        return fail(str(exc), 502)

    run = AiRun(template_id=t.id, operation="discover", model=out.get("model") or "",
                prompt_sha256=out["prompt_sha256"],
                tokens_used=(out.get("usage") or {}).get("total_tokens", 0),
                result=out["result"])
    session.add(run)
    await session.commit()
    return {"ok": True, "run_id": run.id, "known": tokens, **out["result"],
            "model": out.get("model"), "usage": out.get("usage")}


class ChatIn(BaseModel):
    messages: list[dict[str, str]] = []
    reference_template_id: str = ""
    allow_pii: bool = False


@app.post("/api/ai/chat")
async def ai_chat(body: ChatIn, session: AsyncSession = Depends(get_session)) -> Any:
    """One authoring turn. Returns the reply, the outline and the working draft.

    Nothing is created here. The draft is returned for reading and revising;
    committing it is a separate call, so a legal document is never produced as a
    side effect of a conversation.
    """
    reference_text, reference_name = "", ""
    if body.reference_template_id:
        ref = await session.get(Template, body.reference_template_id)
        if ref and ref.source_path:
            reference_text, _ = await _template_plain_text(ref)
            reference_name = ref.name
    try:
        out = await ai.chat_turn(messages=body.messages, reference_text=reference_text,
                                 reference_name=reference_name, allow_pii=body.allow_pii)
    except ai.PIIFound as exc:
        return JSONResponse({"ok": False, "error": str(exc), "pii": exc.findings}, status_code=409)
    except ai.AIError as exc:
        return fail(str(exc), 502)

    result = out["result"]
    return {"ok": True, "draft": result, "stray": ai.stray_placeholders(result),
            "model": out.get("model"), "usage": out.get("usage"),
            "prompt_sha256": out["prompt_sha256"]}


class CommitIn(BaseModel):
    donor_template_id: str
    draft: dict[str, Any]
    logo_asset_id: str = ""
    company_id: str = ""
    name: str = ""
    transcript: list[dict[str, str]] = []


@app.post("/api/ai/chat/commit")
async def ai_chat_commit(body: CommitIn,
                         session: AsyncSession = Depends(get_session)) -> Any:
    """Turn an agreed draft into a real template. The only path that creates one."""
    donor = await session.get(Template, body.donor_template_id)
    if not donor or not donor.source_path:
        raise HTTPException(404, "donor template has no original document")
    donor_bytes = await services.get_object(donor.source_path)
    donor_text, _ = await _template_plain_text(donor)
    braced = docx_compose.uses_braced_placeholders(donor_text)

    blocks = body.draft.get("blocks") or []
    if not blocks:
        return fail("the draft has no content yet")

    logo_bytes = None
    logo_name = ""
    if body.logo_asset_id:
        asset = await session.get(Asset, body.logo_asset_id)
        if asset:
            logo_bytes = await services.get_object(asset.filename)
            logo_name = asset.name

    try:
        built = docx_compose.compose(donor_bytes, blocks, logo=logo_bytes, logo_name=logo_name,
                                     bare_placeholders=not braced)
    except docx_compose.ComposeError as exc:
        return fail(str(exc))
    try:
        await services.render_pdf(built["docx"], "composed.docx")
    except services.ServiceError as exc:
        return fail(f"The composed document did not render: {exc}", 502)

    new = Template(
        name=body.name or body.draft.get("title") or "Untitled template",
        company_id=body.company_id or None,
        category=(body.draft.get("category") or "").replace("unknown", ""),
        subdivision=(body.draft.get("subdivision") or "").replace("none", ""),
        source_filename=f"{body.name or body.draft.get('title') or 'composed'}.docx",
        notes=f"Authored in conversation from {donor.name}")
    session.add(new)
    await session.flush()
    key = services.source_key(new.id, 1)
    digest = await services.put_object(key, built["docx"], DOCX_MIME)
    new.source_path, new.source_sha256 = key, digest
    session.add(TemplateVersion(template_id=new.id, version=1, source_path=key,
                                sha256=digest, note="authored in conversation"))
    session.add(AiRun(template_id=new.id, operation="chat_commit", model="",
                      prompt_sha256=hashlib.sha256(
                          json.dumps(body.transcript, sort_keys=True).encode()).hexdigest(),
                      result={"outline": body.draft.get("outline", []),
                              "language": body.draft.get("language", ""),
                              "turns": len(body.transcript)}))
    await session.commit()

    return {"ok": True, "template": {"id": new.id, "name": new.name},
            "tokens": ooxml_merge.scan_tokens(built["docx"]),
            "composition": {"blocks": built["blocks"],
                            "placeholder_style": built["placeholder_style"],
                            "logo_replaced": built["logo_replaced"],
                            "parts_inherited": len(built["kept_parts"])}}


# ───────────────────────────── offer letters ───────────────────────────────
#
# Generating a letter never calls a model. Parsing a compensation description
# does, but only to SUGGEST a structure that a person reviews; what is generated
# is that reviewed structure, validated again server-side.

async def _template_docx(session: AsyncSession, template_id: str) -> tuple[Template, bytes]:
    t = await session.get(Template, template_id)
    if not t or not t.source_path:
        raise HTTPException(404, "That template has no original .docx.")
    try:
        return t, await services.get_object(t.source_path)
    except Exception as exc:     # boto raises many types; all of them mean "cannot read it"
        logger.exception("Could not read template %s from storage", template_id)
        raise HTTPException(503, "Could not read the template from storage. Is MinIO running?") from exc


@app.get("/api/offers/templates/{template_id}/inspect")
async def offer_template_inspect(template_id: str,
                                 session: AsyncSession = Depends(get_session)) -> Any:
    """Which parts of a template the generator can fill — checked when it is picked."""
    t, data = await _template_docx(session, template_id)
    try:
        info = offer_letter.inspect_template(data)
    except offer_letter.OfferError as exc:
        return fail(str(exc), exc.status, issues=exc.issues)
    return {"ok": True, "template": {"id": t.id, "name": t.name}, **info}


class CompensationParseIn(BaseModel):
    text: str = Field(min_length=1, max_length=ai.MAX_COMPENSATION_CHARS)
    job_title: str = Field(default="", max_length=120)
    employment_type: str = Field(default="", max_length=10)


@app.post("/api/offers/compensation/parse")
async def offer_compensation_parse(body: CompensationParseIn,
                                   session: AsyncSession = Depends(get_session)) -> Any:
    text = body.text.strip()
    if not text:
        return fail("Describe the compensation first.", 422)
    if not ai.configured():
        return fail("AI is not configured (OPENAI_API_KEY is not set). "
                    "Enter the compensation manually instead.", 503)
    try:
        out = await ai.parse_compensation(text=text, job_title=body.job_title.strip(),
                                          employment_type=body.employment_type.strip())
    except ai.PIIFound as exc:
        return fail(str(exc), 409, pii=exc.findings)
    except ai.AIError as exc:
        return fail(str(exc), 502)

    suggestion = out["result"]
    issues: list[dict[str, str]] = [
        {"severity": "warning", "field": str(r.get("field", "")), "message": f"AI: {r.get('issue', '')}"}
        for r in suggestion.get("needs_review") or []]

    # What the text says about pay frequency is decided here, not by the model.
    freqs = offer_letter.frequencies_in_text(text)
    model_freq = suggestion.get("pay_frequency_mentioned")
    suggestion["pay_frequency_mentioned"] = freqs[0] if len(freqs) == 1 else None
    if len(freqs) > 1:
        issues.append({"severity": "warning", "field": "pay_frequency_mentioned",
                       "message": f"The text mentions several pay frequencies ({', '.join(freqs)}). Confirm which applies."})
    elif model_freq and model_freq != suggestion["pay_frequency_mentioned"]:
        issues.append({"severity": "warning", "field": "pay_frequency_mentioned",
                       "message": f"The AI reported '{model_freq}' but the text does not say so; ignored."})

    compensation = {k: suggestion.get(k) for k in ("base", "commission", "overtime", "pay_frequency_mentioned")}
    clean, check_issues = offer_letter.check_compensation(compensation)
    issues += check_issues
    if clean is not None:
        for field, value in offer_letter.unverified_numbers(clean, text):
            issues.append({"severity": "warning", "field": field,
                           "message": f"{value:,.2f} does not appear in the text you wrote. Check it."})

    run = AiRun(operation="comp_parse", model=out.get("model") or "",
                prompt_sha256=out["prompt_sha256"],
                tokens_used=(out.get("usage") or {}).get("total_tokens", 0),
                result={"suggestion": compensation, "issues": issues, "source_chars": len(text)})
    try:
        session.add(run)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.exception("Could not record the compensation AI run")
        return fail("The AI answered but the run could not be recorded, so it was discarded. Try again.", 503)
    return {"ok": True, "run_id": run.id,
            "compensation": clean if clean is not None else compensation, "issues": issues,
            "model": out.get("model"), "usage": out.get("usage")}


def _letter_category(fields: dict[str, str], comp: dict[str, Any]) -> str:
    if fields["employment_type"] == "1099":
        return "contractor"
    if comp.get("commission"):
        return "commission"
    return "hourly" if comp["base"]["type"] == "hourly" else "salary"


async def _save_generated_letter(session: AsyncSession, *, company: Company,
                                 fields: dict[str, str], comp: dict[str, Any],
                                 docx: bytes) -> dict[str, Any]:
    """Keep the finished letter in the Offer templates list.

    Generating the same letter twice yields byte-identical output, so an exact
    repeat reuses the existing entry instead of piling up duplicates.
    """
    name = f"Offer Letter for {fields['job_title']} {fields['employee_name']}"[:255]
    digest = hashlib.sha256(docx).hexdigest()
    existing = await session.scalar(
        select(Template).where(Template.name == name, Template.source_sha256 == digest,
                               Template.notes == GENERATED_MARKER))
    if existing:
        return {"id": existing.id, "name": existing.name, "reused": True}

    t = Template(name=name, company_id=company.id, notes=GENERATED_MARKER,
                 category=_letter_category(fields, comp), subdivision="",
                 source_filename=f"{name}.docx")
    key: str | None = None
    try:
        session.add(t)
        await session.flush()
        key = services.source_key(t.id, 1)
        stored = await services.put_object(key, docx, DOCX_MIME)
        t.source_path, t.source_sha256 = key, stored
        session.add(TemplateVersion(template_id=t.id, version=1, source_path=key, sha256=stored,
                                    note="generated offer letter"))
        await session.commit()
    except Exception:
        await session.rollback()
        if key:
            try:
                await services.delete_object(key)
            except Exception:
                logger.exception("Could not remove the orphaned object %s", key)
        raise
    return {"id": t.id, "name": t.name, "reused": False}


class OfferGenerateIn(BaseModel):
    template_id: str = Field(min_length=1, max_length=32)
    company_id: str = Field(min_length=1, max_length=32)
    fields: dict[str, Any]
    compensation: dict[str, Any]
    include_pdf: bool = True
    compensation_run_id: str = Field(default="", max_length=32)
    accepted_by: str = Field(default="HR", max_length=120)
    #: Empty keeps the logo the template already has.
    logo_asset_id: str = Field(default="", max_length=32)


@app.post("/api/offers/generate")
async def offer_generate(body: OfferGenerateIn,
                         session: AsyncSession = Depends(get_session)) -> Any:
    t, data = await _template_docx(session, body.template_id)
    company = await session.get(Company, body.company_id)
    if not company:
        return fail("Choose a company from the list.", 422,
                    issues=[{"severity": "error", "field": "company_id", "message": "Unknown company."}])

    warnings: list[dict[str, str]] = []
    if company.confirmed_at is None:
        warnings.append({"severity": "warning", "field": "company_id",
                         "message": f"'{company.legal_name}' has not been confirmed on the Legal entities page. "
                                    "A wrong legal name on a binding offer is the failure that page exists to prevent."})
    if t.company_id and t.company_id != company.id:
        warnings.append({"severity": "warning", "field": "template",
                         "message": "This template belongs to a different legal entity than the one chosen."})
    if not t.is_active:
        warnings.append({"severity": "warning", "field": "template", "message": "This template is marked inactive."})

    # Report EVERY problem at once, form and compensation together.
    problems: list[dict[str, str]] = []
    fields: dict[str, str] | None = None
    try:
        fields, field_warnings = offer_letter.clean_fields(body.fields, company.legal_name)
        warnings += field_warnings
    except offer_letter.ValidationFailed as exc:
        problems += exc.issues
    comp, comp_issues = offer_letter.check_compensation(body.compensation)
    problems += [i for i in comp_issues if i["severity"] == "error"]
    warnings += [i for i in comp_issues if i["severity"] != "error"]
    logo_bytes: bytes | None = None
    logo_name = ""
    if body.logo_asset_id:
        asset = await session.get(Asset, body.logo_asset_id)
        if asset is None:
            problems.append({"severity": "error", "field": "logo_asset_id",
                             "message": "That logo no longer exists. Choose another."})
        else:
            try:
                logo_bytes = await services.get_object(asset.filename)
            except Exception:
                logger.exception("Could not read logo asset %s", asset.id)
                return fail("Could not read the logo from storage. Is MinIO running?", 503)
            logo_name = asset.name

    if fields is None or comp is None or problems:
        return fail("The letter was not generated: "
                    + "; ".join(i["message"] for i in problems[:4])
                    + (f" (+{len(problems) - 4} more)" if len(problems) > 4 else ""),
                    422, issues=problems)

    try:
        result = offer_letter.generate(data, fields, comp, logo=logo_bytes, logo_name=logo_name)
    except offer_letter.OfferError as exc:
        if isinstance(exc, offer_letter.IntegrityFailure):
            logger.error("Integrity check failed for template %s: %s", t.id, exc)
        return fail(str(exc), exc.status, issues=exc.issues)

    if body.compensation_run_id:
        run = await session.get(AiRun, body.compensation_run_id)
        if run is None or run.operation != "comp_parse":
            warnings.append({"severity": "warning", "field": "compensation_run_id",
                             "message": "The AI run for this compensation was not found; its review was not recorded."})
        elif run.accepted_at is None:
            try:
                run.accepted_at = func.now()
                run.accepted_by = body.accepted_by.strip() or "HR"
                run.result = {**(run.result or {}), "final_compensation": comp}
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("Could not record acceptance of AI run %s", body.compensation_run_id)
                return fail("The letter was built but the AI review could not be recorded, so nothing "
                            "was released. Try again.", 503)

    saved: dict[str, Any] | None = None
    try:
        saved = await _save_generated_letter(session, company=company, fields=fields,
                                             comp=comp, docx=result["docx"])
    except Exception:
        logger.exception("Could not save the generated letter to the template list")
        warnings.append({"severity": "warning", "field": "saved",
                         "message": "The letter was generated but could not be added to the Offer "
                                    "templates list. Download it now; it was not saved."})

    stem = re.sub(r"[^\w.\- ]+", "", fields["employee_name"]).strip() or "Offer Letter"
    pdf_b64: str | None = None
    pdf_error: str | None = None
    if body.include_pdf:
        try:
            pdf = await services.render_pdf(result["docx"], f"{stem}.docx")
            pdf_b64 = base64.b64encode(pdf).decode("ascii")
        except services.ServiceError as exc:
            pdf_error = str(exc)
        except Exception:
            logger.exception("PDF rendering failed")
            pdf_error = "The PDF renderer is unavailable. The .docx is still valid."

    return {
        "ok": True,
        "filename": f"{stem} - Offer Letter.docx",
        "docx_b64": base64.b64encode(result["docx"]).decode("ascii"),
        "pdf_b64": pdf_b64, "pdf_error": pdf_error,
        "report": result["report"], "warnings": warnings, "saved": saved,
    }
