# Simple fictional retail policy

Version: `simple-retail-v1`.

Use the fixed evaluation date **2026-11-14** with this dataset. All customers and orders are fictional; prices are in USD.

- General policy questions need no customer or order ID.
- Ask for an order number when an order-specific request lacks one. For this fictional prototype, a unique full-name or email lookup identifies the customer. A customer ID alone is not enough. Only disclose an order belonging to the identified customer.
- Report the recorded order status; never invent shipment or delivery details.
- A returnable item is eligible for return from delivery day through day 30, inclusive. Undelivered items and items delivered more than 30 days ago are not eligible.
- Explain eligibility and obtain explicit customer confirmation before requesting a return. A request is not an approved or completed refund.
- Pending orders may be cancelled after explicit confirmation. Shipped or delivered orders cannot be cancelled.
- Refunds are processed within 5–10 business days after the store receives and accepts the return. Never claim a refund or cancellation succeeded without a successful system result.

The current application reads orders and checks eligibility only. Return submission, cancellation, and refund payments are not implemented. Name/email lookup is a prototype identification rule, not production authentication.
