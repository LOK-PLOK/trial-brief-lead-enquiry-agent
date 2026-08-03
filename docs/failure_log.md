# Failure Log

**Candidate:** Paul France M. Detablan  
**Brief:** WCC-TRIAL-A

This project was completed within a limited timeframe, so several issues were encountered during development and testing. They are listed below to show what was discovered, what was fixed, and what still remains to be improved.

---

# What Broke or Was Difficult

## 1. The initial project was only a scaffold

The project started with many placeholder components that could not actually process enquiries. Before the system could work end-to-end, the API, tools, and LLM integration all had to be completed.


## 2. Database configuration prevented the application from starting

An incorrect database configuration caused the backend to fail during startup. This was resolved by providing a proper default SQLite configuration.


## 3. The API could not process enquiries initially

Before connecting the LLM provider, submitting an enquiry resulted in server errors because the backend had no working AI model to process requests.


## 4. Failed enquiries were not being repaired

Originally, any enquiry that failed verification was immediately quarantined. The workflow was updated so the system attempts one repair before deciding whether to quarantine the run.


## 5. Records were saved before verification

The application initially stored records before they had been verified. This sometimes created duplicate or incorrect records. The workflow was changed so data is only saved after verification succeeds.


## 6. The verifier sometimes rejected correct information

The verifier occasionally marked valid information as fabricated, even when it came from trusted system tools. Additional validation rules were added to distinguish between extracted user information and system-generated values.


## 7. The verifier occasionally contradicted itself

In a few cases, the verifier accepted information in its explanation while also flagging the same information as fabricated. Extra validation was added to reduce these inconsistent decisions.


## 8. No official evaluation dataset was available

The trial brief mentioned that enquiry samples would be supplied, but they were not available during development. To continue testing, fictional sample enquiries were created for the evaluation harness.


## 9. Budget and urgency were sometimes interpreted incorrectly

Some enquiries produced incorrect budget bands or urgency levels because of ambiguous wording. The extraction prompts were improved with clearer instructions and additional examples.


## 10. Country detection can still be improved

When an enquiry only mentions a city instead of a country, the system may not always identify the correct jurisdiction. This is an area that could be further improved.


## 11. Large evaluation runs required additional OpenRouter credits

Running the complete evaluation harness consumes many LLM requests. During testing, the available OpenRouter credits were exhausted, so additional credits were required to complete the evaluation.

---

# What I Could Not Finish

1. Create a manually labelled dataset to accurately measure how often the verifier detects fabricated information.

2. Record validation failures that occur before the system automatically retries a request.

3. Perform larger production evaluations on every deployment without relying on additional OpenRouter credits.

4. Improve location detection for enquiries that mention only cities or regions.

---

# What I Would Do With Another Week

1. Build a labelled evaluation dataset to measure verifier accuracy more precisely.

2. Improve location and jurisdiction detection for more ambiguous enquiries.

3. Add persistent storage for the deployed SQLite database instead of relying on temporary storage.

4. Run larger production evaluations and publish additional performance reports.

5. Expand testing with more real-world and edge-case enquiries to further improve system reliability.