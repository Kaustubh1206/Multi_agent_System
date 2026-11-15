"""Read-only v2 policy decisions. No returns, refunds, or cancellations are executed."""
from datetime import date


def return_decision(order, item_id, quantity, today, condition=None, packaging=None, seal=None):
    def outcome(kind, reason, message, question=None, **details):
        return {"outcome": kind, "reason_code": reason, "explanation": message,
                "question": question, "eligible": kind == "eligible", **details}

    items = order["items"]
    if not item_id and len(items) > 1:
        choices = "; ".join(f"{i['item_id']} ({i['product_name']})" for i in items)
        question = f"Which item would you like to return? {choices}"
        return outcome("needs_information", "item_required", question, question)
    item = next((i for i in items if i["item_id"] == item_id), None) if item_id else items[0]
    if not item:
        return outcome("ineligible", "item_unavailable", "That item is unavailable in this order.")
    if not item["delivered_on"] or item["fulfillment_status"] != "delivered":
        return outcome("ineligible", "item_not_delivered", "This item has not been delivered, so it cannot be returned yet.")
    if order["payment_status"] != "paid":
        return outcome("ineligible", "payment_not_captured", "The order has no captured payment available for this return.")
    available = item["quantity"] - item["returned_quantity"] - item["pending_return_quantity"]
    if available <= 0:
        return outcome("ineligible", "return_already_requested", "All units are already returned or pending return; no duplicate return can be requested.")
    if quantity is None and available > 1:
        question = f"How many units of {item['item_id']} would you like to return? {available} are available."
        return outcome("needs_information", "quantity_required", question, question)
    quantity = 1 if quantity is None else quantity
    if quantity < 1 or quantity > available:
        return outcome("ineligible", "invalid_return_quantity", f"Choose between 1 and {available} available units.")
    age = (today - date.fromisoformat(item["delivered_on"])).days
    if age < 0:
        raise ValueError("An item delivery date is in the future")
    # A damage claim may require review; it never converts directly to approval.
    actual_condition = (condition if item["condition"] == "unknown" or condition in {"damaged", "used", "tried_on"}
                        else item["condition"]) or item["condition"]
    if actual_condition == "damaged":
        reported = date.fromisoformat(item["damage_reported_on"]) if item["damage_reported_on"] else today
        report_age = (reported - date.fromisoformat(item["delivered_on"])).days
        if 0 <= report_age <= 7:
            return outcome("needs_review", "damage_review_required",
                           f"Damage was reported {report_age} days after delivery, within the 7-day review window. "
                           "This requires human review, including for perishable or final-sale goods. "
                           "No review case has been submitted and no return or refund has been approved.",
                           days_since_delivery=age, policy_rules=["P6"])
        return outcome("ineligible", "damage_report_window_expired",
                       "The 7-day damage-review exception has expired. Discretionary assistance may be requested, but no exception or refund is promised.")
    if item["final_sale"] or not item["returnable"] or item["category"] == "perishable":
        reason = "final_sale" if item["final_sale"] else "item_not_returnable"
        return outcome("ineligible", reason, "This item is excluded from change-of-mind returns.")
    window = {"standard": 45 if order["membership_at_purchase"] == "plus" else 30,
              "electronics": 14, "hygiene": 30}[item["category"]]
    if age > window:
        return outcome("ineligible", "return_window_expired",
                       f"This item was delivered {age} days ago, outside its {window}-day return window.")
    actual_packaging = False if packaging is False else (packaging if item["original_packaging"] is None else item["original_packaging"])
    actual_seal = "opened" if seal == "opened" else (seal if item["seal"] == "unknown" else item["seal"])
    if actual_condition == "unknown" or actual_packaging is None or (item["category"] == "hygiene" and actual_seal == "unknown"):
        question = "Is the item unused or only tried on, with its original packaging? For hygiene items, is it still sealed?"
        return outcome("needs_information", "item_condition_required", question, question)
    if item["category"] == "hygiene" and actual_seal != "sealed":
        return outcome("ineligible", "opened_hygiene_item", "Opened hygiene items cannot be returned for change of mind.")
    acceptable = {"unused", "tried_on"} if item["category"] == "standard" else {"unused"}
    if actual_condition not in acceptable or not actual_packaging:
        return outcome("ineligible", "item_condition_ineligible", "The item's condition or packaging does not meet the return policy.")
    value = item["line_total_cents"] * quantity
    details = {"item_id": item["item_id"], "quantity": quantity, "days_since_delivery": age,
               "window_days": window, "policy_rules": ["P4", "P5"]}
    if value % item["quantity"] == 0:
        details["merchandise_value_cents"] = value // item["quantity"]
    value_note = ""
    if "merchandise_value_cents" in details:
        cents = details["merchandise_value_cents"]
        value_note = f" Paid merchandise value: USD {cents // 100}.{cents % 100:02d} before applicable tax; this is not an approved refund."
    return outcome("eligible", "return_eligible",
                   f"{quantity} unit(s) of {item['item_id']} are eligible: delivered {age} days ago, "
                   f"within the {window}-day window. Explicit confirmation is required before submission. "
                   "No return has been requested or refund issued." + value_note, **details)
