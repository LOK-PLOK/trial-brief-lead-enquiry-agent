# Manual Testing Checklist (index)

Use the Postman + Swagger walkthrough (updated for **post-verifier** `write_record`):

→ **[`docs/manual_testing.md`](manual_testing.md)**

That guide covers:

- Correct pipeline: Planner → parse → lookup → score → Verifier → Repair → **write_record (after pass)** → persist run  
- Database reset (`app.db`)  
- Postman / Swagger setup with full request bodies and expected responses  
- Ten realistic enquiries (plus E11–E14 budget/urgency format checks) + regression suite  
- What to expect from `/api/leads`, `/api/runs`, `/api/harness/summary`  
- Trial Brief tick-list for hard gates and scored criteria  
