1. order_id: str | None -> Deliberately kept None . As in some user will not provide OrderID intially

2. item_is: List -> Because some customer might request refunds for multiple items


3. Here's how the fields work:
- messages keeps the conversation history. add_messages allows new messages to be merged into the existing history.
- tasks stores the tasks extracted by the Supervisor.
- missing_fields records what information is needed next.
- supervisor_status tells the workflow whether the Supervisor needs to ask a question, has enough information for the next stage, or cannot support the request.