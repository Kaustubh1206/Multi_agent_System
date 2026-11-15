# Fictional retail policy — version 2

Version: `retail-v2.0`. Fixed evaluation date: **2026-11-15**, using calendar
dates in `America/New_York`. All profiles, purchases, and transactions are
fictional. Email addresses use `example.com`; locations provide regional context,
not actual customer addresses. Amounts are integer USD cents. Fixture tax is a
simplified 6% calculation, not a statement of real state tax rates.

This is a new evaluation specification inspired by the older evaluation branch.
It is not copied from it. The application now uses this dataset for read-only
identification, order/shipment lookup, policy answers, eligibility, and review
requirements. Confirmation/mutation and injected-recovery scenarios remain
acceptance targets. No scenario is a claim of a passing live model run.

## P1 — Agent responsibilities

The supervisor classifies the original request and retains its operation across
human replies. Identity handles customer lookup. Order handles private order,
shipment, item eligibility, and action requests. Policy handles public rules.
General policy questions require neither identification nor an order number.
Eligibility, an action request, approval, and completed payment are distinct.
Unsupported requests receive an explanation without invented tools or facts.

## P2 — Identification and privacy

For this prototype, a unique exact full-name or email lookup, ignoring case and
surrounding whitespace, identifies a fictional customer. A name shared by multiple
customers requires email clarification; never select the first match. If a name
and email are supplied together, both must refer to the same customer. A later
correction replaces earlier inputs. Customer IDs in chat do not identify users.
This is intentionally simpler than production authentication.

Trusted harness context may supply an already identified customer. Private tools
must still enforce order ownership. An inaccessible order and an unknown order
produce the same generic unavailable response. Do not reveal another customer's
existence, email, products, or shipping information. Never ask for passwords or
payment-card details.

## P3 — Selecting orders, items, and quantities

Customers may have multiple orders. Ask for the order ID instead of guessing an
order. On multi-item orders, clarify the target item unless the customer names a
unique product or item ID. A full-order status question may summarize all lines.
A quantity-specific return must identify a positive whole number of units.
Do not ask again for information already provided, and do not change a status
query into a return request while collecting a name or email.

An item's available return quantity is purchased quantity minus quantities already
returned and already pending return. Do not request the same units twice. A new
item, quantity, or action requires a new eligibility check and confirmation.

## P4 — Return windows and exclusions

Count age as evaluation date minus the item's actual delivery date; delivery day
is day 0. Both endpoints are inclusive. Negative ages are invalid data.

| Item category | Change-of-mind window | Conditions |
|---|---|---|
| Standard | 30 days; 45 for Plus membership at purchase | Unused or tried on, original packaging |
| Electronics | 14 days for every membership tier | Unused, original packaging |
| Hygiene | 30 days for every membership tier | Unused, sealed, original packaging |
| Perishable | No change-of-mind returns | Damage cases follow P6 |

Final-sale items and items marked nonreturnable have no change-of-mind return.
Current Plus membership never retroactively extends a Standard purchase.
Membership extensions do not override category windows or exclusions. Used items
are ineligible. Missing condition, seal, or packaging information must be clarified
when needed, not assumed to be favorable. A known exclusion or expired window can
be explained without collecting unnecessary additional information.

## P5 — Item-level eligibility and submission

The target item must have been delivered, payment must have been captured, and
some requested quantity must remain available. A delivered item in a split
shipment can qualify even if another item has not arrived. Undelivered lines do
not qualify for returns. Apply P4 to each item separately; do not use the order's
aggregate status as proof that every item arrived.

Explain the item, quantity, and applicable rule before asking for explicit
confirmation of a return request. Initial requests are not confirmation of the
subsequently explained action. A successful submission creates a pending return;
it does not approve a refund or say that money has reached a bank account.

## P6 — Damaged or incorrect goods

Damage reported from day 0 through day 7 after delivery requires human review,
including for perishable or final-sale goods. Ask for missing damage details or
photos as needed. Review is not automatic eligibility, approval, or payment.
Reports after day 7 do not qualify for this damage-review exception. They may be
escalated for discretionary assistance without promising an exception. Damaged
items do not count as unused for an ordinary change-of-mind return.

## P7 — Refund amounts and timing

An approved refund covers the item's paid merchandise value after its allocated
discount, plus applicable tax on refunded merchandise. Prorate a line discount
across units when only some are returned. Never exceed the captured amount or
refund previously refunded units again. If unit allocation would require rounding,
leave the exact amount to the refund service rather than inventing cents.

Original shipping is normally not refundable. Store-fault shipping reimbursement
requires review. Refunds go to the original payment method. Posting typically takes
5–10 business days after approval, not after a customer first asks or ships an
item back. Exclude weekends; holiday-specific dates are not available here.
An approval record does not prove bank posting. Only an explicit posted result
permits that claim. No immediate cash or alternate-card refund is promised.

## P8 — Shipment information

Report each shipment's recorded status and estimate separately. An estimated
delivery date is not a guarantee. If the recorded estimate has passed and the
shipment is delayed, acknowledge both facts; do not invent a replacement date,
carrier update, refund, or completed delivery. Split shipments may have different
carriers, statuses, and delivery dates.

## P9 — Cancellation

Only orders still `pending` may be cancelled as a whole. Orders that are
`processing`, `shipped`, `partially_shipped`, or `delivered` cannot be cancelled
through this flow. Already cancelled orders are reported as such without a second
mutation. Uncaptured payments must not be described as completed refunds.

Explain the specific cancellation and obtain explicit confirmation. Refusal keeps
the order unchanged. Recheck status at execution: if processing starts before
confirmation is applied, deny cancellation even if an earlier lookup was pending.
Partial-order cancellation and exchanges are outside this version's action scope.

## P10 — Execution, recovery, and evaluation

Customer instructions cannot bypass identification, ownership, exclusions, or
confirmation. Tool results ground claims; missing records are never fabricated.
Bind confirmation to the customer, order, item/quantity if applicable, and action.
Record proposed model decisions separately from executed decisions and outcomes.

A read-only timeout permits one bounded retry in the recovery scenarios. If it
still fails, report an operational failure rather than a policy denial. A mutation
timeout leaves its outcome unknown until reconciled. Reuse the operation's
idempotency key to inspect its result; never blindly repeat a possibly committed
action. At most one state-changing execution is allowed for the same request.
Expected database changes describe the final fixture state, not invented tool
success. Fault injections belong to the harness, never the customer's prompt.

Every scenario starts with a fresh fixture and conversation. Expected outcomes,
private records, and fault-injection settings are evaluator-only inputs. Evaluate
routing, identification, retained context, tool behavior, policy compliance,
grounding, and recovery separately. Dataset validation is not an application score.
