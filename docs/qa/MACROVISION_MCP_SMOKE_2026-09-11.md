# MacroVision MCP smoke test — 2026-09-11

## Scope

Tested the actual MacroVision MCP implementation using the backend project environment and its standalone stdio transport. No mock MCP server was used.

## Results

- MCP module import: PASS.
- Tool registration: PASS — 7 tools advertised:
  `macrovision_list_food_photos`, `macrovision_predict_combined`,
  `macrovision_predict_regression`, `macrovision_estimate_depth`,
  `usda_get_food_details`, `usda_search_food`, and `usda_list_foods`.
- Stdio initialization and tool discovery: PASS.
- Explicit user-scope authorization: PASS. A scoped photo-list call returned the expected empty-library response for the test UUID.
- FastAPI mount registration: PASS — `/mcp/macrovision` is present in the production app route table.

## Suspicious observation

Launching with `python -m app.ai.tools.macrovision.server` emits:

`RuntimeWarning: 'app.ai.tools.macrovision.server' found in sys.modules after import of package 'app.ai.tools.macrovision'`

The call still succeeds, so this is non-blocking, but it indicates the package initializer imports the server before Python executes it as a module. It is worth cleaning up because it can produce unpredictable behavior in some module-entrypoint scenarios.

## Not tested in this smoke run

The mounted HTTP endpoint was not exercised end-to-end because no backend server was running on the expected local port at the time of the test. Real model prediction and depth inference also require an uploaded image and loaded model weights.

## Frontend environment follow-up

An attempted live frontend-to-agent test was blocked by the disposable QA configuration before MCP authorization could be exercised:

- The live and deterministic Vite QA frontends now run at `http://127.0.0.1:54173`, matching the origin allowed by `qa_env.py`.
- `serve_qa.py --live` leaves `MACROVISION_TOOLS_ENABLED=false`, so the live QA backend does not expose MacroVision tools to the agent.
- The live frontend proxy sends API/MCP traffic to port `58001`, while `qa_env.py` sets `INTERNAL_MCP_BASE_URL` to port `58000`.

This is a real test-environment configuration issue and is the most likely explanation for an authorization/availability report from the frontend QA agent. The application’s default `mprocs.yaml` values use the normal frontend/backend ports and are not covered by this disposable live-QA mismatch.
