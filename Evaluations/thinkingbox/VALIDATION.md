# Retail-only validation performed in the cloud

- The default suite selects exactly **98 unique canonical retail tasks** from the
  pinned 507-task release. Audit confirmed only `sandbox_external_retail`, and
  every selected native test script compiled.
- `run.py --list` and both standalone DeepEval metric scripts list 98 tasks.
- Full regression suite: **84 tests passed**, including **15 ThinkingBox tests**.
  Checks cover rejection of other domains through the CLI/task loader/backend
  validation, native retail end-state assertion failures, conversation/tool
  history, bounded schema repair, both actual DeepEval metric implementations
  with a controlled judge, and blocked-run/judge-error reporting.
- Ruff checks and `git diff --check` passed.
- Only the retail MCP server is configured. The MAS prompts and result manifests
  identify the retail scope under protocol `thinkingbox-retail-mas-v2`.

**No live MAS/DeepEval benchmark scores were produced.** The default command
selected 98 retail tasks but preflight found no running Ollama server. The saved
report records 98 blocked cases and zero scored metrics, without inventing results.

An earlier retail backend component smoke initialized the real source MCP server;
executing its first task's reference writes produced the expected native database
hash. Retrieval was disabled only in that component smoke because the retrieval
model download is blocked here. This verifies backend wiring, not MAS behavior or
benchmark performance. Production tasks retain their source retrieval setup.

The cloud network also denied `dl.typesense.org` and `models.typesense.org`.
For the service/component checks only, Typesense 30.1 was obtained from the
official `typesense/typesense:30.1` container image (digest
`sha256:91604dc128e2023e7cba34ae8bd36f68351bcb74ea33a84960bfdc5649cf43b3`).
The actual unmodified source session initialization then exposed the blocked
`ts/e5-small` model download. Downloads are required for a complete local run.

macOS downloads/platform selection follow the pinned upstream installer, but
macOS execution and real local model quality were not tested on this Linux cloud.
Run the commands in [README.md](README.md) on the local Ollama machine to obtain
the complete 98-task retail results.
