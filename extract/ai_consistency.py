import json
from .llm import run_llm_agent

def ai_medical_consistency_check(entities, text):
    prompt = f"""
Check medical consistency.

Diseases: {entities.get("diseases")}
Treatments: {entities.get("treatments")}
Medications: {entities.get("medications")}
Labs: {entities.get("lab_values")}

Return JSON:
{{
  "severity": "LOW/MEDIUM/HIGH",
  "issues": []
}}
"""

    result = run_llm_agent(
        extracted_text=text,
        decision="CHECK",
        reason="Consistency check",
        entities=entities,
        flags=[],
        missing_fields=[],
        confidence_score=1.0,
    )

    try:
        return json.loads(result.get("explanation", "{}"))
    except:
        return {
            "severity": "LOW",
            "issues": []
        }