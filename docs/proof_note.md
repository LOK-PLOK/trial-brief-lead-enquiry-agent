# Proof Note

**Candidate:** Paul France M. Detablan  
**Brief:** WCC-TRIAL-A

## What I Built

During this exercise, I built a lead enquiry processing system that combines LLM reasoning with deterministic business logic. Users can submit a natural language enquiry through a React frontend, which is processed by a FastAPI backend. A Planner first decides which tools should be used, after which the Executor extracts structured information, applies jurisdiction rules, calculates a lead score, and only stores the result after it has been independently verified.

To improve reliability, the system includes a separate Verifier that checks whether the extracted information is actually supported by the original enquiry instead of simply trusting the previous LLM output. If verification fails, a single repair attempt is made before the run is quarantined. Every run records the execution plan, tool trace, verifier decision, latency, token usage, and cost for later inspection.

## What I Measured

I also implemented an evaluation harness that runs the same production pipeline against predefined datasets, adversarial samples, or custom enquiries. The harness measures completion and quarantine rates, repair outcomes, latency, token usage, cost, verifier results, and other execution statistics. These metrics are stored and displayed through the Harness Testing dashboard to make testing repeatable and transparent.

## Live URL Verification

The application was deployed publicly on Render as separate frontend and backend services:

- **Frontend:** https://trial-brief-lead-enquiry-agent-client.onrender.com
- **Backend:** https://trial-brief-lead-enquiry-agent.onrender.com

To confirm the deployment worked outside my development environment, I accessed the application using my mobile phone over a cellular data connection rather than my home network. The frontend loaded successfully, the backend health endpoint responded correctly, and I completed a full end-to-end test by submitting a fictional enquiry and confirming that the execution plan, tool trace, verifier decision, and final record were generated correctly.