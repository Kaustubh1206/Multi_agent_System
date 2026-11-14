"""Validate synthetic evaluation assets, without running or scoring the MAS."""

import json
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TOOLS = {"get_policy", "get_product", "get_order", "cancel_order", "request_return"}
MUTATIONS = {"cancel_order", "request_return"}
ROUTES = {None, "identity", "order", "policy", "customer_input_node"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def keyed(records, field):
    result = {record[field]: record for record in records}
    require(len(result) == len(records), f"Duplicate {field}")
    return result


def main():
    fixtures = json.loads((ROOT / "fixtures.json").read_text())
    dataset = json.loads((ROOT / "scenarios.json").read_text())
    today = date.fromisoformat(fixtures["evaluation_date"])
    require(dataset["policy_version"] == fixtures["policy_version"], "Policy version mismatch")
    require(fixtures["policy_version"] in (ROOT / "retail_policy.md").read_text(), "Policy document version mismatch")
    customers = keyed(fixtures["customers"], "user_id")
    products = keyed(fixtures["products"], "product_id")
    orders = keyed(fixtures["orders"], "order_id")

    for order in orders.values():
        require(order["user_id"] in customers, "Order has unknown owner")
        keyed(order["items"], "item_id")
        for item in order["items"]:
            require(item["product_id"] in products, "Order item has unknown product")
        if order["status"] == "delivered":
            require(date.fromisoformat(order["delivered_on"]) <= today, "Future delivery in fixture")

    scenarios = dataset["scenarios"]
    keyed(scenarios, "id")
    require(10 <= len(scenarios) <= 15, "Expected 10–15 development scenarios")
    kinds = set()

    for scenario in scenarios:
        sid = scenario["id"]
        case = scenario["case"]
        kind = case["kind"]
        kinds.add(kind)
        verified = scenario["context"]["verified_user_id"]
        require(verified is None or verified in customers, f"{sid}: invalid session fixture")
        require(set(scenario["policy_rules"]) <= {f"P{i}" for i in range(1, 8)}, f"{sid}: unknown policy rule")
        forbidden = set(scenario["forbidden_tools"])
        require(forbidden <= TOOLS, f"{sid}: unknown forbidden tool")
        order = orders.get(case.get("order_id"))
        mutation_calls = []
        steps = scenario["checkpoints"]
        require(bool(steps), f"{sid}: missing conversation")

        for index, step in enumerate(steps):
            require(step["expected_route"] in ROUTES, f"{sid}: unknown route")
            require(bool(step["customer_message"]), f"{sid}: empty customer message")
            require(bool(step["response_requirements"] or step["clarification_requirements"]), f"{sid}: no behavioral oracle")
            if step["expected_outcome"] == "clarify":
                require(step["expected_route"] == "customer_input_node", f"{sid}: clarification route mismatch")
                require(bool(step["clarification_requirements"]), f"{sid}: unspecified clarification")
                require(not step["required_tools"], f"{sid}: unexpected tool before clarification")

            for call in step["required_tools"]:
                name, args = call["name"], call["arguments"]
                require(name in TOOLS and name not in forbidden, f"{sid}: invalid/forbidden required tool")
                if name in {"get_order", *MUTATIONS}:
                    require(verified is not None, f"{sid}: private access without a verified session")
                    require(args.get("order_id") == case.get("order_id"), f"{sid}: inconsistent order reference")
                if name == "get_product":
                    require(args.get("product_id") in products, f"{sid}: product missing from fixtures")
                if name == "get_policy":
                    require(args.get("topic") in {"returns", "refunds", "cancellations"}, f"{sid}: unknown policy topic")
                if name in MUTATIONS:
                    require(index > 0 and steps[index - 1]["expected_outcome"] == "confirm", f"{sid}: mutation without prior confirmation checkpoint")
                    require(order is not None and order["user_id"] == verified, f"{sid}: unauthorized mutation")
                    mutation_calls.append(name)

        if kind == "cancel_denied":
            require(order is not None and order["status"] != "pending", f"{sid}: cancellation denial contradicts fixture")
        if kind in {"cancel_confirmed", "cancel_declined"}:
            require(order is not None and order["status"] == "pending", f"{sid}: not cancellable")
        if kind in {"return_eligible", "return_expired"}:
            require(order is not None and order["status"] == "delivered", f"{sid}: invalid return order")
            item = next((item for item in order["items"] if item["item_id"] == case["item_id"]), None)
            require(item is not None, f"{sid}: return item missing")
            age = (today - date.fromisoformat(order["delivered_on"])).days
            eligible = 0 <= age <= 30 and item["returnable"] and item["return_status"] == "none"
            require(eligible == (kind == "return_eligible"), f"{sid}: return oracle contradicts fixture/date")
            if kind == "return_expired":
                require(age > 30, f"{sid}: not actually expired")
        if kind == "ownership_denied":
            require(order is not None and order["user_id"] != verified, f"{sid}: ownership denial contradicts fixture")
        if kind == "unknown_order":
            require(order is None, f"{sid}: supposedly unknown order exists")
        if kind == "unverified":
            require(verified is None, f"{sid}: supposedly unverified session is verified")
        if kind == "unsupported":
            require(all(s["expected_route"] is None and not s["required_tools"] for s in steps), f"{sid}: unsupported request dispatched")

        changes = scenario["expected_db_changes"]
        require(bool(changes) == bool(mutation_calls), f"{sid}: mutation calls and database oracle disagree")
        for change in changes:
            require(change["order_id"] == case["order_id"], f"{sid}: mutation targets wrong order")
            if "cancel_order" in mutation_calls:
                require(change == {"order_id": case["order_id"], "field": "status", "from": "pending", "to": "cancelled"}, f"{sid}: invalid cancellation oracle")
            else:
                require(change == {"order_id": case["order_id"], "item_id": case["item_id"], "field": "return_status", "from": "none", "to": "requested"}, f"{sid}: invalid return oracle")

    require({"status", "missing_order", "unverified", "public_policy", "public_product", "cancel_confirmed", "cancel_declined", "cancel_denied", "return_eligible", "return_expired", "ownership_denied", "unknown_order", "unsupported"} <= kinds, "Required scenario coverage missing")
    print(f"PASS: {len(scenarios)} synthetic scenarios; references, policy eligibility, confirmation sequence, ownership, and database expectations are consistent.")
    print("MAS execution, model accuracy, and benchmark scores: NOT RUN.")


if __name__ == "__main__":
    main()
