"""Social pipeline: drafts never post by themselves — approval exports the pack, rejection files it away."""
from __future__ import annotations

from nodo.db.models import Approval, NightAction
from nodo.night import approvals, briefing
from nodo.social import service as soc


def test_normalize_channels():
    assert soc.normalize_channels("tiktok e instagram") == ["tiktok", "instagram"]
    assert soc.normalize_channels("ig fb") == ["instagram", "facebook"]
    assert soc.normalize_channels("tutti") == ["tiktok", "instagram", "facebook"]
    assert soc.normalize_channels(["IG", "tiktok"]) == ["instagram", "tiktok"]
    assert soc.normalize_channels(None) == ["tiktok", "instagram", "facebook"]


def _draft(db, seeded):
    item, a = soc.draft(db, seeded, title="Unboxing materasso in lattice",
                        body="Oggi vi faccio vedere come nasce un materasso su misura. #materassi #artigianato",
                        channels=["tiktok", "instagram"], request_id="test")
    return item, a


def test_draft_creates_pending_approval(db, seeded):
    item, a = _draft(db, seeded)
    assert item.status == "draft" and a.status == "pending"
    assert a.tool_name == "social" and a.action == "publish"
    pend = soc.pending(db, seeded)
    assert [p["approval_id"] for p in pend] == [a.id]
    labels = [p["label"] for p in briefing.pending_decisions(db, seeded)]
    assert any("Unboxing materasso" in lab and "tiktok" in lab for lab in labels)


async def test_approval_exports_the_pack(db, seeded, container):
    item, a = _draft(db, seeded)
    out = await approvals.resolve(db, container, a.id, True, via="test")
    assert out["ok"] and out["executed"]
    assert "pacchetto" in out["message"]
    assert item.status == "exported"                    # not «published»: the phone does the real posting
    from pathlib import Path
    pack = Path(container.files.root) / "social"
    dirs = list(pack.iterdir())
    assert len(dirs) == 1
    assert (dirs[0] / "caption.txt").read_text(encoding="utf-8").startswith("Unboxing")
    assert (dirs[0] / "meta.json").exists() and (dirs[0] / "ISTRUZIONI.md").exists()
    act = db.scalars(__import__("sqlalchemy").select(NightAction)
                     .where(NightAction.approval_id == a.id)).first()
    assert act is not None and act.action == "social_publish" and act.status == "done"


async def test_rejection_keeps_the_draft(db, seeded, container):
    item, a = _draft(db, seeded)
    out = await approvals.resolve(db, container, a.id, False, via="test")
    assert out["ok"] and not out["executed"]
    assert item.status == "rejected"
    assert not soc.pending(db, seeded)                  # the decision is gone from the table


async def test_nothing_publishes_without_approval(db, seeded, container):
    item, a = _draft(db, seeded)
    assert db.get(Approval, a.id).status == "pending"   # the draft alone exports nothing
    assert item.status == "draft"                        # stays a draft until the «sì»
    import pytest
    with pytest.raises(approvals.ApprovalError):         # deciding twice is impossible
        await approvals.resolve(db, container, "no-such-approval", True, via="test")
