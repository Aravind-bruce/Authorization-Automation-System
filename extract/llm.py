"""
IPAA v2 — Groq LLM Agent
Generates clinical explanations, improvement suggestions,
appeal letters, and executive clinical summaries via Groq LLaMA-3.
"""

import logging
import time
from typing import Dict
from django.conf import settings

logger = logging.getLogger("extract")

SYSTEM_PROMPT = """You are IPAA (Intelligent Prior Authorization AI Agent), an expert AI clinical decision-support system deployed within a major healthcare insurance organization.

Your expertise spans:
- Clinical medicine and evidence-based guidelines (AHA, ADA, NCCN, CHEST, etc.)
- Insurance prior authorization policies (Medicare, Medicaid, commercial payers)
- CMS coverage determinations and LCD/NCD policies
- Healthcare documentation standards (SOAP notes, ICD-10, CPT)
- HIPAA-compliant communication

Your outputs are used by:
- Insurance claims processors making coverage decisions
- Physicians preparing appeals and resubmissions
- Hospital administrators reviewing authorization workflows

Always be:
- Medically precise and evidence-referenced
- Professionally worded (suitable for clinical and legal review)
- Empathetic to patient needs while respecting policy constraints
- Structured and actionable

You are NOT the final decision-maker — you provide intelligent decision-support."""


def run_llm_agent(
    extracted_text: str,
    decision: str,
    reason: str,
    entities: Dict,
    flags: list,
    missing_fields: list,
    confidence_score: float,
) -> Dict:
    logger.info(f"[LLM] Invoking Groq | Decision={decision}")
    start = time.time()

    try:
        client = _get_groq_client()
        if client is None:
            return _fallback_response(decision, reason, missing_fields)

        diseases_str = ", ".join(entities.get("diseases", [])) or "Not identified"
        treatments_str = ", ".join(entities.get("treatments", [])) or "Not identified"
        meds_str = ", ".join(entities.get("medications", [])) or "None detected"
        procs_str = ", ".join(entities.get("procedures", [])) or "None detected"
        icd_str = ", ".join(entities.get("icd_codes", [])) or "None"
        cpt_str = ", ".join(entities.get("cpt_codes", [])) or "None"
        doc_type = entities.get("document_type", "Medical Document")
        completeness = entities.get("completeness_score", 0)

        lab_str = ""
        if entities.get("lab_values"):
            lab_str = " | ".join(f"{k}: {v}" for k, v in entities["lab_values"].items())

        vitals_str = ""
        if entities.get("vitals"):
            vitals_str = " | ".join(f"{k}: {v}" for k, v in entities["vitals"].items())

        allergies_str = ", ".join(entities.get("allergies", [])) or "None documented"

        flags_str = "\n".join(
            f"  [{f['severity']}] {f['code']}: {f['message']}" for f in flags
        ) or "  None"
        missing_str = "\n".join(f"  - {m}" for m in missing_fields) or "  None"

        prompt = f"""
PRIOR AUTHORIZATION CASE — FULL CLINICAL ANALYSIS
===================================================

DOCUMENT METADATA:
  Document Type: {doc_type}
  Document Completeness Score: {completeness}%

EXTRACTED CLINICAL DATA:
  Diagnoses / Conditions: {diseases_str}
  Treatments Requested: {treatments_str}
  Medications (with dosage): {meds_str}
  Procedures: {procs_str}
  ICD-10 Codes: {icd_str}
  CPT Codes: {cpt_str}
  Laboratory Values: {lab_str or "Not documented"}
  Vital Signs: {vitals_str or "Not documented"}
  Allergies: {allergies_str}
  Clinical Sections Present: {', '.join(entities.get('clinical_sections', [])) or 'Standard'}

AUTHORIZATION DECISION: {decision}
APPROVAL CONFIDENCE: {round(confidence_score * 100, 1)}%

POLICY COMPLIANCE FLAGS:
{flags_str}

MISSING DOCUMENTATION:
{missing_str}

RULE ENGINE SUMMARY:
{reason}

CLINICAL TEXT EXCERPT (first 2000 chars):
{extracted_text[:2000]}

===================================================
TASK: Provide a structured clinical analysis in EXACTLY this format with these exact section headers:

## DECISION EXPLANATION
[3-5 sentences. Explain the authorization decision with specific reference to: (1) what clinical evidence was found, (2) which policy rules were triggered, (3) what the confidence score means. Use professional clinical language. Reference relevant clinical guidelines if applicable.]

## CLINICAL SUMMARY
[2-3 sentences. Provide a concise clinical summary of the patient's case based on what was extracted — as if you were a clinical reviewer summarizing the case for a committee. Include key diagnoses, current medications, and treatment being requested.]

## WHAT'S MISSING / HOW TO IMPROVE
[Numbered list of 4-6 specific, actionable items. For each missing item, explain: (a) what it is, (b) why it's required per policy, and (c) exactly how to provide it. Be specific — don't say "add more information", say "Include HbA1c lab result from the past 3 months showing >8.0% to justify insulin intensification."]

## APPEAL LETTER
[If REJECT or INSUFFICIENT: Write a complete formal appeal letter (200-280 words) formatted as:
  Date: [DATE]
  From: [PHYSICIAN NAME, CREDENTIALS]
  [HOSPITAL/PRACTICE NAME AND ADDRESS]
  
  To: Prior Authorization Review Committee
  [INSURANCE COMPANY NAME]
  
  Re: Appeal for Prior Authorization — [PATIENT MRN] — [CONDITION/TREATMENT]
  
  Dear Authorization Review Committee,
  
  [3-4 paragraphs: (1) state the appeal, (2) clinical justification with evidence, (3) cite relevant clinical guidelines, (4) conclude with urgency if applicable]
  
  Sincerely,
  [PHYSICIAN SIGNATURE BLOCK]

If APPROVE: Write "N/A — Authorization approved. No appeal required."]

Be clinically precise, professionally authoritative, and actionable.
"""

        response = client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=settings.GROQ_MAX_TOKENS,
            temperature=settings.GROQ_TEMPERATURE,
        )

        raw = response.choices[0].message.content.strip()
        parsed = _parse_output(raw)
        elapsed = round((time.time() - start) * 1000)
        logger.info(f"[LLM] Done in {elapsed}ms | tokens={response.usage.total_tokens}")
        return parsed

    except Exception as exc:
        logger.error(f"[LLM] Groq failed: {exc}", exc_info=True)
        return _fallback_response(decision, reason, missing_fields)


def _get_groq_client():
    try:
        from groq import Groq
        key = settings.GROQ_API_KEY
        if not key:
            logger.error("[LLM] GROQ_API_KEY not set.")
            return None
        return Groq(api_key=key)
    except ImportError:
        logger.error("[LLM] groq not installed.")
        return None


def _parse_output(text: str) -> Dict:
    headers = {
        "## DECISION EXPLANATION": "explanation",
        "## CLINICAL SUMMARY": "clinical_summary",
        "## WHAT'S MISSING / HOW TO IMPROVE": "suggestions",
        "## APPEAL LETTER": "appeal_letter",
    }
    buffers = {k: [] for k in headers.values()}
    current = None

    for line in text.split("\n"):
        matched = False
        for header, key in headers.items():
            if line.strip().startswith(header):
                current = key
                matched = True
                break
        if not matched and current:
            buffers[current].append(line)

    result = {k: "\n".join(v).strip() for k, v in buffers.items()}
    if not result["explanation"]:
        result["explanation"] = text[:800]

    appeal = result.get("appeal_letter", "")
    if "n/a" in appeal.lower() and "approved" in appeal.lower():
        result["appeal_letter"] = ""

    return result


def _fallback_response(decision: str, reason: str, missing_fields: list) -> Dict:
    logger.warning("[LLM] Using rule-based fallback.")

    explanation = (
        f"Decision: {decision}. {reason} "
        "(AI explanation unavailable — rule-based assessment only.)"
    )
    suggestions = ""
    if missing_fields:
        steps = "\n".join(f"{i+1}. {m}" for i, m in enumerate(missing_fields))
        suggestions = f"Required documentation:\n{steps}"
    else:
        suggestions = "All required fields present."

    appeal = ""
    if decision in ("REJECT", "INSUFFICIENT"):
        appeal = (
            "Date: [DATE]\n\n"
            "To: Prior Authorization Review Committee\n\n"
            "Re: Appeal for Prior Authorization\n\n"
            "Dear Review Committee,\n\n"
            f"I am writing to formally appeal the denial of prior authorization. "
            f"The clinical documentation submitted supports medical necessity. "
            f"Issues cited: {reason}\n\n"
            "Please reconsider based on the attached updated clinical records.\n\n"
            "Respectfully,\n[Physician Name, MD]\n[Hospital / Practice]"
        )
    return {
        "explanation": explanation,
        "clinical_summary": "",
        "suggestions": suggestions,
        "appeal_letter": appeal,
    }
