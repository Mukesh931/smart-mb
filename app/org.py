"""Smart-MB :: organisation model.

PWD Electrical works are executed by a division, which is made up of sub-divisions,
which are made up of sections.  A measurement book belongs to a section, is checked by
the sub-divisional officer and is administratively owned by the division - so the app
has to know that hierarchy:

    PWD Electrical Division Dhule
      └── PWD Electrical Sub Division Jalgaon
            ├── Section Jalgaon-1
            ├── Section Jalgaon-2
            └── Section Amalner

Roles map onto the hierarchy: ``ee`` (division), ``sdo`` (sub-division), ``section``
(section).  What an officer can see follows the hierarchy: an EE sees every project in
the division, an SDO every project of the sub-division, a section officer only their own
section.  ``admin`` sees everything.
"""
from __future__ import annotations

from .db import ex, now_iso, q, q1

KINDS = ("division", "subdivision", "section")
ROLES = ("admin", "ee", "sdo", "section", "engineer")
ROLE_LABEL = {
    "admin": "Administrator",
    "ee": "Executive Engineer (Division)",
    "sdo": "Sub Divisional Officer",
    "section": "Section Officer / JE",
    "engineer": "Engineer",
}
ROLE_KIND = {"ee": "division", "sdo": "subdivision", "section": "section"}

DEFAULT_TREE = {
    "PWD Electrical Division Dhule": {
        "PWD Electrical Sub Division Jalgaon": ["Jalgaon-1", "Jalgaon-2", "Amalner"],
    },
}


# ------------------------------------------------------------------ structure
def seed_org() -> None:
    """Create the division tree once; never touches an organisation an admin has edited."""
    if q1("SELECT id FROM org_units LIMIT 1"):
        return
    ts = now_iso()
    for division, subs in DEFAULT_TREE.items():
        did = ex("INSERT INTO org_units (parent_id, kind, name, code, is_active, created_at)"
                 " VALUES (NULL,'division',?,?,1,?)", (division, _code_for(division), ts)).lastrowid
        for sub, sections in subs.items():
            sid = ex("INSERT INTO org_units (parent_id, kind, name, code, is_active, created_at)"
                     " VALUES (?,?,?,?,1,?)", (did, "subdivision", sub, _code_for(sub), ts)).lastrowid
            for sec in sections:
                ex("INSERT INTO org_units (parent_id, kind, name, code, is_active, created_at)"
                   " VALUES (?,?,?,?,1,?)", (sid, "section", sec, _code_for(f"{sub} {sec}"), ts))


def _code_for(name: str) -> str:
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in name).split() if w]
    return "-".join(w[:3].upper() for w in words)[:24]


def unit(uid: int | None) -> dict | None:
    if not uid:
        return None
    row = q1("SELECT * FROM org_units WHERE id=?", (uid,))
    return dict(row) if row else None


def units(kind: str | None = None, active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM org_units"
    where, params = [], []
    if kind:
        where.append("kind=?")
        params.append(kind)
    if active_only:
        where.append("is_active=1")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE kind WHEN 'division' THEN 1 WHEN 'subdivision' THEN 2 ELSE 3 END, name"
    return [dict(r) for r in q(sql, tuple(params))]


def tree(active_only: bool = True) -> list[dict]:
    """The whole organisation as a nested list, for the pickers in the UI."""
    rows = units(active_only=active_only)
    by_id = {r["id"]: dict(r, children=[]) for r in rows}
    roots: list[dict] = []
    for row in rows:
        node = by_id[row["id"]]
        parent = by_id.get(row["parent_id"])
        (parent["children"] if parent else roots).append(node)
    return roots


def path_of(uid: int | None) -> str:
    """'PWD Electrical Division Dhule › PWD Electrical Sub Division Jalgaon › Jalgaon-1'."""
    names, seen = [], set()
    while uid and uid not in seen:
        seen.add(uid)
        row = q1("SELECT id, parent_id, name FROM org_units WHERE id=?", (uid,))
        if not row:
            break
        names.append(row["name"])
        uid = row["parent_id"]
    return " › ".join(reversed(names))


def ancestors(uid: int | None) -> dict[str, dict]:
    """The user's own unit and its parents, keyed by kind - used to fill project defaults."""
    out: dict[str, dict] = {}
    while uid:
        row = q1("SELECT * FROM org_units WHERE id=?", (uid,))
        if not row:
            break
        out.setdefault(row["kind"], dict(row))
        uid = row["parent_id"]
    return out


def descendant_ids(uid: int | None) -> set[int]:
    """The unit and everything under it (recursive CTE, so deep trees are fine)."""
    if not uid:
        return set()
    rows = q("""WITH RECURSIVE down(id) AS (
                    SELECT id FROM org_units WHERE id=?
                    UNION ALL
                    SELECT u.id FROM org_units u JOIN down d ON u.parent_id = d.id)
                SELECT id FROM down""", (uid,))
    return {r["id"] for r in rows}


# ------------------------------------------------------------------ scoping
def scope_ids(user: dict | None) -> set[int] | None:
    """Org units this user may see.  ``None`` means unrestricted."""
    if not user:
        return set()
    if user.get("role") == "admin":
        return None
    uid = user.get("org_unit_id")
    if uid:
        return descendant_ids(uid)
    return None          # legacy accounts without a unit keep the old "everything" view


def default_unit_for(user: dict | None) -> int | None:
    """Where a new project by this user belongs: their own unit, else their parent."""
    if not user:
        return None
    uid = user.get("org_unit_id")
    if not uid:
        return None
    role = user.get("role")
    want = ROLE_KIND.get(role)
    if want:                      # an EE creates projects at division level, an SDO at sub-division
        anc = ancestors(uid)
        if want in anc:
            return anc[want]["id"]
    return uid


def scope_label(user: dict | None) -> str:
    if not user:
        return ""
    if user.get("role") == "admin":
        return "All divisions"
    uid = user.get("org_unit_id")
    if not uid:
        return "All projects"
    return path_of(uid)


def project_sql(user: dict | None, alias: str = "p") -> tuple[str, list]:
    """SQL fragment (no leading AND) restricting projects to the user's organisation."""
    ids = scope_ids(user)
    if ids is None:
        return "1=1", []
    if not ids:
        return "1=0", []
    marks = ",".join("?" for _ in ids)
    # projects created before the organisation model (org_unit_id NULL) stay visible to
    # everyone who can see the division - there is no better place to file them.
    return (f"({alias}.org_unit_id IN ({marks}) OR {alias}.org_unit_id IS NULL)",
            sorted(ids))


DIVISION = "PWD Electrical Division Dhule"
SUBDIVISION = "PWD Electrical Sub Division Jalgaon"
SECTIONS = ("Jalgaon-1", "Jalgaon-2", "Amalner")
