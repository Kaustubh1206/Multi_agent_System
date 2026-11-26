# Validation performed in the cloud

- Shared root environment installed successfully with `uv sync --frozen --extra thinkingbox`.
- All **507** canonical tasks hydrated; expected five-domain counts matched;
  every selected native test script compiled.
- Full regression suite: **81 tests passed**. The 12 ThinkingBox tests include
  native agent tool execution/history, bounded argument repair, native test
  subprocesses for all five domains, both real DeepEval metric implementations
  with a controlled judge, and honest reporting of blocked runs/judge errors.
- The affected 12-test suite passed again after the final runner/service changes.
- Ruff checks and `git diff --check` passed; `uv lock --check` passed.
- Separate component smoke: real source MCP servers initialized in all five
  domains; executing each first task's reference writes produced the expected
  native database hash. **Retrieval was disabled only in that component smoke**
  because the retrieval-model download is blocked here. This verifies backend
  wiring, not MAS behavior or benchmark performance. Production task initialization
  retains the original retrieval configuration.

**No live MAS/DeepEval benchmark scores were produced.** The full default command
selected all 507 tasks but preflight found no running Ollama server. It recorded
507 blocked cases and zero scored metrics, without inventing results.

The cloud network also denied `dl.typesense.org` and `models.typesense.org`.
For the service/component checks only, Typesense 30.1 was obtained from the
official `typesense/typesense:30.1` container image (digest
`sha256:91604dc128e2023e7cba34ae8bd36f68351bcb74ea33a84960bfdc5649cf43b3`).
The actual unmodified source session initialization then exposed the blocked
`ts/e5-small` model download. Downloads are required for a complete local run.

macOS downloads/platform selection follow the pinned upstream installer, but
macOS execution and real local model quality were not tested on this Linux cloud.
Run the commands in [README.md](README.md) on the local Ollama machine to obtain
the complete 507-task results.
