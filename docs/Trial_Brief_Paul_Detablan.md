# Paid Technical Trial: Briefing Document

**Candidate:** Paul France M. Detablan
**Role under consideration:** AI Systems Engineer
**Issued by:** Alexander Knight, Whisky Cask Club
**Date issued:** 30 July 2026
**Duration:** Three working days
**Brief reference:** WCC-TRIAL-A
**Subject:** Multi-agent orchestration with independent verification

---

## 1. Purpose

This is a paid technical trial, not an offer of employment. It exists so that both of us can decide on evidence rather than on interview impressions. You will be paid in full on delivery regardless of the outcome.

The role is an AI systems role. Accordingly this trial is not a web development exercise. Conventional application work is stripped to the minimum needed to display results. Effectively all of the difficulty sits in the agent layer: planning, tool use, verification, self-correction and measurement.

The brief is deliberately larger than three days comfortably allows. We are not measuring whether you finish everything. We are measuring how you prioritise, whether the system you describe actually runs, and whether you report your own results honestly.

---



## 2. Commercial terms


| Item                    | Detail                                                       |
| ----------------------- | ------------------------------------------------------------ |
| Day rate                | PHP 1,800                                                    |
| Total fee               | PHP 5,400 for three days                                     |
| Payment trigger         | On delivery, irrespective of assessment outcome              |
| Model API reimbursement | Up to PHP 500 on production of a receipt or usage screenshot |
| Start                   | Monday                                                       |
| Delivery deadline       | End of day Wednesday                                         |


No access will be given to any Whisky Cask Club system, account, credential or dataset. You deploy to your own hosting and use your own model API access. Free and trial tiers are expected and entirely acceptable. Small open models run locally are equally acceptable if you prefer.

---



## 3. What you are building

A two-role agent system with an independent verifier gate, plus an evaluation harness that measures it.

The architecture mirrors how we run production internally, where one agent plans and governs and a second executes locally. Build that pattern at small scale.

### 3.1 The agent's task

Given a single block of unstructured enquiry text, the system must produce a verified, structured lead record. I will supply 15 enquiry samples on the Monday morning.

### 3.2 Planner

An LLM call that receives the enquiry and the tool manifest, and returns an explicit ordered plan: which tools to call, in which order, with what arguments. The plan itself is structured output and must validate against a schema.

The planner does not execute anything.

### 3.3 Executor

Carries out the plan by calling tools. Must use real tool calling or function calling, not string parsing of model prose.

Minimum tool set, all of which you implement:


| Tool                       | Behaviour                                                                                         |
| -------------------------- | ------------------------------------------------------------------------------------------------- |
| `parse_enquiry`            | LLM extraction of name, email, phone, country, budget band, asset interest, urgency               |
| `lookup_jurisdiction_rule` | Deterministic lookup against a small local rules file, returns the handling rule for that country |
| `score_lead`               | Deterministic scoring function over the extracted fields                                          |
| `write_record`             | Persists the record, rejecting duplicates on a normalised hash of email and phone                 |


The executor must be capable of reporting a tool failure back rather than continuing blindly.

### 3.4 Independent verifier

A separate LLM call, with its own prompt, receiving the original enquiry text, the plan, the tool call trace and the final record. It returns a pass or fail, a confidence figure, and a reason on failure.

The verifier must catch two distinct failure classes:

1. **Fabrication.** Any field value not present in or reasonably inferable from the source text.
2. **Plan deviation.** The executor skipped, reordered or substituted a tool relative to the plan.

This must be genuinely independent. Not the same prompt, not the same request, not a self-check appended to extraction. The separation is the substance of the exercise.

### 3.5 Repair loop

On verifier failure, the system attempts one repair pass, then routes to quarantine with the reason. Never silently drop a submission. Demonstrate the repair loop successfully correcting at least one case.

### 3.6 Evaluation harness

This is the deliverable that carries the most weight.

Run the full system across all 15 enquiries, three times each, for 45 runs. Report:

- end to end task completion rate
- schema breach rate at the planner and at the extractor
- fabrication rate, and the proportion the verifier caught
- tool selection accuracy against the plan
- repair loop success rate
- mean tokens and mean cost per run
- mean latency per run
- run to run variance on identical input

Non-determinism is expected. Quantifying it is the point.

### 3.7 Adversarial testing

Write three enquiries of your own designed to break the system. At least one must attempt prompt injection embedded in the free text, for example instructions telling the executor to skip the jurisdiction lookup or to record the enquirer in the highest budget band.

Run them against the finished system. Report what happened, including anything that succeeded.

### 3.8 Display

One page, functional rather than designed. It must show, for any given run: the plan, the tool call trace, the final record, the verifier decision and reason, and the token cost. Plus a summary view of the harness results from 3.6.

---



## 4. Constraints

- Stack, models and framework are entirely your choice. Justify the choices in one paragraph.
- Frameworks and libraries are permitted, but you must be able to explain what the framework is doing.
- Fictional data only. No real personal data.
- The system must be reachable at a public URL. Localhost is not a delivery.

---



## 5. Deliverables

Four items, by end of day Wednesday.

1. **Live public URL.** Working, reachable, no login required.
2. **Repository link.** Public, or shared with the address provided.
3. **Proof note.** Maximum 400 words. What you built, what you measured, and how you confirmed the live URL works from a device and network other than your own.
4. **Failure log.** What broke. What you could not finish. What you would do with another week.

An empty failure log will be read as a failure to test, not as a clean run. On a brief of this scope in three days there will be gaps. Name them.

---



## 6. Assessment

Three hard gates, applied before anything else is scored.


| Gate                                                               | Result       |
| ------------------------------------------------------------------ | ------------ |
| Does the URL work when opened cold, from an unfamiliar network?    | Pass or fail |
| Does the verifier catch a fabrication we deliberately induce?      | Pass or fail |
| Do the harness numbers reproduce when we run the system ourselves? | Pass or fail |


Failure at any gate fails the trial, regardless of the quality of the rest.


| Scored criterion                                          | Out of |
| --------------------------------------------------------- | ------ |
| Evaluation harness rigour and honesty of reported figures | 5      |
| Verifier genuinely independent and functionally effective | 5      |
| Proof note accurately matches what exists                 | 5      |
| Failure log honest and specific                           | 5      |
| Agent loop works end to end                               | 5      |
| Handling of the adversarial cases                         | 3      |
| Questions asked before building rather than after         | 3      |
| Code legibility and structure                             | 3      |
| Delivered on time without being chased                    | 3      |


Note the weighting. A system with modest capability and rigorous, honest measurement scores above an impressive demonstration with no measurement behind it.

---



## 7. Working expectations

Ask questions at any point, by WhatsApp, and ask them early. Clarifying a requirement before building counts in your favour. Building the wrong thing quietly does not.

If you conclude partway through that a requirement is wrong or unachievable in the time, say so and propose an alternative. That is a better outcome than a silent partial build.

Thank you.

**Alexander Knight**
Founder and CEO, Whisky Cask Club