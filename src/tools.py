"""Small read-only tools. File paths do not depend on the shell's working directory."""
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from langchain_core.tools import tool


SIMPLE_DATA = Path(__file__).resolve().parents[1] / "evaluation" / "simple-retail"
DATA = SIMPLE_DATA.parent / "retail-v2"
_customer_data = ContextVar("customer_data", default=None)


@contextmanager
def customer_dataset(data):
    """Temporarily supply a read-only fixture; graph worker contexts inherit it."""
    token = _customer_data.set(data)
    try:
        yield
    finally:
        _customer_data.reset(token)


def load_customers():
    if (data := _customer_data.get()) is not None:
        return data
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
        if customer["customer_id"] == customer_id:
            orders = customer.get("orders", [customer.get("order")])
            for order in orders:
                if order and order["order_id"] == order_id:
                    return {"status": "found", "order": order}
    # Unknown order and another customer's order have the same public response.
    return {"status": "unavailable"}


@tool
def get_policy(topic: str) -> dict:
    """Read the short versioned policy document for an allowed public topic."""
    if topic not in {"returns", "refunds", "cancellations", "shipping", "identification"}:
        return {"status": "unsupported"}
    version = load_customers().get("policy_version", "simple-retail-v1")
    text = (DATA / "policy.md").read_text()
    if f"`{version}`" not in text:
        raise ValueError("Policy document and dataset versions differ")
    if version == "retail-v2.0":
        rules = {"returns": ["P4", "P5", "P6"], "refunds": ["P7"], "cancellations": ["P9"],
                 "shipping": ["P8"], "identification": ["P2"]}[topic]
        sections = dict(re.findall(r"^## (P\d+) ([\s\S]*?)(?=^## |\Z)", text, re.M))
        return {"status": "found", "version": version, "topic": topic,
                "answer": "\n\n".join(rule + " " + sections[rule].strip() for rule in rules)}
    if topic not in {"returns", "refunds", "cancellations"}:
        return {"status": "unsupported"}
    # Policy lines have stable prefixes; missing rules are data errors, not guesses.
    prefix = {"returns": "- A returnable item", "refunds": "- Refunds are processed",
              "cancellations": "- Pending orders"}[topic]
    answer = next(line.removeprefix("- ") for line in text.splitlines() if line.startswith(prefix))
    return {"status": "found", "version": version, "topic": topic, "answer": answer}
