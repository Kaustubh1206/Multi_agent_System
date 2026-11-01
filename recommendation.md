| Field | Purpose |
|---|---|
| `messages` | Customer and assistant conversation history, using LangGraph’s message reducer. |
| `tasks` | Structured records of the customer’s requests. |
| `active_task_id` | Identifies the task currently being processed when execution is sequential. |
| `pending_question` | The clarification being asked, linked to a task and its missing information. |
| `workflow_status` | Overall progress, such as `processing`, `awaiting_customer`, `completed`, or `needs_human`. |


-----



| Field | Purpose |
|---|---|
| `task_id` | Stable identifier for this request across turns. |
| `intent` | A controlled category such as `order_status`, `return_request`, `refund_request`, or `product_question`. |
| `request_summary` | Brief description preserving what the customer actually wants. |
| `order_id` | Relevant order identifier, initially absent if unknown. |
| `item_ids` | Relevant items, when identified. |
| `status` | Task lifecycle: `pending`, `awaiting_customer`, `ready`, `in_progress`, `completed`, `unsupported`, or `failed`. |
| `missing_fields` | Information required for the **next action**. |
| `result` | Structured outcome from the specialized agent, including any customer-facing explanation. |


