"""One prompt module per agent stage.

Each of planner_prompt.py, verifier_prompt.py, parse_enquiry_prompt.py, and
repair_prompt.py must remain self-contained: no shared prompt text between
the planner/extractor and the verifier. That separation is what makes the
verifier genuinely independent (docs/architecture.md section 9).
"""
