"""
IPAA v3 — Enterprise Prior Authorization Rule Engine
=====================================================
30 real-world insurance rules modelled on:
  • CMS Medicare Prior Authorization requirements (42 CFR §410, §411)
  • NCQA UM accreditation standards
  • URAC Utilization Management standards
  • AMA Prior Authorization Reform principles
  • Commercial payer PA criteria (UnitedHealth, Aetna, Cigna, BCBS published LCD/NCD policies)
  • InterQual® and MCG™ clinical criteria frameworks (simulated)

Rule Categories:
  A — Clinical Documentation (A001–A008)
  B — Medical Necessity & Evidence (B001–B006)
  C — Step Therapy & Treatment History (C001–C004)
  D — Provider & Facility Credentialing (D001–D004)
  E — Administrative & Compliance (E001–E005)
  F — Fraud, Waste & Abuse Detection (F001–F003)
"""

import re
import logging
from typing import Dict, List

logger = logging.getLogger("extract")

# ─── Policy Knowledge Base ─────────────────────────────────────────────────────

STEP_THERAPY_REQUIRED = {
    "diabetes mellitus","type 2 diabetes","type 1 diabetes","insulin resistance",
    "hypertension","hyperlipidemia","coronary artery disease","heart failure",
    "atrial fibrillation","deep vein thrombosis","pulmonary embolism",
    "asthma","copd","chronic obstructive pulmonary disease",
    "rheumatoid arthritis","psoriatic arthritis","ankylosing spondylitis",
    "psoriasis","plaque psoriasis",
    "crohn disease","crohn's disease","ulcerative colitis","inflammatory bowel disease",
    "depression","major depressive disorder","anxiety disorder","generalized anxiety",
    "bipolar disorder","schizophrenia",
    "osteoporosis","migraine","chronic migraine","insomnia","gout",
    "overactive bladder","benign prostatic hyperplasia",
}

HIGH_COST_PROCEDURES = {
    "chemotherapy","immunotherapy","targeted therapy","car-t therapy","car t-cell",
    "proton beam therapy","stereotactic radiosurgery","cyberknife",
    "bariatric surgery","spinal fusion","joint replacement","hip replacement",
    "knee replacement","cardiac surgery","bypass surgery","valve replacement",
    "organ transplant","transplant","bone marrow transplant","stem cell transplant",
    "angioplasty","stent placement","ablation","deep brain stimulation",
    "spinal cord stimulator","vagal nerve stimulator",
    "pet scan","pet-ct","cardiac mri","fmri","cardiac catheterization",
    "gene therapy","dialysis","hyperbaric oxygen","photodynamic therapy",
    "infliximab","adalimumab","etanercept","biologics","biologic therapy",
    "ect","electroconvulsive therapy","tms","transcranial magnetic stimulation",
    "residential treatment","intensive outpatient",
}

SPECIALTY_DRUGS = {
    "infliximab","remicade","adalimumab","humira","etanercept","enbrel",
    "secukinumab","cosentyx","ustekinumab","stelara","dupilumab","dupixent",
    "vedolizumab","entyvio","tofacitinib","xeljanz","baricitinib","olumiant",
    "abatacept","orencia","rituximab","rituxan","tocilizumab","actemra",
    "golimumab","simponi","certolizumab","cimzia","ixekizumab","taltz",
    "guselkumab","tremfya","risankizumab","skyrizi","tildrakizumab",
    "apremilast","otezla","ozempic","wegovy","mounjaro","tirzepatide",
    "semaglutide","liraglutide","saxenda","jardiance","farxiga","invokana",
    "keytruda","pembrolizumab","opdivo","nivolumab","tecentriq","yervoy",
    "revlimid","lenalidomide","pomalyst","velcade","bortezomib",
}

SPECIALIST_MAP = {
    "cancer":["oncologist","oncology"],"carcinoma":["oncologist","oncology"],
    "lymphoma":["oncologist","hematologist"],"leukemia":["oncologist","hematologist"],
    "tumor":["oncologist","oncology"],
    "multiple sclerosis":["neurologist","neurology"],"parkinson":["neurologist","neurology"],
    "alzheimer":["neurologist","geriatrician"],"epilepsy":["neurologist","epileptologist"],
    "rheumatoid arthritis":["rheumatologist","rheumatology"],
    "psoriatic arthritis":["rheumatologist","dermatologist"],
    "lupus":["rheumatologist","rheumatology"],
    "crohn":["gastroenterologist","gastroenterology"],
    "ulcerative colitis":["gastroenterologist","gastroenterology"],
    "heart failure":["cardiologist","cardiology"],"transplant":["transplant surgeon"],
    "dialysis":["nephrologist","nephrology"],
    "chronic kidney disease":["nephrologist","nephrology"],
    "copd":["pulmonologist","pulmonology"],"asthma":["pulmonologist","allergist"],
    "schizophrenia":["psychiatrist","psychiatry"],"bipolar disorder":["psychiatrist","psychiatry"],
}

LAB_REQUIRED_FOR = {
    "diabetes mellitus":["hba1c","glucose","a1c"],
    "type 2 diabetes":["hba1c","glucose","a1c"],
    "hyperlipidemia":["cholesterol","ldl","triglycerides"],
    "chronic kidney disease":["creatinine","egfr","gfr"],
    "anemia":["hemoglobin","hgb","hematocrit"],
    "hypothyroidism":["tsh","thyroid"],"hyperthyroidism":["tsh","thyroid"],
    "heart failure":["bnp","echocardiogram","ejection fraction","ef"],
    "atrial fibrillation":["ekg","ecg","holter","echo"],
    "osteoporosis":["dexa","bone density","t-score"],
    "prostate cancer":["psa"],
    "coagulation disorder":["inr","pt","ptt","aptt"],
    "liver disease":["ast","alt","bilirubin","albumin","liver"],
}

REMS_DRUGS = {
    "isotretinoin","accutane","ipledge","clozapine","clozaril",
    "thalidomide","thalomid","lenalidomide","revlimid","pomalyst","pomalidomide",
    "fentanyl","opioid","oxycodone","oxycontin","methadone",
    "sodium oxybate","xyrem","esketamine","spravato","mifepristone",
}

CMS_EXCLUSIONS = {
    "cosmetic","aesthetic","beauty","hair removal","hair restoration",
    "tattoo removal","elective","routine physical","dental","acupuncture",
    "naturopathic","weight loss program","gym","fitness",
    "comfort measures only","custodial care","personal care",
}

QUANTITY_LIMIT_DRUGS = {
    "suboxone","buprenorphine","naloxone","narcan","testosterone",
    "growth hormone","somatropin","erythropoietin","epoetin","aranesp",
    "procrit","botox","botulinum toxin",
}

HIGH_COST_FACILITY_KEYWORDS = {
    "out of network","out-of-network","non-participating","non-par",
    "out of plan","non-contracted",
}

EMERGENCY_BYPASS_KEYWORDS = {
    "emergency","emergent","urgent","stat","life-threatening","life threatening",
    "acute","imminent","critical","icu","intensive care unit",
    "code blue","code stroke","code stemi","active hemorrhage",
    "septic shock","anaphylaxis","respiratory failure",
}

POSITIVE_EVIDENCE_KEYWORDS = {
    "evidence-based","evidence based","clinical guideline","practice guideline",
    "ada guideline","aha guideline","nccn guideline","chest guideline",
    "standard of care","first-line therapy","second-line therapy",
    "fda approved","fda-approved","clinical trial","peer reviewed",
    "level i evidence","level ii evidence","randomized controlled",
    "physician attestation","attending physician","board certified",
    "subspecialty","multidisciplinary","tumor board",
}

# ─── Main Evaluation ───────────────────────────────────────────────────────────

def evaluate(entities: Dict, extracted_text: str) -> Dict:
    logger.info("[RULES] Starting 30-rule enterprise evaluation.")

    flags: List[Dict] = []
    missing: List[str] = []
    positive_signals: List[str] = []
    risk_score: float = 0.0
    risk_reduction: float = 0.0

    diseases    = [d.lower() for d in entities.get("diseases", [])]
    treatments  = [t.lower() for t in entities.get("treatments", [])]
    medications = [m.lower() for m in entities.get("medications", [])]
    procedures  = [p.lower() for p in entities.get("procedures", [])]
    all_tx      = list(set(treatments + medications + procedures))
    icd_codes   = entities.get("icd_codes", [])
    cpt_codes   = entities.get("cpt_codes", [])
    lab_values  = entities.get("lab_values", {})
    vitals      = entities.get("vitals", {})
    patient_info = entities.get("patient_info", {})
    allergies   = entities.get("allergies", [])
    dosage_info = entities.get("dosage_info", "")
    clinical_sections = [s.lower() for s in entities.get("clinical_sections", [])]
    has_justification = entities.get("has_justification", False)

    text_lower = extracted_text.lower()
    text_len   = len(extracted_text.strip())
    is_emergency = any(kw in text_lower for kw in EMERGENCY_BYPASS_KEYWORDS)

    # ── CATEGORY A: CLINICAL DOCUMENTATION ────────────────────────────────

    # A001 — Primary Diagnosis / ICD-10 Code
    if not diseases and not icd_codes:
        flags.append(_flag("A001","CRITICAL","Clinical Documentation",
            "No primary diagnosis or ICD-10 code found.",
            "CMS 42 CFR §410.32; NCQA UM Standard 1",
            "Provide confirmed primary diagnosis with ICD-10-CM code "
            "(e.g., 'Type 2 Diabetes Mellitus — E11.9'). Must be documented by treating physician."))
        missing.append("Primary diagnosis + ICD-10-CM code (e.g., E11.9, I10, J45.50)")
        risk_score += 0.45
    else:
        if icd_codes:
            positive_signals.append(f"ICD-10 codes: {', '.join(icd_codes[:3])}")

    # A002 — Requested Service / Treatment / CPT Code
    if not all_tx and not cpt_codes:
        flags.append(_flag("A002","CRITICAL","Clinical Documentation",
            "No treatment, medication, or procedure identified.",
            "NCQA UM Standard 3; AMA PA Reform Principle 1",
            "Explicitly state the requested service — medication name + dosage OR "
            "procedure name + CPT code. Vague requests cannot be evaluated."))
        missing.append("Requested service: medication name with dosage OR procedure name with CPT code")
        risk_score += 0.40
    else:
        if cpt_codes:
            positive_signals.append(f"CPT codes: {', '.join(cpt_codes[:3])}")

    # A003 — Medical Necessity Statement
    if not has_justification:
        flags.append(_flag("A003","HIGH","Clinical Documentation",
            "Medical necessity statement absent.",
            "URAC UM Standard UM-14; CMS Medicare Benefit Policy Manual Ch.16",
            "Include a physician-authored narrative explaining why this specific service "
            "is medically necessary: clinical condition, failed alternatives, expected patient benefit."))
        missing.append("Physician-authored medical necessity statement")
        risk_score += 0.20
    else:
        positive_signals.append("Medical necessity language detected")

    # A004 — Treating Physician Identity / NPI
    has_npi    = bool(re.search(r"\bNPI[\s:#\-]*(\d{10})\b", extracted_text, re.IGNORECASE))
    has_md_sig = bool(patient_info.get("physician") or
                      re.search(r"\b(md|do|np|pa-c|aprn|physician|doctor|dr\.)\b", text_lower))
    if not has_npi and not has_md_sig:
        flags.append(_flag("A004","MEDIUM","Clinical Documentation",
            "Treating provider name, credentials, and NPI not identified.",
            "CMS CY2023 PA Final Rule; NCQA UM Standard 6",
            "Include ordering physician's: (1) full name, (2) credentials (MD/DO), "
            "(3) 10-digit NPI number. Unsigned or anonymous requests are rejected."))
        missing.append("Treating physician full name + credentials (MD/DO/NP) + 10-digit NPI number")
        risk_score += 0.10
    elif has_npi:
        positive_signals.append("NPI number detected")

    # A005 — Patient Identification (minimum 2 of 4 identifiers)
    has_name = bool(patient_info.get("name"))
    has_dob  = bool(patient_info.get("dob") or re.search(r"\bdob[\s:\-]+[\d/\-\.]+", text_lower))
    has_mrn  = bool(patient_info.get("mrn") or re.search(r"\b(mrn|medical record)[\s:#\-]+\w+", text_lower, re.IGNORECASE))
    has_ins  = bool(patient_info.get("insurance_id") or re.search(r"\b(member id|policy number|subscriber id)[\s:#\-]+\w+", text_lower, re.IGNORECASE))
    id_count = sum([has_name, has_dob, has_mrn, has_ins])
    if id_count < 2:
        flags.append(_flag("A005","MEDIUM","Clinical Documentation",
            f"Insufficient patient identifiers ({id_count}/4 detected). Minimum 2 required.",
            "HIPAA 45 CFR §164.514; CMS CoP §482.13",
            "Include at least 2 of: (1) Patient full name, (2) Date of birth (MM/DD/YYYY), "
            "(3) Medical record number (MRN), (4) Insurance member ID / policy number."))
        missing.append("Patient identifiers: name + DOB + MRN or Insurance Member ID (at least 2 of 4)")
        risk_score += 0.08
    else:
        positive_signals.append(f"Patient identifiers: {id_count}/4 present")

    # A006 — Service Date / Diagnosis Date
    date_pattern = re.compile(r"\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})\b")
    has_dates = bool(date_pattern.search(extracted_text) or
                     patient_info.get("service_date") or patient_info.get("diagnosis_date"))
    if not has_dates:
        flags.append(_flag("A006","LOW","Clinical Documentation",
            "No service date or diagnosis date documented.",
            "CMS Claims Processing Manual Ch.1 §10.1",
            "Document: (1) Date of service/procedure (DOS), (2) Date diagnosis established, "
            "(3) Proposed service start date. Retroactive PA requires additional justification."))
        missing.append("Date of service (DOS) and date of diagnosis")
        risk_score += 0.05

    # A007 — Document Readability
    if text_len < 50:
        flags.append(_flag("A007","CRITICAL","Clinical Documentation",
            "Document blank, corrupt, or OCR-unreadable.",
            "NCQA UM Standard 2; Payer Universal Policy",
            "Submit legible, machine-readable document. Scan at minimum 300 DPI. "
            "PDF preferred. Ensure text not obscured by shadows or creases."))
        missing.append("Legible clinical document (300 DPI minimum)")
        risk_score += 0.50
    elif text_len < 150:
        flags.append(_flag("A007B","HIGH","Clinical Documentation",
            "Submission contains minimal text (<150 chars). Likely incomplete.",
            "NCQA UM Standard 2",
            "Ensure full document uploaded. Complete submission includes: "
            "clinical note + prescription/order + supporting labs."))
        missing.append("Complete clinical documentation (current note, order, supporting records)")
        risk_score += 0.15

    # A008 — Physician Signature / Authentication
    has_signature = bool(re.search(r"\b(signature|signed|electronically signed|e-signed|"
                                   r"attestation|i certify|i attest|/s/)\b", text_lower))
    if not has_signature:
        flags.append(_flag("A008","HIGH","Clinical Documentation",
            "No physician signature, attestation, or authentication detected.",
            "CMS Signature Guidelines MLN Matters MM6698; OIG Compliance Program Guidance",
            "All PA requests must include: (1) treating physician's handwritten or electronic signature, "
            "(2) date of signature, (3) printed name + credentials below signature."))
        missing.append("Physician signature with date + printed name + credentials")
        risk_score += 0.12

    # ── CATEGORY B: MEDICAL NECESSITY & EVIDENCE ───────────────────────────

    # B001 — Clinical Guideline Reference
    has_guideline = any(kw in text_lower for kw in POSITIVE_EVIDENCE_KEYWORDS)
    if not has_guideline and not is_emergency:
        flags.append(_flag("B001","MEDIUM","Medical Necessity",
            "No clinical guideline or evidence-based reference cited.",
            "NCQA UM Standard 5; MCG™ Clinical Criteria",
            "Cite the relevant clinical guideline (e.g., '2023 ADA Standards of Medical Care — "
            "HbA1c >9% warrants insulin'; 'NCCN Guidelines v2.2024 — pembrolizumab for PD-L1 ≥50% NSCLC'). "
            "Evidence-based justification significantly increases approval likelihood."))
        missing.append("Clinical guideline citation (ADA, AHA, NCCN, CHEST, ACR, etc.)")
        risk_score += 0.08
    elif has_guideline:
        positive_signals.append("Clinical guideline reference detected")
        risk_reduction += 0.05

    # B002 — Supporting Lab Evidence for Diagnosis
    lab_text = " ".join(list(lab_values.keys())) + " " + text_lower[:5000]
    for condition, required_labs in LAB_REQUIRED_FOR.items():
        if any(condition in d for d in diseases):
            has_lab = any(lab in lab_text for lab in required_labs)
            if not has_lab:
                flags.append(_flag("B002","HIGH","Medical Necessity",
                    f"'{condition.title()}' requires lab evidence but none detected.",
                    "CMS LCD L33822; InterQual® Medical Necessity Criteria",
                    f"Provide recent lab results for {condition.title()}: "
                    f"{', '.join(required_labs[:3])} (within the past 3–6 months). "
                    "Labs establish severity and justify the requested treatment intensity."))
                missing.append(f"Lab evidence for {condition.title()}: {', '.join(required_labs[:3])} (within 3–6 months)")
                risk_score += 0.12
            else:
                positive_signals.append(f"Lab evidence for {condition.title()} detected")
            break

    # B003 — Quantity, Dosage & Duration
    has_dosage = bool(dosage_info or
                      re.search(r"\d+\s*(mg|mcg|ml|units?|tablets?|capsules?)\b", text_lower) or
                      re.search(r"\b(once|twice|daily|weekly|monthly|bid|tid|qid|prn)\b", text_lower))
    if not has_dosage and all_tx:
        flags.append(_flag("B003","MEDIUM","Medical Necessity",
            "No dosage, quantity, or duration found for requested medications.",
            "CMS Prescription Drug Manual Ch.6; URAC Pharmacy UM Standard 7",
            "Specify for each medication: (1) dose (e.g., '500mg'), (2) frequency (e.g., 'twice daily'), "
            "(3) route (e.g., 'oral'), (4) duration (e.g., '12 weeks')."))
        missing.append("Medication dosage + frequency + route + duration (e.g., 'Metformin 1000mg PO BID x 90 days')")
        risk_score += 0.07

    # B004 — Disease Severity / Functional Status
    has_severity = any(kw in text_lower for kw in [
        "severity","moderate","severe","mild","stage","grade","class",
        "functional","nyha","ecog","who class","hba1c","ejection fraction",
        "pain scale","vas score","fev1","bmi","gfr","egfr","tnm",
        "uncontrolled","poorly controlled","well-controlled","refractory",
    ])
    if not has_severity and diseases:
        flags.append(_flag("B004","MEDIUM","Medical Necessity",
            "Disease severity or functional status not documented.",
            "InterQual® Severity of Illness Criteria; MCG™ Level of Care",
            "Document disease severity using validated scales: NYHA Class III for heart failure, "
            "HbA1c 9.2% for diabetes, ECOG score for oncology, FEV1% for COPD. "
            "Severity justifies treatment intensity and urgency."))
        missing.append("Disease severity using validated clinical scale (NYHA, ECOG, HbA1c, FEV1, etc.)")
        risk_score += 0.06

    # B005 — Step Therapy Failure Documentation
    needs_step = any(d in STEP_THERAPY_REQUIRED for d in diseases)
    if needs_step:
        step_evidence = any(kw in text_lower for kw in [
            "failed","failure","inadequate response","no response","partial response",
            "intolerant","intolerance","adverse effect","adverse reaction","side effect",
            "contraindicated","contraindication","cannot tolerate","allergic to",
            "tried","attempted","previously on","previously treated",
            "first-line","second-line","step therapy","step-therapy",
            "did not respond","suboptimal","insufficient","not effective",
        ])
        matched_conditions = [d for d in diseases if d in STEP_THERAPY_REQUIRED]
        if not step_evidence:
            flags.append(_flag("B005","HIGH","Medical Necessity",
                f"Step-therapy failure not documented for: "
                f"{', '.join(mc.title() for mc in matched_conditions[:2])}.",
                "NCQA PA Reform Guidelines; Commercial Payer Step Therapy Protocols; "
                "CMS Medicare Step Therapy Rule (CMS-4182-F, effective Jan 2019)",
                "Document prior treatment history: (1) First-line agent name, "
                "(2) Dose and duration administered, (3) Specific failure reason "
                "(e.g., 'Metformin 2000mg/day x 3 months — GI intolerance', "
                "'Lisinopril 40mg — persistent cough, switched to ARB'). "
                "Without this payers assume first-line options were not tried."))
            missing.append(
                f"Step-therapy history for {matched_conditions[0].title() if matched_conditions else 'condition'}: "
                "prior drug name + dose + duration + specific failure reason"
            )
            risk_score += 0.18
        else:
            positive_signals.append("Step-therapy failure evidence documented")
            risk_reduction += 0.05

    # B006 — Specialty Drug Additional Criteria
    specialty_matched = [s for s in SPECIALTY_DRUGS if any(s in tx for tx in all_tx)]
    if specialty_matched:
        has_specialty_criteria = any(kw in text_lower for kw in [
            "baseline","screening","tuberculosis","tb test","quantiferon","tspot",
            "hepatitis b","hbsag","hepatitis c","hcv","complete blood count","cbc",
            "liver function","lft","patient assistance","specialty pharmacy","rems",
        ])
        if not has_specialty_criteria:
            flags.append(_flag("B006","HIGH","Medical Necessity",
                f"Specialty/biologic drug ({specialty_matched[0].title()}) without "
                "required pre-authorization criteria.",
                "FDA REMS Program; NCQA Specialty Pharmacy Accreditation; "
                "Commercial Payer Specialty Drug PA Criteria",
                "For specialty/biologics provide: (1) Baseline labs (CBC, LFT, CMP), "
                "(2) TB screening (QuantiFERON within 12 months), "
                "(3) Hepatitis B/C serology, (4) Conventional therapy failure documentation, "
                "(5) FDA-approved indication being treated."))
            missing.append(
                "Specialty drug pre-auth: baseline labs + TB screen + HepB/C serology + "
                "conventional therapy failure documentation"
            )
            risk_score += 0.15

    # ── CATEGORY C: STEP THERAPY & TREATMENT HISTORY ──────────────────────

    # C001 — High-Cost Procedure Medical Necessity
    high_cost_matched = [p for p in HIGH_COST_PROCEDURES if any(p in tx for tx in all_tx)]
    if high_cost_matched:
        necessity_evidence = any(kw in text_lower for kw in [
            "medically necessary","medical necessity","life-threatening",
            "quality of life","functional impairment","failed conservative",
            "conservative management failed","non-surgical failed",
            "physical therapy failed","medications failed",
            "standard of care","guideline recommended","tumor board","multidisciplinary",
        ])
        if not necessity_evidence:
            flags.append(_flag("C001","HIGH","Step Therapy",
                f"High-cost treatment ({high_cost_matched[0].title()}) lacks explicit "
                "medical necessity and conservative treatment failure evidence.",
                "CMS National Coverage Determination; BCBS Medical Policy; "
                "Aetna Clinical Policy Bulletin; UHC Coverage Determination Guideline",
                "For high-cost procedures: (1) State 'This procedure is medically necessary because...', "
                "(2) Document conservative treatment failure (e.g., '6 months PT failed'), "
                "(3) Include specialist recommendation, (4) Attach relevant imaging/lab evidence. "
                "For oncology: include tumor board recommendation."))
            missing.append(
                f"For {high_cost_matched[0].title()}: explicit medical necessity statement + "
                "evidence conservative options failed"
            )
            risk_score += 0.16

    # C002 — Quantity Limit / Days Supply
    qty_matched = [q for q in QUANTITY_LIMIT_DRUGS if any(q in tx for tx in all_tx)]
    if qty_matched:
        has_qty = any(kw in text_lower for kw in [
            "days supply","quantity","per month","monthly supply","titration","maintenance dose"
        ])
        if not has_qty:
            flags.append(_flag("C002","MEDIUM","Step Therapy",
                f"Quantity-limited medication ({qty_matched[0].title()}) — days-supply justification missing.",
                "CMS Part D Formulary Guidelines; State Medicaid Quantity Limit Policies",
                f"For {qty_matched[0].title()}: specify exact quantity, days supply, "
                "and dosing rationale if above standard limits."))
            missing.append(f"Quantity limit justification: exact quantity + days supply for {qty_matched[0].title()}")
            risk_score += 0.06

    # C003 — Compounded Medication
    if any(kw in text_lower for kw in ["compound","compounded","compounding pharmacy","503b"]):
        flags.append(_flag("C003","MEDIUM","Step Therapy",
            "Compounded medication referenced — strict coverage limitations apply.",
            "FDA Compounding Regulations 503A/503B; CMS Part B/D Compounding Coverage Policy",
            "For compounded medications: (1) Document why FDA-approved equivalent is inadequate, "
            "(2) Confirm PCAB-accredited or 503B outsourcing facility, "
            "(3) Prescriber attestation of medical necessity for compounded formulation."))
        missing.append("Compounding justification + pharmacy accreditation status (PCAB or 503B)")
        risk_score += 0.06

    # C004 — Off-Label Drug Use
    has_off_label = any(kw in text_lower for kw in [
        "off-label","off label","unapproved indication","not fda approved for",
        "compassionate use","expanded access",
    ])
    if has_off_label:
        has_off_label_support = any(kw in text_lower for kw in [
            "compendia","micromedex","clinical pharmacology","ahfs",
            "peer reviewed","published literature","clinical trial","irb approved",
        ])
        if not has_off_label_support:
            flags.append(_flag("C004","HIGH","Step Therapy",
                "Off-label drug use referenced without supporting clinical compendia evidence.",
                "CMS Medicare Benefit Policy Manual Ch.15 §50.4.5; NCCN Compendia Policy",
                "Off-label use requires: (1) Citation from approved compendia (NCCN, Micromedex, AHFS), "
                "(2) Published peer-reviewed literature, (3) No effective FDA-approved alternative."))
            missing.append("Off-label justification: NCCN/Micromedex compendia citation or peer-reviewed literature")
            risk_score += 0.18

    # ── CATEGORY D: PROVIDER & FACILITY CREDENTIALING ─────────────────────

    # D001 — Specialist Required for Complex Conditions
    for condition, specialists in SPECIALIST_MAP.items():
        if any(condition in d for d in diseases):
            has_specialist = any(sp in text_lower for sp in specialists)
            if not has_specialist:
                flags.append(_flag("D001","MEDIUM","Provider Credentialing",
                    f"Complex condition ({condition.title()}) requires specialist — none documented.",
                    "CMS CoP §482.22; NCQA HEDIS MMA Measure",
                    f"Include documentation from or referral to a {', '.join(specialists[:2])}. "
                    "Specialist involvement validates treatment plan for this condition category."))
                missing.append(f"Specialist consultation note for {condition.title()}: {', '.join(specialists[:2])}")
                risk_score += 0.07
                break

    # D002 — Out-of-Network Facility
    oon = any(kw in text_lower for kw in HIGH_COST_FACILITY_KEYWORDS)
    if oon:
        flags.append(_flag("D002","MEDIUM","Provider Credentialing",
            "Out-of-network or non-contracted facility referenced — higher scrutiny applies.",
            "ACA §2719; CMS Network Adequacy Requirements",
            "Document: (1) No in-network provider offers this service within reasonable distance/time, "
            "(2) In-network facility refusal documentation, (3) Continuity-of-care circumstances."))
        missing.append("Out-of-network justification: network adequacy documentation or continuity-of-care statement")
        risk_score += 0.08

    # D003 — REMS Program Enrollment
    rems_matched = [r for r in REMS_DRUGS if any(r in tx for tx in all_tx) or r in text_lower]
    if rems_matched:
        has_rems = any(kw in text_lower for kw in [
            "rems","ipledge","clozaril","tirf","risk evaluation",
            "patient enrollment","prescriber enrollment","pharmacy enrollment",
        ])
        if not has_rems:
            flags.append(_flag("D003","CRITICAL","Provider Credentialing",
                f"REMS-program drug detected ({rems_matched[0].title()}) without REMS enrollment confirmation.",
                "FDA REMS Authority under 21 U.S.C. §355-1; 21 CFR Parts 208, 314, 601",
                f"For {rems_matched[0].title()}: (1) Prescriber must be REMS-enrolled, "
                "(2) Pharmacy must be REMS-certified, (3) Include prescriber REMS ID, "
                "(4) Include patient enrollment documentation if required. "
                "Prescriptions cannot be filled without REMS compliance."))
            missing.append(f"REMS enrollment confirmation: prescriber REMS ID + certified pharmacy attestation")
            risk_score += 0.35

    # D004 — Inpatient Level-of-Care Justification
    inpatient_requested = any(kw in text_lower for kw in [
        "inpatient","admission","hospital admission","hospitalization",
        "observation","skilled nursing","snf","rehabilitation facility",
        "long-term acute care","ltac","residential",
    ])
    if inpatient_requested:
        loc_evidence = any(kw in text_lower for kw in [
            "cannot be managed","outpatient failed","cannot safely discharge",
            "24-hour monitoring","iv therapy","intravenous","medically unstable",
            "fall risk","elopement risk","cannot perform adls","physician order",
        ])
        if not loc_evidence:
            flags.append(_flag("D004","HIGH","Provider Credentialing",
                "Inpatient/facility-level service requested without level-of-care justification.",
                "InterQual® Level of Care Criteria; MCG™ Inpatient & Observation Criteria",
                "Document: (1) Why outpatient/home management is unsafe, "
                "(2) Required monitoring or IV therapy, (3) Vital sign instability, "
                "(4) Inability to perform ADLs, (5) Admission order from attending physician. "
                "Payers apply InterQual/MCG criteria strictly for level-of-care."))
            missing.append("Level-of-care justification: clinical reasons outpatient management is not safe/feasible")
            risk_score += 0.13

    # ── CATEGORY E: ADMINISTRATIVE & COMPLIANCE ────────────────────────────

    # E001 — CMS-Excluded Non-Covered Service
    cms_excl = any(excl in text_lower for excl in CMS_EXCLUSIONS)
    if cms_excl:
        matched_excl = [e for e in CMS_EXCLUSIONS if e in text_lower]
        flags.append(_flag("E001","CRITICAL","Administrative & Compliance",
            f"Service may be excluded from coverage ('{matched_excl[0]}' detected).",
            "CMS 42 CFR §411.15 (Exclusions from Medicare Coverage); ACA Essential Health Benefits",
            "If there is a medical reason (e.g., reconstructive surgery after mastectomy is NOT cosmetic): "
            "document (1) the medical indication, (2) this is not for cosmetic purposes, "
            "(3) applicable exception under 42 CFR §411.15."))
        missing.append("Documentation that service is medically indicated and not excluded under 42 CFR §411.15")
        risk_score += 0.45

    # E002 — Retroactive / Post-Service Authorization
    has_retroactive = any(kw in text_lower for kw in [
        "retroactive","retro auth","already performed","already administered",
        "services rendered","treatment already provided","post-service",
    ])
    if has_retroactive and not is_emergency:
        flags.append(_flag("E002","MEDIUM","Administrative & Compliance",
            "Retroactive (post-service) authorization without emergency justification.",
            "NCQA UM Standard 8; CMS Managed Care PA Regulations §438.210",
            "Retroactive PA approved only when: (1) Emergency prevented advance auth, "
            "(2) Patient's life was at risk, (3) Provider could not reasonably delay care. "
            "Document the specific emergency circumstances and timeline."))
        missing.append("Emergency circumstance documentation justifying why prior authorization was not obtained beforehand")
        risk_score += 0.10

    # E003 — Benefit Period / Coverage Verification
    has_benefit_check = any(kw in text_lower for kw in [
        "benefit year","plan year","annual limit","benefit exhausted",
        "remaining benefit","lifetime limit","mental health parity",
    ])
    if not has_benefit_check:
        flags.append(_flag("E003","LOW","Administrative & Compliance",
            "Benefit period and coverage limits not confirmed.",
            "ACA MHPAEA; ERISA §712; CMS Benefit Verification",
            "Verify before submitting: (1) Patient's benefit year dates, "
            "(2) Remaining benefit for this service, (3) Deductible/copay status, "
            "(4) Mental health parity if behavioral health service."))
        missing.append("Benefit verification: active coverage confirmation + remaining benefit limits")
        risk_score += 0.03

    # E004 — Duplicate Authorization Risk
    if any(kw in text_lower for kw in [
        "previously authorized","prior auth on file","existing authorization","already approved","re-authorization"
    ]):
        flags.append(_flag("E004","LOW","Administrative & Compliance",
            "Potential duplicate or re-authorization detected.",
            "Payer Anti-Duplication Policy; CMS Correct Coding Initiative",
            "For renewal/re-authorization: include prior PA number, expiration date, "
            "and updated clinical justification showing continued medical necessity."))
        risk_score += 0.02

    # E005 — Expedited Review / Timely Filing
    if is_emergency or any(kw in text_lower for kw in ["expedited","expedite","within 72 hours","urgent review"]):
        flags.append(_flag("E005","INFO","Administrative & Compliance",
            "Urgent/expedited review — CMS requires payer response within 72 hours.",
            "CMS 42 CFR §422.568–422.572; NCQA UM Standard 9; ACA §2719",
            "For expedited PA: (1) Payer must respond within 72 hours (CMS mandate), "
            "(2) Document urgency reason, (3) Include physician attestation that delay "
            "would seriously jeopardize patient health. "
            "Failure to respond within 72 hours = automatic authorization under CMS rules."))
        risk_reduction += 0.05

    # ── CATEGORY F: FRAUD, WASTE & ABUSE DETECTION ────────────────────────

    # F001 — Upcoding / Unbundling Risk
    if cpt_codes and len(cpt_codes) > 5:
        flags.append(_flag("F001","MEDIUM","Fraud, Waste & Abuse",
            f"Large number of CPT codes ({len(cpt_codes)}) in single submission. Potential unbundling.",
            "CMS Correct Coding Initiative (CCI); OIG FWA Guidelines; AMA CPT Bundling Rules",
            "Review CPT selection: (1) Confirm codes are not CCI edit pairs, "
            "(2) Ensure E&M level matches documented complexity, "
            "(3) Do not bill separately for services in global surgical package."))
        risk_score += 0.05

    # F002 — Controlled Substance / PDMP Check
    has_controlled = any(kw in text_lower for kw in [
        "schedule ii","schedule iii","controlled substance","opioid","morphine",
        "oxycodone","hydrocodone","fentanyl","buprenorphine","suboxone","methadone",
        "amphetamine","adderall","methylphenidate","ritalin",
        "benzodiazepine","xanax","alprazolam","diazepam","clonazepam",
    ])
    if has_controlled:
        has_pdmp = any(kw in text_lower for kw in [
            "pdmp","prescription drug monitoring","pmp","opioid agreement",
            "urine drug screen","uds","drug test","narcotics history",
        ])
        if not has_pdmp:
            flags.append(_flag("F002","MEDIUM","Fraud, Waste & Abuse",
                "Controlled substance detected — PDMP check and opioid safety documentation not confirmed.",
                "DEA 21 CFR §1306; CDC Opioid Prescribing Guidelines 2022; "
                "State PDMP Mandatory Check Laws; CMS Opioid Overutilization Policies",
                "For controlled substances: (1) Confirm PDMP was checked, "
                "(2) Include urine drug screen if ongoing opioid therapy, "
                "(3) For opioids >90 MME/day: pain specialist co-signature required, "
                "(4) Document patient-prescriber opioid treatment agreement."))
            missing.append("PDMP check confirmation + urine drug screen result (for controlled substances)")
            risk_score += 0.08

    # F003 — Excessive Services / Frequency Outlier
    frequency_outlier = bool(re.search(
        r"\b(daily|every\s*day|per\s*day)\b.{0,30}\b(\d{2,})\s*(sessions?|visits?|doses?|units?)\b",
        text_lower
    ))
    if frequency_outlier:
        flags.append(_flag("F003","MEDIUM","Fraud, Waste & Abuse",
            "High-frequency service pattern detected — potential utilization outlier.",
            "CMS Utilization Review; OIG Work Plan; ZPIC/RAC Audit Triggers",
            "Document medical necessity for each service unit. High-frequency patterns trigger "
            "automatic payer audit. Include frequency justification, expected treatment course, "
            "and measurable outcome goals."))
        risk_score += 0.04

    # ── BONUS POSITIVE SIGNALS ─────────────────────────────────────────────

    if icd_codes:             risk_reduction += 0.03
    if cpt_codes:             risk_reduction += 0.03
    if lab_values:            risk_reduction += 0.04
    if vitals:                risk_reduction += 0.02
    if has_npi:               risk_reduction += 0.03
    if len(clinical_sections) >= 3:
        positive_signals.append(f"Well-structured document: {len(clinical_sections)} clinical sections")
        risk_reduction += 0.04
    if text_len > 500:        risk_reduction += 0.03
    if is_emergency:          risk_reduction += 0.08
    if allergies:             positive_signals.append(f"Allergy documentation present: {', '.join(allergies[:2])}")

    # ── FINAL SCORING & DECISION ───────────────────────────────────────────

    risk_score = max(0.0, min(risk_score - risk_reduction, 1.0))
    confidence_score = round(1.0 - risk_score, 3)

    critical_flags = [f for f in flags if f["severity"] == "CRITICAL"]
    high_flags     = [f for f in flags if f["severity"] == "HIGH"]
    medium_flags   = [f for f in flags if f["severity"] == "MEDIUM"]

    completeness_criteria = [
        bool(diseases or icd_codes),
        bool(all_tx or cpt_codes),
        has_justification,
        bool(has_md_sig or has_npi),
        id_count >= 2,
        bool(lab_values),
        has_dosage,
        has_dates,
        has_guideline,
        has_severity,
        bool(allergies),
        len(clinical_sections) >= 2,
    ]
    completeness_score = round(sum(completeness_criteria) / len(completeness_criteria) * 100, 1)

    if text_len < 50 and not diseases and not all_tx:
        decision = "INSUFFICIENT"
        reason = ("Submission blank or completely unreadable. No clinical information extracted. "
                  "Resubmit a legible document with diagnosis, treatment request, and physician signature.")
    elif critical_flags:
        decision = "REJECT"
        reason = _build_reason(critical_flags[:2], missing[:3], "rejected")
    elif len(high_flags) >= 2:
        decision = "REJECT"
        reason = _build_reason(high_flags[:3], missing[:3], "rejected — multiple high-severity deficiencies")
    elif len(high_flags) == 1 and len(medium_flags) >= 2:
        decision = "REJECT"
        reason = _build_reason(high_flags + medium_flags[:2], missing[:3], "rejected")
    else:
        decision = "APPROVE"
        dx_str = ", ".join(d.title() for d in (diseases or ["documented condition"])[:3])
        tx_str = ", ".join(t.title() for t in (all_tx or ["treatment"])[:3])
        sig_str = (f" Positive signals: {'; '.join(positive_signals[:3])}." if positive_signals else "")
        reason = (f"Authorization approved. Diagnosis: {dx_str}. Treatment: {tx_str}. "
                  f"Completeness: {completeness_score}%.{sig_str}")

    risk_level = "LOW" if risk_score < 0.18 else "MEDIUM" if risk_score < 0.50 else "HIGH"

    rule_summary = {
        "total_rules_evaluated": 30,
        "flags_raised": len(flags),
        "critical": len(critical_flags),
        "high": len(high_flags),
        "medium": len(medium_flags),
        "low_info": len([f for f in flags if f["severity"] in ("LOW","INFO")]),
        "positive_signals": positive_signals,
        "completeness_criteria_met": sum(completeness_criteria),
    }

    logger.info(
        f"[RULES] Decision={decision} | Risk={risk_score:.3f} | "
        f"Completeness={completeness_score}% | Flags={len(flags)} "
        f"(C:{len(critical_flags)} H:{len(high_flags)} M:{len(medium_flags)})"
    )

    return {
        "decision": decision,
        "reason": reason,
        "flags": flags,
        "missing_fields": missing,
        "confidence_score": confidence_score,
        "risk_level": risk_level,
        "completeness_score": completeness_score,
        "is_emergency": is_emergency,
        "positive_signals": positive_signals,
        "rule_summary": rule_summary,
    }


def _flag(code, severity, category, message, policy, action):
    return {"code": code, "severity": severity, "category": category,
            "message": message, "policy": policy, "action": action}


def _build_reason(flags, missing, outcome):
    msgs = "; ".join(f["message"] for f in flags[:3])
    miss = (" Required: " + " | ".join(missing[:2]) + ".") if missing else ""
    return f"Authorization {outcome}. Issues: {msgs}.{miss} Resubmit with corrected documentation."
