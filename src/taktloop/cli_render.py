"""Rendering for the ``taktloop`` CLI (pure: dict in, str out)."""

from datetime import UTC, datetime


def _when(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def _table(rows: list[tuple[str, ...]]) -> list[str]:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]


def render_summary(d: dict) -> str:
    return f"#{d['id']} {d['title']} [{d['status']}] — {d['waiting_for']}"


def _event_detail(e: dict) -> str:
    """Short extra column: what a gate decision, release or completion carried."""
    d = e["detail"]
    if e["operation"] == "decide_gate":
        return ": ".join(x for x in (d.get("decision"), d.get("reason")) if x)
    if e["operation"] == "escalate":
        return f"{d['review_step']} failed {d['issue_rounds']} times → {d['gate']}"
    if e["operation"] == "step_opened":
        return f"round {d['round']}"
    if e["operation"] == "task_closed":
        return d["status"]
    if e["operation"] == "route_at_intake":
        return f"{d['route']} by {d['by']}"
    if e["operation"] == "report_stall":
        return f"stalled: {d['reason']}"
    if e["operation"] == "override_review":
        return f"override: {d.get('reason')}"
    if e["operation"] == "release_step":
        return f"{d.get('reason', '')}{' (force)' if d.get('force') else ''}"
    if e["operation"] == "check":
        return f"{d['result']}: {d['summary']}"
    if e["operation"] == "complete_step" and d.get("route"):
        return f"route {d['route']} → {d['next']}: {d['findings']}"
    if e["operation"] == "complete_step" and d.get("outcome") in ("pass", "issues", "failed"):
        return f"{d['outcome']} → {d.get('next', '?')}"
    if e["operation"] == "complete_step":
        return ", ".join([a["name"] for a in d.get("artifacts", [])]
                         + [f"{k}={v}" for k, v in (d.get("evidence") or {}).items()])
    return ""


def _event_note(e: dict) -> str:
    """Last history column: the rejection reason first, then what the call carried."""
    detail = _event_detail(e)
    if not e["reason"]:
        return detail
    return f"{e['reason']} ({detail})" if detail else e["reason"]


def render_task_detail(d: dict) -> str:
    lines = [f"#{d['id']} {d['title']} [{d['status']}]"]
    if d["step_id"] is not None:
        lines.append(f"step: {d['current_step']}  (step id {d['step_id']})")
    lines.append(f"waiting for: {d['waiting_for']}")
    lines += [f"  {d['description']}"] if d.get("description") else []
    lines += ["", "artifacts:"]
    arts = [(a["name"], f"{a['path']}@{a['commit'][:12]}", a["branch"], f"{a['step']} r{a['round']}")
            for a in d["artifacts"]]
    lines += ["  " + ln for ln in _table(arts)] if arts else ["  none"]
    lines += ["", "history:"]
    hist = [
        (_when(e["at"]), f"{e['subject']} ({e['executor']})" if e["executor"] else e["subject"],
         ", ".join(e["roles"]) or "-", e["operation"], e["step"] or "-", e["result"], _event_note(e))
        for e in d["history"]
    ]  # fmt: skip
    lines += ["  " + ln for ln in _table(hist)] if hist else ["  none"]
    return "\n".join(lines)


def render_task_list(d: dict) -> str:
    if not d["tasks"]:
        return "no tasks"
    return "\n".join(
        f"#{t['id']} {t['title']} [{t['status']}]  {t['waiting_for']}" for t in d["tasks"]
    )


def render_steps(d: dict) -> str:
    if not d["steps"]:
        return "no open steps"
    return "\n".join(
        f"step {s['step_id']}  task #{s['task_id']} {s['task_title']}  {s['step']} "
        f"(round {s['round']}, skill {s['skill']})"
        for s in d["steps"]
    )


def render_workflow(d: dict) -> str:
    lines = [f"draft based on version {d['base_version']}" if d["draft"] else f"version {d['version']}"]
    for name, s in d["data"]["steps"].items():
        flow = [t for k in ("next", "on_approve", "on_reject", "on_pass", "on_issues", "on_escalate", "on_fail")
                for t in ([s[k]] if isinstance(s.get(k), str) else s.get(k) or [])]
        flow += [f"{r}:{t}" for r, t in (s.get("routes") or {}).items()]
        lines.append(f"{name}: {s['kind']} {s['role']} → {', '.join(flow)}")
        lines += [f"  {s['description']}"] if s.get("description") else []
    return "\n".join(lines + [f"problem: {p}" for p in d["problems"]])
