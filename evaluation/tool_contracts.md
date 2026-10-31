# Proposed retail tool contracts

These contracts describe tools to implement later. No live backend or authentication is added by these files.

## Shared conventions

- Public arguments are model-supplied; `verified_user_id`, evaluation date, and recorded confirmation are trusted runtime context injected by the application.
- Never accept a model-generated `verified=true` or `confirmed=true` as authorization. The application records confirmation from the conversation and binds it to the target/action.
- Reads return `{ "ok": true, "data": ... }`. Failures return `{ "ok": false, "error": "CODE" }` and do not expose private records.
- Customer-specific calls without a verified session fail with `AUTH_REQUIRED`. Unknown and non-owned orders both fail with `ORDER_UNAVAILABLE`.
- Mutations are atomic, recheck policy at execution time, and use idempotency keys to prevent duplicate side effects on retries. Replaying the same key/request returns the original result; key reuse for a different request fails.
- Only return fields needed for the operation. Do not expose other customers, full payment details, or unrelated profile information.

| Tool | Model-supplied input | Trusted context / preconditions | Successful result |
|---|---|---|---|
| `get_policy` | `topic`: `returns`, `refunds`, or `cancellations` | Public; policy version fixed | Applicable policy text and version |
| `get_product` | `product_id` | Public; known product | ID, name, description, price, currency, availability |
| `get_order` | `order_id` | Verified session; ownership | Order ID/status, delivery date, shipment status, item IDs, quantities, return eligibility/status |
| `cancel_order` | `order_id`, `reason`, `idempotency_key` | Verified owner; status `pending`; recorded confirmation for this cancellation | Order ID and status `cancelled` |
| `request_return` | `order_id`, `item_id`, `reason`, `idempotency_key` | Verified owner; P5 eligibility; recorded confirmation for this return | Return ID, item ID, return status `requested` |

Additional errors: `POLICY_NOT_FOUND`, `PRODUCT_NOT_FOUND`, `ORDER_NOT_CANCELLABLE`, `ITEM_NOT_FOUND`, `RETURN_INELIGIBLE`, `CONFIRMATION_REQUIRED`, and `IDEMPOTENCY_CONFLICT`.

For the initial fixture, each item line has quantity one; partial-quantity returns are out of scope. A return request never calls a refund tool. There is deliberately no account verification tool: real authentication must be integrated through the application rather than invented as an LLM decision.

## Example interaction

1. Customer asks to cancel `ORD-1001` in a verified `USR-001` session.
2. Order agent calls `get_order(order_id="ORD-1001")` and observes `pending`.
3. Agent explains cancellation and asks for confirmation; no mutation occurs yet.
4. Customer explicitly confirms. The application records confirmation bound to this order/action/session.
5. Agent calls `cancel_order` with a unique idempotency key. The tool rechecks ownership/status/confirmation, changes the status, and returns success.
6. Only then does the agent tell the customer the order was cancelled.
