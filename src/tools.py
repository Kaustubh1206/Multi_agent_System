"""Small read-only tools. File paths do not depend on the shell's working directory."""
import json
from datetime import date
from pathlib import Path

from langchain_core.tools import tool


DATA = Path(__file__).resolve().parents[1] / "evaluation" / "simple-retail"
POLICY_VERSION = "simple-retail-v1"


def load_customers():
    return json.loads((DATA / "customers.json").read_text())


@tool
def identify_customer(name: str | None = None, email: str | None = None) -> dict:
    """Find a unique fictional customer by exact full name or email, ignoring case."""
    name = (name or "").strip().casefold()
    email = (email or "").strip().casefold()
    if not name and not email:
        return {"status": "missing_information"}
    matches = [c for c in load_customers()["customers"]
               if (not name or c["name"].casefold() == name)
               and (not email or c["email"].casefold() == email)]
    if not matches:
        return {"status": "not_found"}
    if len(matches) != 1:
        return {"status": "ambiguous"}
    customer = matches[0]
    return {"status": "matched", "customer_id": customer["customer_id"]}


@tool
def get_order(customer_id: str, order_id: str) -> dict:
    """Read only an order belonging to the identified customer; otherwise return unavailable."""
    for customer in load_customers()["customers"]:
        if customer["customer_id"] == customer_id and customer["order"]["order_id"] == order_id:
            return {"status": "found", "order": customer["order"]}
    # Unknown order and another customer's order have the same public response.
    return {"status": "unavailable"}


@tool
def get_policy(topic: str) -> dict:
    """Read the short versioned policy document for an allowed public topic."""
    if topic not in {"returns", "refunds", "cancellations"}:
        return {"status": "unsupported"}
    text = (DATA / "policy.md").read_text()
    if f"`{POLICY_VERSION}`" not in text:
        raise ValueError("Policy document and rule implementation versions differ")
    # Policy lines have stable prefixes; missing rules are data errors, not guesses.
    prefix = {"returns": "- A returnable item", "refunds": "- Refunds are processed",
              "cancellations": "- Pending orders"}[topic]
    answer = next(line.removeprefix("- ") for line in text.splitlines() if line.startswith(prefix))
    return {"status": "found", "version": POLICY_VERSION, "topic": topic, "answer": answer}


@tool
def check_order_eligibility(customer_id: str, order_id: str, topic: str) -> dict:
    """Check returns/cancellation rules against the dataset clock and an authorized order."""
    # Recheck authorization here instead of trusting a model-supplied order record.
    lookup = get_order.func(customer_id, order_id)
    if lookup["status"] != "found":
        return {"status": "unavailable"}
    order = lookup["order"]
    policy = get_policy.func(topic)
    if policy["status"] != "found":
        return {"status": "unsupported"}
    result = {"status": "checked", "policy_version": policy["version"], "rule": policy["answer"]}
    if topic == "returns":
        today = date.fromisoformat(load_customers()["evaluation_date"])
        age = ((today - date.fromisoformat(order["delivered_on"])).days
               if order["delivered_on"] else None)
        eligible = order["status"] == "delivered" and order["returnable"] and age is not None and 0 <= age <= 30
        result.update(eligible=eligible, days_since_delivery=age, evaluation_date=today.isoformat())
    elif topic == "cancellations":
        result.update(eligible=order["status"] == "pending")
    return result
