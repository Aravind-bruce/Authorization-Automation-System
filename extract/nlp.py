"""
IPAA v2 — Universal NLP Extraction Engine
Extracts ALL medical entities from any clinical document using:
  1. spaCy NER
  2. Medical pattern matching (regex)
  3. Curated clinical lexicons
  4. Lab value & vitals extraction
  5. Dosage & medication parsing
No document type assumed — works on any medical text.
"""

import re
import logging
from typing import Dict, List

logger = logging.getLogger("extract")

# ─── Regex Patterns ───────────────────────────────────────────────────────────

ICD_PATTERN = re.compile(r"\b([A-TV-Z][0-9][0-9AB]\.?[0-9A-TV-Z]{0,4})\b")
CPT_PATTERN = re.compile(r"\b(\d{5}[A-Z0-9]?)\b")

LAB_PATTERNS = [
    (r"(?:hemoglobin|hgb|hb)\s*[:\-=]?\s*([\d.]+)\s*(?:g/dl|g/l)?", "hemoglobin"),
    (r"(?:hba1c|a1c|glycated\s*hemoglobin)\s*[:\-=]?\s*([\d.]+)\s*%?", "hba1c"),
    (r"(?:blood\s*glucose|fasting\s*glucose|glucose)\s*[:\-=]?\s*([\d.]+)\s*(?:mg/dl|mmol)?", "glucose"),
    (r"(?:creatinine)\s*[:\-=]?\s*([\d.]+)\s*(?:mg/dl)?", "creatinine"),
    (r"(?:egfr|gfr)\s*[:\-=]?\s*([\d.]+)", "egfr"),
    (r"(?:potassium|k\+?)\s*[:\-=]?\s*([\d.]+)\s*(?:meq/l|mmol)?", "potassium"),
    (r"(?:sodium|na\+?)\s*[:\-=]?\s*([\d.]+)\s*(?:meq/l|mmol)?", "sodium"),
    (r"(?:wbc|white\s*blood\s*cell)\s*[:\-=]?\s*([\d.]+)", "wbc"),
    (r"(?:platelet|plt)\s*[:\-=]?\s*([\d.]+)", "platelets"),
    (r"(?:cholesterol|ldl|hdl|triglycerides?)\s*[:\-=]?\s*([\d.]+)\s*(?:mg/dl)?", "cholesterol"),
    (r"(?:psa)\s*[:\-=]?\s*([\d.]+)", "psa"),
    (r"(?:inr|pt/inr)\s*[:\-=]?\s*([\d.]+)", "inr"),
    (r"(?:tsh|thyroid)\s*[:\-=]?\s*([\d.]+)", "tsh"),
    (r"(?:ast|alt|alp|bilirubin)\s*[:\-=]?\s*([\d.]+)", "liver_enzymes"),
]

VITALS_PATTERNS = [
    (r"(?:blood\s*pressure|bp)\s*[:\-=]?\s*(\d{2,3}/\d{2,3})", "blood_pressure"),
    (r"(?:heart\s*rate|hr|pulse)\s*[:\-=]?\s*(\d{2,3})\s*(?:bpm)?", "heart_rate"),
    (r"(?:temperature|temp)\s*[:\-=]?\s*([\d.]+)\s*(?:°?[fc])?", "temperature"),
    (r"(?:respiratory\s*rate|rr)\s*[:\-=]?\s*(\d{1,3})\s*(?:/min)?", "respiratory_rate"),
    (r"(?:oxygen\s*saturation|spo2|o2\s*sat)\s*[:\-=]?\s*(\d{2,3})\s*%?", "spo2"),
    (r"(?:height)\s*[:\-=]?\s*([\d.]+)\s*(?:cm|ft|in|m)?", "height"),
    (r"(?:weight)\s*[:\-=]?\s*([\d.]+)\s*(?:kg|lbs?|pounds?)?", "weight"),
    (r"(?:bmi)\s*[:\-=]?\s*([\d.]+)", "bmi"),
]

DOSAGE_PATTERN = re.compile(
    r"(\b\w+(?:\s+\w+)?)\s+"          # drug name
    r"(\d+(?:\.\d+)?)\s*"             # dose number
    r"(mg|mcg|g|ml|units?|iu|%)\s*"   # unit
    r"(?:(once|twice|thrice|\d+\s*times?)\s*(?:daily|a\s*day|per\s*day))?"  # frequency
    r"(?:\s+for\s+(\d+)\s*(?:days?|weeks?|months?))?",  # duration
    re.IGNORECASE
)

ALLERGY_PATTERN = re.compile(
    r"(?:allergic?\s+to|allerg(?:y|ies)|nkda|nkfa|no\s+known\s+(?:drug\s+)?allerg|"
    r"adverse\s+reaction\s+to)\s*[:\-]?\s*([^.\n]{3,80})",
    re.IGNORECASE
)

PATIENT_PATTERNS = [
    (r"(?:patient\s*name|name)\s*[:\-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", "name"),
    (r"(?:age|aged?)\s*[:\-]?\s*(\d{1,3})\s*(?:years?|yrs?|y\.?o\.?)?", "age"),
    (r"(?:dob|date\s+of\s+birth|born)\s*[:\-]?\s*([\d]{1,2}[\/\-\.][\d]{1,2}[\/\-\.][\d]{2,4})", "dob"),
    (r"(?:gender|sex)\s*[:\-]?\s*(male|female|m\b|f\b|non-binary|other)", "gender"),
    (r"(?:mrn|medical\s*record|patient\s*id)\s*[:\-]?\s*([A-Z0-9\-]{4,20})", "mrn"),
    (r"(?:insurance\s*id|member\s*id|policy\s*(?:number|no|#))\s*[:\-]?\s*([A-Z0-9\-]{4,25})", "insurance_id"),
    (r"(?:physician|doctor|dr\.?|provider|attending)\s*[:\-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", "physician"),
    (r"(?:npi)\s*[:\-]?\s*(\d{10})", "npi"),
    (r"(?:address|addr)\s*[:\-]?\s*([^\n]{5,80})", "address"),
    (r"(?:phone|tel|contact)\s*[:\-]?\s*([\d\-\(\)\+\s]{7,20})", "phone"),
    (r"(?:diagnosis\s*date|date\s+of\s+diagnosis)\s*[:\-]?\s*([\d\/\-\.]+)", "diagnosis_date"),
    (r"(?:date\s+of\s+service|service\s+date|dos)\s*[:\-]?\s*([\d\/\-\.]+)", "service_date"),
]

# ─── Broad clinical terminology lists (detection triggers) ────────────────────
CLINICAL_SECTION_MARKERS = [
    "chief complaint", "history of present illness", "past medical history",
    "physical examination", "assessment", "plan", "impression",
    "diagnosis", "clinical indication", "reason for request",
    "relevant history", "review of systems", "medications", "allergies",
    "social history", "family history", "laboratory", "radiology",
    "pathology", "procedure", "treatment plan", "discharge summary",
]

JUSTIFICATION_KEYWORDS = [
    "because", "indicated for", "due to", "prescribed", "recommended",
    "necessary", "required", "justify", "clinical notes", "physician notes",
    "doctor notes", "medically necessary", "clinical indication",
    "evidence-based", "guideline", "standard of care", "first-line",
    "second-line", "failed", "intolerant", "inadequate response",
    "contraindicated", "monitor", "follow up",
]


def extract_entities(text: str) -> Dict:
    """
    Universal medical entity extractor.
    Works on any clinical document — no disease list assumptions.
    """
    if not text or len(text.strip()) < 5:
        return _empty_result()

    logger.info(f"[NLP] Analyzing {len(text)} characters")

    result = {
        "diseases": _extract_with_nlp_and_context(text, "disease"),
        "treatments": _extract_with_nlp_and_context(text, "treatment"),
        "medications": _extract_medications(text),
        "procedures": _extract_procedures(text),
        "patient_info": _extract_patient_info(text),
        "icd_codes": _extract_icd_codes(text),
        "cpt_codes": _extract_cpt_codes(text),
        "lab_values": _extract_lab_values(text),
        "vitals": _extract_vitals(text),
        "allergies": _extract_allergies(text),
        "dosage_info": _extract_dosages(text),
        "clinical_sections": _detect_sections(text),
        "has_justification": _has_justification(text),
        "document_type": _classify_document(text),
        "extraction_method": "universal_nlp_v2",
    }
    result["entity_count"] = (
        len(result["diseases"]) + len(result["treatments"]) +
        len(result["medications"]) + len(result["procedures"])
    )

    logger.info(
        f"[NLP] Entities: {len(result['diseases'])} dx, {len(result['treatments'])} tx, "
        f"{len(result['medications'])} meds, {len(result['procedures'])} procs, "
        f"{len(result['icd_codes'])} ICD, {len(result['lab_values'])} labs"
    )
    return result


def _extract_with_nlp_and_context(text: str, entity_type: str) -> List[str]:
    """
    Extract disease/condition or treatment entities using:
    1. spaCy NER (primary)
    2. Context-based sentence scanning (fallback)
    """
    results = []

    # spaCy pass
    try:
        import spacy
        nlp = spacy.load("en_core_web_sm")
        doc = nlp(text[:100000])

        disease_labels = {"DISEASE", "CONDITION"}
        treatment_labels = {"PRODUCT", "CHEMICAL", "DRUG", "WORK_OF_ART"}

        for ent in doc.ents:
            token = ent.text.strip()
            if len(token) < 3 or token.isdigit():
                continue
            if entity_type == "disease" and ent.label_ in disease_labels:
                results.append(token.title())
            elif entity_type == "treatment" and ent.label_ in treatment_labels:
                results.append(token.title())

    except Exception:
        pass

    # Context scan: extract noun phrases near clinical keywords
    if entity_type == "disease":
        context_triggers = [
            r"(?:diagnosis|diagnosed\s+with|suffering\s+from|history\s+of|"
            r"presents?\s+with|complaint\s+of|condition[:\s]+|impression[:\s]+|"
            r"assessment[:\s]+|problem[:\s]+|disorder|syndrome|disease|"
            r"disorder|ailment|condition|pathology)\s*[:\-]?\s*([^.\n,]{3,60})",
        ]
    else:
        context_triggers = [
            r"(?:prescribed|administered|treatment[:\s]+|therapy[:\s]+|"
            r"medication[:\s]+|drug[:\s]+|procedure[:\s]+|ordered|initiated|"
            r"started\s+on|placed\s+on|receiving|recommend(?:ed)?)\s*[:\-]?\s*([^.\n,]{3,60})",
        ]

    for pattern in context_triggers:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            candidate = match.group(1).strip().strip(".,;:")
            if 3 < len(candidate) < 80 and not candidate.isdigit():
                results.append(candidate.title())

    return _deduplicate(results)[:20]  # cap at 20 entities per type


def _extract_medications(text: str) -> List[str]:
    """Extract drug/medication names with dosages."""
    meds = []
    # Match "Drug Name Dose Unit" patterns
    for match in DOSAGE_PATTERN.finditer(text):
        drug = match.group(1).strip()
        dose = match.group(2)
        unit = match.group(3)
        if len(drug) > 2 and not drug[0].isdigit():
            meds.append(f"{drug.title()} {dose}{unit}")

    # Also capture lines that look like medication lists
    med_line = re.compile(
        r"^\s*[\d\.\-\*]?\s*([A-Za-z][a-zA-Z\s\-]+?)"
        r"\s+(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|units?|iu|%)"
        r"(?:\s+[\w\s,]+)?$",
        re.MULTILINE
    )
    for match in med_line.finditer(text):
        drug = match.group(1).strip().title()
        dose = match.group(2)
        unit = match.group(3)
        if 2 < len(drug) < 50:
            meds.append(f"{drug} {dose}{unit}")

    return _deduplicate(meds)[:15]


def _extract_procedures(text: str) -> List[str]:
    """Extract surgical/diagnostic procedures."""
    procedure_keywords = re.compile(
        r"\b((?:mri|ct\s*scan|x[\-\s]?ray|ultrasound|echocardiogram|ekg|ecg|"
        r"endoscopy|colonoscopy|biopsy|angiogram|angioplasty|stent|bypass|"
        r"laparoscopy|appendectomy|cholecystectomy|dialysis|catheterization|"
        r"bronchoscopy|cystoscopy|arthroscopy|mammogram|pet\s*scan|dexa|"
        r"spirometry|pulmonary\s*function|stress\s*test|holter|tilt\s*table|"
        r"lumbar\s*puncture|bone\s*marrow|transfusion|chemotherapy|radiation|"
        r"surgery|operation|resection|excision|repair|replacement|transplant|"
        r"rehabilitation|physiotherapy|occupational\s*therapy|speech\s*therapy|"
        r"cognitive\s*behavioral\s*therapy|infusion\s*therapy|phototherapy|"
        r"lithotripsy|ablation|cryotherapy|immunotherapy|gene\s*therapy)\b)",
        re.IGNORECASE
    )
    found = [m.group(1).strip().title() for m in procedure_keywords.finditer(text)]
    return _deduplicate(found)


def _extract_patient_info(text: str) -> Dict:
    info = {}
    for pattern, key in PATIENT_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            info[key] = match.group(1).strip()
    return info


def _extract_icd_codes(text: str) -> List[str]:
    return _deduplicate(ICD_PATTERN.findall(text))


def _extract_cpt_codes(text: str) -> List[str]:
    matches = CPT_PATTERN.findall(text)
    return _deduplicate([m for m in matches if m.isdigit() and 10000 <= int(m) <= 99999])


def _extract_lab_values(text: str) -> Dict:
    labs = {}
    for pattern, name in LAB_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            labs[name] = match.group(1).strip()
    return labs


def _extract_vitals(text: str) -> Dict:
    vitals = {}
    for pattern, name in VITALS_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            vitals[name] = match.group(1).strip()
    return vitals


def _extract_allergies(text: str) -> List[str]:
    matches = ALLERGY_PATTERN.findall(text)
    if matches:
        return _deduplicate([m.strip().title() for m in matches if len(m.strip()) > 2])
    if re.search(r"nkda|no\s*known\s*(?:drug\s*)?allerg", text, re.IGNORECASE):
        return ["NKDA (No Known Drug Allergies)"]
    return []


def _extract_dosages(text: str) -> str:
    dosages = []
    for match in DOSAGE_PATTERN.finditer(text):
        drug = match.group(1).strip()
        if len(drug) > 2 and not drug[0].isdigit():
            parts = [drug.title(), match.group(2), match.group(3)]
            if match.group(4):
                parts.append(match.group(4))
            if match.group(5):
                parts.append(f"for {match.group(5)} days")
            dosages.append(" ".join(parts))
    return ", ".join(_deduplicate(dosages)[:10])


def _detect_sections(text: str) -> List[str]:
    text_lower = text.lower()
    return [s.title() for s in CLINICAL_SECTION_MARKERS if s in text_lower]


def _has_justification(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in JUSTIFICATION_KEYWORDS)


def _classify_document(text: str) -> str:
    text_lower = text.lower()
    if any(w in text_lower for w in ["prescription", "rx", "prescribed", "dispense"]):
        return "Prescription"
    if any(w in text_lower for w in ["laboratory", "lab result", "blood test", "urine", "culture"]):
        return "Lab Report"
    if any(w in text_lower for w in ["radiology", "imaging", "mri", "ct scan", "x-ray", "ultrasound"]):
        return "Radiology Report"
    if any(w in text_lower for w in ["discharge summary", "discharge", "admitted", "hospitalized"]):
        return "Discharge Summary"
    if any(w in text_lower for w in ["referral", "referred to", "specialist"]):
        return "Referral Letter"
    if any(w in text_lower for w in ["clinic note", "progress note", "soap note", "office visit"]):
        return "Clinical Note"
    if any(w in text_lower for w in ["prior authorization", "insurance", "coverage", "benefit"]):
        return "Insurance Form"
    if any(w in text_lower for w in ["operative", "surgery", "procedure note", "operative report"]):
        return "Operative Report"
    return "Medical Document"


def _deduplicate(lst: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in lst:
        key = item.lower().strip()
        if key not in seen and len(key) > 1:
            seen.add(key)
            result.append(item)
    return result


def _empty_result() -> Dict:
    return {
        "diseases": [], "treatments": [], "medications": [], "procedures": [],
        "patient_info": {}, "icd_codes": [], "cpt_codes": [],
        "lab_values": {}, "vitals": {}, "allergies": [],
        "dosage_info": "", "clinical_sections": [],
        "has_justification": False, "document_type": "Unknown",
        "entity_count": 0, "extraction_method": "none",
    }
