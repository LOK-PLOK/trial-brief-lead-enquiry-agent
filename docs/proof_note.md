# Proof Note

**Candidate:** Paul France M. Detablan  
**Brief:** WCC-TRIAL-A  
**Limit:** 400 words

## What I built

A two-role lead-enquiry agent with an independent verifier gate: Planner (structured `Plan`) → deterministic Executor (four tools via `ToolRegistry`) → independent Verifier (separate prompt/call) → one-shot Repair Loop → persist. OpenRouter supplies the LLM through a provider-agnostic `ModelAdapter`. FastAPI serves `/api/*` and the built React SPA from one container. SQLite stores runs, LLM usage, leads, and harness batches. An offline evaluation harness runs 15×3=45 identical `Pipeline.run()` calls and writes JSON/Markdown reports; three adversarial enquiries run separately. Assessors can also start harness jobs from the **Harness Testing** UI.

## What I measured

Harness metrics from persisted rows: completion/error/quarantine rates, fabrication rate (verifier-reported), tool-selection accuracy vs plan, repair success rate, mean/median/p95 latency, mean tokens and cost, and run-to-run variance by `enquiry_id`. Schema-breach rates and true fabrication-caught rate are reported as `None` with honesty notes where the contracts cannot observe first-attempt adapter retries or ground-truth fabrications without extending immutable interfaces.

## Live URL check

After deploy to Render (single Docker Web Service, health `/api/health`, no login):

1. Open the public URL on a phone hotspot (or other network not used to develop).
2. Confirm the SPA loads and `GET /api/health` returns `{"status":"ok"}`.
3. Submit a short fictional enquiry on the Run tab; confirm plan, tool trace, record, verifier decision, and cost panels populate.
4. Note the URL, timestamp, and network used here before submission.

| Field | Value |
| --- | --- |
| **Public URL** | _(fill after Render deploy)_ |
| **Checked from** | _(device / network)_ |
| **Checked at** | _(UTC timestamp)_ |
