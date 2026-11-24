"""Pinned source facts and explicit, narrower read-only tasks for the existing MAS.

Original user scenarios, reference actions and outcomes are never executed as
customer input. Reference order IDs only index the orders used to build new
status questions. Their scope is deliberately different from the original tasks.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "evaluation" / "tau-retail" / "source"
TASK_SETS = {"reviewed50": "tasks_reviewed_50.json", "legacy114": "tasks_114.json"}
WRITE_TOOLS = {"cancel_pending_order", "exchange_delivered_order_items",
               "return_delivered_order_items", "modify_pending_order_items",
               "modify_pending_order_address", "modify_pending_order_payment", "modify_user_address"}


def load_source():
    manifest = json.loads((SOURCE / "manifest.json").read_text())
    for name, spec in manifest["files"].items():
        raw = (SOURCE / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
            raise ValueError(f"Source checksum mismatch: {name}")
    db = json.loads((SOURCE / "db.json").read_text())
    sets = {key: json.loads((SOURCE / name).read_text()) for key, name in TASK_SETS.items()}
    for key, count in (("reviewed50", 50), ("legacy114", 114)):
        tasks = sets[key]
        if len(tasks) != count or len({t["id"] for t in tasks}) != count:
            raise ValueError(f"Unexpected task set: {key}")
    if not {t["id"] for t in sets["reviewed50"]} <= {t["id"] for t in sets["legacy114"]}:
        raise ValueError("Reviewed IDs are not a subset of the original task IDs")
    emails = [u["email"].casefold() for u in db["users"].values()]
    if len(emails) != len(set(emails)):
        raise ValueError("Probe email lookup must be unambiguous")
    for oid, order in db["orders"].items():
        if oid != order["order_id"] or order["user_id"] not in db["users"]:
            raise ValueError(f"Invalid source order: {oid}")
    return db, sets, manifest


def status_fixture(db):
    """Project only actual identity/ownership/status facts; invent no dates/rules."""
    customers = []
    for uid, user in db["users"].items():
        customers.append({"customer_id": uid,
            "name": user["name"]["first_name"] + " " + user["name"]["last_name"],
            "email": user["email"],
            "orders": [{"order_id": oid, "status": o["status"]}
                       for oid, o in db["orders"].items() if o["user_id"] == uid]})
    return {"policy_version": "tau-status-probes-v1", "evaluation_date": None,
            "customers": customers}


def status_probe(oid, db, source_ids):
    order = db["orders"][oid]
    uid = order["user_id"]
    return {"id": "TAU-" + oid.removeprefix("#"), "title": f"Recorded {order['status']} status for {oid}",
        "category": "status", "kind": "status", "context": {"customer_id": None},
        "lookup_customer_id": uid, "case": {"order_id": oid}, "policy_rules": [],
        "messages": [f"What is the status of order {oid}?", db["users"][uid]["email"]],
        "expected_output": f"Order {oid} is {order['status']}.",
        "expected": {"agent": "order", "status": "completed", "reason_code": "order_status_found",
            "response_requirements": ["Identify the customer through their supplied email before private lookup.",
                f"Report {oid} as {order['status']}; retain the order across identification.",
                "Do not invent delivery dates, estimates, refund facts, or completed actions."]},
        "expected_tool_names": ["identify_customer", "identify_customer", "get_order"],
        "source_task_ids": sorted(source_ids, key=int), "expected_source_status": order["status"]}


def build_probes(task_set="reviewed50"):
    db, sets, manifest = load_source()
    tasks = sets[task_set]
    references, no_order, invalid = {}, [], []
    writes, handoffs, operations = 0, 0, Counter()
    for task in tasks:
        actions = task["evaluation_criteria"].get("actions") or []
        operations.update(a["name"] for a in actions)
        writes += any(a["name"] in WRITE_TOOLS for a in actions)
        handoffs += any(a["name"] == "transfer_to_human_agents" for a in actions)
        found = False
        for action in actions:
            oid = action.get("arguments", {}).get("order_id")
            if not oid:
                continue
            if oid not in db["orders"]:
                invalid.append({"source_task_id": task["id"], "order_id": oid})
                continue  # Some are intentionally incorrect user-supplied IDs.
            references.setdefault(oid, set()).add(task["id"])
            found = True
        if not found:
            no_order.append(task["id"])
    probes = [status_probe(oid, db, ids) for oid, ids in sorted(references.items())]
    if not probes:
        raise ValueError("No source-backed order probes")

    # Synthetic controls are reported separately, not presented as native tasks.
    first = probes[0]
    foreign = json.loads(json.dumps(first))
    foreign.update(id="TAU-CROSS-OWNER", title="Generic denial for another customer's order",
                   kind="control", source_task_ids=[])
    other = next(uid for uid in db["users"] if uid != first["lookup_customer_id"])
    foreign["lookup_customer_id"] = other
    foreign["messages"][1] = db["users"][other]["email"]
    unavailable = {"agent": "order", "status": "denied", "reason_code": "order_unavailable",
                   "response_requirements": ["Identify the customer using email.",
                       "Give a generic unavailable response; disclose no other customer's order facts."]}
    foreign["expected"] = unavailable
    foreign["expected_output"] = "This order is unavailable for the identified customer."
    foreign.pop("expected_source_status")
    unknown = json.loads(json.dumps(foreign))
    unknown.update(id="TAU-UNKNOWN-ORDER", title="Same generic denial for a nonexistent order")
    oid = "#W0000000"
    if oid in db["orders"]:
        raise ValueError("Unknown-order control collides with source")
    unknown["case"]["order_id"] = oid
    unknown["messages"][0] = f"What is the status of order {oid}?"
    corrected = json.loads(json.dumps(first))
    corrected.update(id="TAU-EMAIL-CORRECTION", title="Corrected email retains the source order",
                     kind="control", source_task_ids=[])
    wrong_email = "nobody@tau-probes.invalid"
    if any(u["email"].casefold() == wrong_email for u in db["users"].values()):
        raise ValueError("Unknown email collides with source")
    corrected["messages"].insert(1, wrong_email)
    corrected["expected_tool_names"].insert(0, "identify_customer")
    corrected["expected"]["response_requirements"].append(
        "Reject the unknown email, accept the correction, and retain the order ID.")

    audit = {"release": manifest["release"], "commit": manifest["commit"], "task_set": task_set,
        "native_source_tasks": len(tasks), "native_end_to_end_tasks_evaluated": 0,
        "source_tasks_with_reference_writes": writes, "source_tasks_with_reference_handoffs": handoffs,
        "source_tasks_linked_to_status_probes": len(tasks) - len(no_order),
        "source_tasks_without_a_valid_referenced_order": no_order,
        "unique_order_status_probes": len(probes), "synthetic_controls": 3,
        "total_probes": len(probes) + 3,
        "source_order_status_counts": dict(Counter(p["expected_source_status"] for p in probes)),
        "unavailable_reference_ids_not_used_as_positive_probes": invalid,
        "reference_operation_counts": dict(sorted(operations.items())),
        "score_scope": "Adapted status/identity/ownership probes only. No native tau task reward or leaderboard comparison."}
    return probes + [foreign, unknown, corrected], status_fixture(db), audit


def source_policy():
    return ("This evaluation is a rewritten read-only order-status probe, not the original action task. "
            "Only email identification, authorized status lookup and generic denial are in scope. "
            "Do not require returns, edits, payment or exchanges to complete this probe. "
            "The snapshot supplies no delivery dates or ETAs; never invent them.\n\n"
            + (SOURCE / "policy.md").read_text())
