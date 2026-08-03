# Proof Note

**Candidate:** Paul France M. Detablan  
**Brief:** WCC-TRIAL-A

## What I built

I built a lead-enquiry agent that converts an unstructured customer enquiry into a verified structured lead. A React frontend sends the enquiry to a FastAPI backend, where an LLM Planner decides which tools should be executed and in what order. The Executor then runs those tools deterministically to extract key information from the enquiry, apply the appropriate jurisdiction rules, calculate a lead score, and prepare the final record. An independent Verifier uses a separate prompt and LLM call to check that the extracted information is supported by the original enquiry and that the execution followed the planned workflow.

If verification fails, the system performs one repair attempt before running verification again. Only verified leads are written to the database. If verification still fails, the enquiry is quarantined with an explanation instead of being silently discarded. Each run records the execution plan, tool trace, verifier decision, latency, token usage, and cost.

## What I measured

I implemented an evaluation harness that runs the same production pipeline against multiple datasets, including the Official Trial A samples (E01–E15), with three executions per enquiry (45 runs). The harness measures completion, quarantine and error rates, verifier pass rates, fabrication detection, repair attempts and successes, tool-selection accuracy, latency, token usage, cost, and run-to-run consistency. Results are available through the Harness Testing page and are also written as Markdown and JSON reports for later review.

## Live URL check

The application is deployed publicly on Render as two separate services with no login required:

- **Frontend:** https://trial-brief-lead-enquiry-agent-client.onrender.com
- **Backend:** https://trial-brief-lead-enquiry-agent.onrender.com

To confirm the deployment worked outside my development environment, I accessed the application from my mobile phone using a cellular data connection instead of my home network. The frontend loaded successfully, the backend health endpoint responded correctly, and I completed a full end-to-end test by submitting a fictional enquiry and confirming that the execution plan, tool trace, verifier decision, and final record were generated successfully.