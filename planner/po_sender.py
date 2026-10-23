"""Step 8: draft purchase orders and sending them to Zoho. Blocked while the system is PAUSED.

A run's draft POs are:
  - every Track A SKU with a rule quantity above 0 (reason: the rule's templated reason)
  - every Track B SKU Raghav approved or edited to a quantity above 0 (reason: his reason)
They are created in Zoho as DRAFTS; procurement approves them there.
"""

import logging
from datetime import datetime

from sqlalchemy import select

from planner.config import now_ist
from planner.db import DraftPO, ReviewDecision, Run, SkuDecision, get_sessionmaker
from planner.killswitch import KillSwitchError, require_not_paused
from planner.synthetic_data import VENDORS
from planner.zoho.base import ZohoAdapter

log = logging.getLogger("planner.po_sender")


def send_draft_pos(pos: list[dict], adapter: ZohoAdapter, sent_by: str, db_url: str | None = None) -> list[str]:
    """Raises SystemPaused (and sends nothing) if the kill switch is PAUSED."""
    require_not_paused("send draft POs to Zoho", db_url)
    ids = [adapter.create_draft_po(po) for po in pos]
    log.info("%s sent %d draft POs to Zoho", sent_by, len(ids))
    return ids


def reviews_for_run(session, run: Run) -> dict[str, ReviewDecision]:
    rows = session.scalars(select(ReviewDecision).where(ReviewDecision.run_date == run.run_date,
                                                        ReviewDecision.seeded.is_(False))).all()
    return {r.sku_id: r for r in rows}


def draft_lines(session, run: Run) -> list[dict]:
    """All draft PO lines for a run, with whether each has been sent."""
    decisions = session.scalars(select(SkuDecision).where(SkuDecision.run_id == run.id)).all()
    reviews = reviews_for_run(session, run)
    sent = {p.sku_id: p for p in session.scalars(select(DraftPO).where(DraftPO.run_id == run.id))}
    lines = []
    for d in decisions:
        if d.track == "A" and (d.recommended_qty or 0) > 0:
            qty, source, reason = d.recommended_qty, "rule", d.reason
        elif d.track == "B" and d.sku_id in reviews and reviews[d.sku_id].action != "reject" \
                and reviews[d.sku_id].final_qty > 0:
            r = reviews[d.sku_id]
            verb = {"approve": "Approved", "edit": "Edited"}[r.action]
            qty, source, reason = r.final_qty, "review", f"{verb} by {r.decided_by}: {r.reason}"
        else:
            continue
        p = sent.get(d.sku_id)
        lines.append({"sku_id": d.sku_id, "name": d.name, "category": d.category, "perishability": d.perishability,
                      "vendor_name": VENDORS.get(d.perishability, "Freshroots Plant"), "qty": int(qty),
                      "unit_cost_inr": d.unit_cost_inr, "value_inr": round(qty * d.unit_cost_inr),
                      "source": source, "reason": reason, "status": "sent" if p else "draft",
                      "zoho_po_id": p.zoho_po_id if p else None, "sent_by": p.sent_by if p else None,
                      "sent_at": p.sent_at.isoformat() if p else None})
    return sorted(lines, key=lambda x: (x["source"], -x["value_inr"]))


def send_run_drafts(run_id: int, sent_by: str, adapter: ZohoAdapter, db_url: str | None = None,
                    now: datetime | None = None) -> dict:
    """Send every not-yet-sent draft line of a run to Zoho and record it."""
    if not (sent_by or "").strip():
        raise KillSwitchError("Say who is sending the POs")
    require_not_paused("send draft POs to Zoho", db_url)
    with get_sessionmaker(db_url)() as s:
        run = s.get(Run, run_id)
        if run is None or run.status != "completed":
            raise ValueError("No completed run with that id")
        to_send = [line for line in draft_lines(s, run) if line["status"] == "draft"]
        at = (now or now_ist()).replace(tzinfo=None)
        for line in to_send:
            zoho_id = adapter.create_draft_po(line)
            s.add(DraftPO(run_id=run.id, sku_id=line["sku_id"], name=line["name"], vendor_name=line["vendor_name"],
                          qty=line["qty"], unit_cost_inr=line["unit_cost_inr"], source=line["source"],
                          reason=line["reason"], zoho_po_id=zoho_id, sent_by=sent_by.strip(), sent_at=at))
            s.commit()        # one at a time, so a failure part-way never loses what was already sent
    log.info("%s sent %d draft POs for run %s to Zoho", sent_by, len(to_send), run_id)
    return {"sent": len(to_send), "value_inr": sum(line["value_inr"] for line in to_send)}
