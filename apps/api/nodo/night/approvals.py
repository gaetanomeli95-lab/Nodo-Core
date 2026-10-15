"""Approval execution (Phase 4): when you say yes, NODO does what it proposed — and records it.

A resolved approval runs the exact tool action and parameters that were stored when it was requested (nothing is
re-decided at approval time). Rejections clean up after themselves (a rejected fix PR is closed and its branch
deleted). Used by the REST endpoint and by the voice channel ("sì" bound to the pending approval).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from nodo.core.permissions import ActionLevel
from nodo.db.models import Approval, EventLog, FixProposal, Incident, NightAction, Site, ToolExecution
from nodo.tools.base import ToolSpec
from nodo.tools.github import github_tool
from nodo.tools.vercel import READY, vercel_tool

if TYPE_CHECKING:
    from nodo.app import Container

log = logging.getLogger("nodo.approvals")
_background: set[asyncio.Task] = set()


class ApprovalError(RuntimeError):
    pass


def _specs(c: Container) -> dict[str, ToolSpec]:
    specs: dict[str, ToolSpec] = {"github": github_tool(c.github, c.repo)}
    if c.vercel is not None:
        specs["vercel"] = vercel_tool(c.vercel)
    return specs


def _night_action(db: Session, a: Approval, action: str, status: str, summary: str, details: dict | None = None,
                  site_id: str | None = None, incident_id: str | None = None) -> NightAction:
    na = NightAction(organization_id=a.organization_id, site_id=site_id, incident_id=incident_id, action=action,
                     status=status, summary=summary, details=details or {}, approval_id=a.id)
    db.add(na)
    db.flush()
    return na


async def resolve(db: Session, c: Container, approval_id: str, approve: bool, via: str = "api") -> dict:
    a = db.get(Approval, approval_id)
    if a is None or a.status != "pending":
        raise ApprovalError("approvazione non trovata o già decisa")
    a.status, a.resolved_at = ("approved" if approve else "rejected"), datetime.now(UTC)
    db.add(EventLog(event_type=f"approval.{a.status}", request_id=a.request_id, organization_id=a.organization_id,
                    payload={"approval_id": a.id, "tool": a.tool_name, "action": a.action, "via": via}))
    db.flush()
    try:
        out = await (_execute(db, c, a) if approve else _reject(db, c, a))
    except Exception as e:  # the decision is recorded even if the execution fails
        log.exception("approval execution failed")
        out = {"executed": False, "ok": False, "message": f"Decisione registrata, ma l'esecuzione è fallita: {e}"}
    db.flush()
    return {"approval_id": a.id, "status": a.status, **out}


async def _run_tool(db: Session, c: Container, a: Approval, action: str | None = None, params: dict | None = None,
                    tool: str | None = None) -> dict:
    tool = tool or a.tool_name
    spec = _specs(c).get(tool)
    if spec is None or (action or a.action) not in spec.actions:
        raise ApprovalError(f"strumento {tool}.{action or a.action} non disponibile (accessi mancanti?)")
    act = spec.action(action or a.action)
    params = params if params is not None else dict(a.payload or {})
    ex = ToolExecution(request_id=a.request_id, tool_name=tool, action=act.name, level=int(act.level), input=params)
    db.add(ex)
    t0 = time.perf_counter()
    try:
        out = await act.handler(**params)
        ex.status, ex.output = "ok", out
        return out
    except Exception as e:
        ex.status, ex.error = "failed", f"{type(e).__name__}: {e}"
        raise
    finally:
        ex.duration_ms = int((time.perf_counter() - t0) * 1000)
        db.flush()


async def _execute(db: Session, c: Container, a: Approval) -> dict:
    fix = db.scalars(select(FixProposal).where(FixProposal.approval_id == a.id)).first()
    act = db.scalars(select(NightAction).where(NightAction.approval_id == a.id)).first()
    site_id = (fix.site_id if fix else act.site_id) if (fix or act) else None
    site = db.get(Site, site_id) if site_id else None          # NODO's own night actions have no site
    key = f"{a.tool_name}.{a.action}"
    if key == "nodo.self_patch":                     # NODO's own code, improved during the night
        from nodo.night.selfcare import apply_patch
        out = await apply_patch(dict(a.payload or {}))
        if act:
            act.status = "done" if out.get("ok") else "failed"
            act.summary = out["message"]
        return out
    if key == "github.merge_pr":
        out = await _run_tool(db, c, a)
        if not out.get("merged"):
            return {"executed": True, "ok": False, "message": f"GitHub non ha unito la PR: {out.get('message')}"}
        msg = f"Correzione pubblicata: PR #{a.payload.get('number')} unita"
        if fix:
            fix.status = "merged"
            _night_action(db, a, "merge", "done", msg + ". Verifico il deploy.",
                          {"fix_id": fix.id, "sha": out.get("sha")}, fix.site_id, fix.incident_id)
            if fix.incident_id and (inc := db.get(Incident, fix.incident_id)):
                inc.resolution = (inc.resolution + "; " if inc.resolution else "") + f"correzione PR #{fix.pr_number} pubblicata"
            if site and site.vercel_project_id and c.vercel is not None:
                db.flush()
                watch_after_merge(c, fix.id, out.get("sha"))
        return {"executed": True, "ok": True, "message": msg + ": Vercel la sta pubblicando, la verifico io."}
    if key == "github.open_fix_pr":
        out = await _run_tool(db, c, a)
        if fix:
            fix.commit_sha, fix.pr_number, fix.pr_url = out.get("sha"), out.get("pr_number"), out.get("pr_url")
            fix.preview_status = "pending"
            merge = Approval(organization_id=a.organization_id, request_id=a.request_id, tool_name="github",
                             action="merge_pr", level=int(ActionLevel.EXECUTE_SENSITIVE),
                             payload={"owner": a.payload["owner"], "name": a.payload["name"],
                                      "number": out.get("pr_number"), "method": "squash"},
                             reason=f"Pubblicare la correzione (PR #{out.get('pr_number')})")
            db.add(merge)
            db.flush()
            fix.approval_id = merge.id
        return {"executed": True, "ok": True,
                "message": f"Pull request aperta (#{out.get('pr_number')}). Quando l'anteprima è pronta ti chiedo "
                           "l'ok per pubblicarla."}
    if key in ("vercel.rollback", "vercel.redeploy", "vercel.promote"):
        out = await _run_tool(db, c, a)
        label = {"vercel.rollback": "Rollback eseguito", "vercel.redeploy": "Build rilanciata",
                 "vercel.promote": "Versione pubblicata"}[key]
        if act:
            act.status = "done"
            act.summary = f"{label} su tua approvazione."
            if key == "vercel.rollback" and act.incident_id and (inc := db.get(Incident, act.incident_id)):
                inc.status, inc.resolution = "mitigated", "rollback approvato da te"
        return {"executed": True, "ok": True, "message": f"{label}.", "output": out}
    out = await _run_tool(db, c, a)
    return {"executed": True, "ok": True, "message": "Fatto.", "output": out}


async def _reject(db: Session, c: Container, a: Approval) -> dict:
    fix = db.scalars(select(FixProposal).where(FixProposal.approval_id == a.id)).first()
    act = db.scalars(select(NightAction).where(NightAction.approval_id == a.id)).first()
    if a.tool_name == "nodo" and a.action == "self_patch":
        from nodo.night.selfcare import drop_patch
        if act:
            act.status = "skipped"
        return await drop_patch(dict(a.payload or {}))
    if fix:
        fix.status = "rejected"
        site = db.get(Site, fix.site_id)
        if fix.pr_number and site and c.repo is not None:
            try:
                await _run_tool(db, c, a, action="close_pr", params={
                    "owner": site.repo_owner, "name": site.repo_name, "number": fix.pr_number, "branch": fix.branch,
                    "comment": "Correzione rifiutata da NODO su indicazione del proprietario."})
                _night_action(db, a, "close_pr", "done", f"PR #{fix.pr_number} chiusa e branch eliminato.",
                              {"fix_id": fix.id}, fix.site_id, fix.incident_id)
            except Exception as e:
                return {"executed": False, "ok": False,
                        "message": f"Correzione rifiutata, ma non sono riuscito a chiudere la PR: {e}"}
        return {"executed": False, "ok": True, "message": "Ok, correzione scartata: non pubblico nulla."}
    if act:
        act.status = "skipped"
        act.summary = act.summary + " (rifiutato da te)"
    return {"executed": False, "ok": True, "message": "Ok, non lo faccio."}


# ---------------------------------------------------------------------------------------------- after merge
def watch_after_merge(c: Container, fix_id: str, merge_sha: str | None) -> asyncio.Task:
    task = asyncio.create_task(post_merge_watch(c, fix_id, merge_sha))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


async def post_merge_watch(c: Container, fix_id: str, merge_sha: str | None, timeout: float | None = None,
                           poll: float | None = None) -> str:
    """Wait for the production deployment of the merge, promote it if Vercel is holding production after a
    rollback, then verify the site. Records the outcome as a NightAction."""
    from nodo.db.session import session_scope
    from nodo.night.shift import _join_url
    timeout = c.settings.night_preview_wait_seconds * 2 if timeout is None else timeout
    poll = c.night_poll_interval if poll is None else poll
    with session_scope() as db:
        fix = db.get(FixProposal, fix_id)
        site = db.get(Site, fix.site_id) if fix else None
        if not (fix and site and c.vercel and site.vercel_project_id):
            return "skipped"
        org, site_id, incident_id = fix.organization_id, site.id, fix.incident_id
        project_id, url, pr = site.vercel_project_id, site.url, fix.pr_number
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    dep = None
    while True:
        try:
            deps = await c.vercel.list_deployments(project_id, target="production", limit=5)
            dep = next((d for d in deps if not merge_sha or d.commit_sha == merge_sha), None)
        except Exception:
            dep = None
        if (dep and dep.state in (READY, "ERROR", "CANCELED")) or loop.time() >= deadline:
            break
        await asyncio.sleep(poll)
    status, summary, details = "failed", "", {"fix_id": fix_id, "deployment_id": dep.id if dep else None}
    try:
        if dep is None or dep.state not in (READY, "ERROR", "CANCELED"):
            status, summary = "done", f"Il deploy della PR #{pr} è ancora in corso: lo verifico al prossimo giro."
        elif dep.state != READY:
            summary = f"Il deploy della PR #{pr} è fallito: in produzione resta la versione precedente."
        else:
            project = await c.vercel.get_project(project_id)
            if project.production_deployment_id != dep.id:  # production pinned by a previous rollback
                await c.vercel.promote(project_id, dep.id)
                details["promoted"] = True
            r = await c.prober.probe(_join_url(url, "/"))
            if r.ok:
                status, summary = "verified", f"La correzione (PR #{pr}) è online e il sito risponde ({r.status})."
            else:
                summary = f"La correzione (PR #{pr}) è online ma il sito non risponde bene ({r.short()})."
    except Exception as e:
        summary = f"Verifica dopo la pubblicazione non riuscita: {e}"
    notifier = c.notifier
    if notifier is not None and notifier.enabled:  # you approved it (maybe from the phone): tell how it went
        try:
            await notifier.send(f"NODO · {summary}")
        except Exception:
            pass
    with session_scope() as db:
        db.add(NightAction(organization_id=org, site_id=site_id, incident_id=incident_id, action="verify",
                           status=status, summary=summary, details=details))
        if status == "verified":
            # the fix is live and the site answers: close the root incident and what the bad deploy had caused
            for inc in db.scalars(select(Incident).where(Incident.site_id == site_id,
                                                         Incident.status.in_(("open", "mitigated")))):
                if inc.id == incident_id or inc.status == "mitigated":
                    inc.status, inc.resolved_at = "resolved", datetime.now(UTC)
                    inc.resolution = (inc.resolution + "; " if inc.resolution else "") + \
                        "correzione verificata in produzione"
            site = db.get(Site, site_id)
            still = db.scalars(select(Incident).where(Incident.site_id == site_id,
                                                      Incident.status.in_(("open", "mitigated")))).all()
            if site is not None:
                site.status = "ok" if not still else max(
                    ("critical" if i.severity == "critical" and i.status == "open" else "warning" for i in still),
                    key=lambda x: x == "critical")
                site.status_note = None if not still else still[0].title
    return status
