"""
IPAA v2 — Views
Multi-document pipeline orchestrator + all page views.
"""

import json
import logging
import time
from pathlib import Path

from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.db.models import Count, Avg, Q

from .models import AuthorizationRequest, AuditLog, UploadedDocument
from .ocr import extract_text_from_file
from .nlp import extract_entities
from .rules import evaluate
from .llm import run_llm_agent
from. validation import validate_document
from .ai_consistency import ai_medical_consistency_check

logger = logging.getLogger("extract")
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf"}
MAX_FILES = 10
MAX_FILE_SIZE = 15 * 1024 * 1024  # 15MB per file


# ── Upload / Home ──────────────────────────────────────────────────────────────

@require_http_methods(["GET", "POST"])
def upload_view(request):
    # ── GET REQUEST ─────────────────────────────────────────────
    if request.method == "GET":
        stats = _dashboard_stats()
        recent = AuthorizationRequest.objects.filter(
            status=AuthorizationRequest.Status.COMPLETE
        ).order_by("-created_at")[:6]

        # ✅ FIX: move steps from template to backend
        steps = [
            "OCR",
            "NLP Entity Extraction",
            "15-Rule Policy Engine",
            "Groq LLaMA-3 AI",
            "Decision + Appeal"
        ]

        return render(request, "extract/upload.html", {
            "recent_cases": recent,
            "stats": stats,
            "steps": steps   # 🔥 important
        })

    # ── POST REQUEST ────────────────────────────────────────────
    uploaded_files = request.FILES.getlist("documents")

    if not uploaded_files:
        messages.error(request, "No files uploaded. Please select at least one document.")
        return redirect("upload")

    if len(uploaded_files) > MAX_FILES:
        messages.error(request, f"Maximum {MAX_FILES} files allowed per submission.")
        return redirect("upload")

    valid_files = []
    for uf in uploaded_files:
        ext = Path(uf.name).suffix.lower()

        if ext not in ALLOWED_EXTENSIONS:
            messages.error(request, f"'{uf.name}' — unsupported type. Use PNG, JPG, JPEG, or PDF.")
            return redirect("upload")

        if uf.size > MAX_FILE_SIZE:
            messages.error(request, f"'{uf.name}' exceeds 15MB limit.")
            return redirect("upload")

        valid_files.append(uf)

    # ── CREATE REQUEST ──────────────────────────────────────────
    primary = valid_files[0]
    primary_ext = Path(primary.name).suffix.lower()

    auth_request = AuthorizationRequest.objects.create(
        file=primary,
        file_name=primary.name,
        file_type=primary_ext.lstrip(".").upper(),
        document_count=len(valid_files),
        request_type=request.POST.get("request_type", "INITIAL"),
        priority=request.POST.get("priority", "ROUTINE"),
        submitter_name=request.POST.get("submitter_name", "").strip(),
        submitter_notes=request.POST.get("notes", "").strip(),
        status=AuthorizationRequest.Status.PROCESSING,
    )

    # ── SAVE DOCUMENTS ─────────────────────────────────────────
    for i, uf in enumerate(valid_files):
        ext = Path(uf.name).suffix.lower()

        UploadedDocument.objects.create(
            request=auth_request,
            file=uf,
            file_name=uf.name,
            file_type=ext.lstrip(".").upper(),
            file_size=uf.size,
            document_label=request.POST.get(f"label_{i}", ""),
        )

    _log(
        auth_request,
        AuditLog.Stage.UPLOAD,
        f"{len(valid_files)} file(s) received | total size: {sum(f.size for f in valid_files)} bytes"
    )

    logger.info(
        f"[PIPELINE] Request {auth_request.reference_number} created | {len(valid_files)} docs"
    )

    # ── RUN PIPELINE ───────────────────────────────────────────
    try:
        _run_pipeline(auth_request)

    except Exception as exc:
        logger.error(f"[PIPELINE] Fatal: {exc}", exc_info=True)

        auth_request.status = AuthorizationRequest.Status.ERROR
        auth_request.error_message = str(exc)
        auth_request.save()

        messages.error(request, "Processing error. Please try again.")
        return redirect("upload")

    # ── SUCCESS REDIRECT ───────────────────────────────────────
    return redirect("result", pk=auth_request.pk)


# ── Result ─────────────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def result_view(request, pk):
    req = get_object_or_404(AuthorizationRequest, pk=pk)
    docs = req.uploaded_documents.all()
    audit_logs = req.audit_logs.all()
    flags = req.rule_flags.get("flags", [])

    context = {
        "req": req,
        "docs": docs,
        "audit_logs": audit_logs,
        "disease_list": req.disease_list,
        "treatment_list": req.treatment_list,
        "medication_list": req.medication_list,
        "procedure_list": req.procedure_list,
        "icd_list": req.icd_list,
        "flags": flags,
        "critical_flags": [f for f in flags if f.get("severity") == "CRITICAL"],
        "high_flags": [f for f in flags if f.get("severity") == "HIGH"],
        "medium_flags": [f for f in flags if f.get("severity") == "MEDIUM"],
        "info_flags": [f for f in flags if f.get("severity") in ("LOW", "INFO")],
        "missing_fields": req.missing_fields,
        "confidence_pct": req.confidence_percent,
        "lab_values": {},
        "vitals": {},
    }
    # Try to recover lab/vitals from NLP pass
    try:
        context["lab_values"] = json.loads(req.patient_info).get("lab_values", {}) if req.patient_info.startswith("{") else {}
    except Exception:
        pass

    return render(request, "extract/result.html", context)


# ── History ────────────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def history_view(request):
    qs = AuthorizationRequest.objects.all().order_by("-created_at")

    # Filters
    decision_filter = request.GET.get("decision", "")
    risk_filter = request.GET.get("risk", "")
    search = request.GET.get("q", "").strip()

    if decision_filter:
        qs = qs.filter(decision=decision_filter)
    if risk_filter:
        qs = qs.filter(risk_level=risk_filter)
    if search:
        qs = qs.filter(
            Q(reference_number__icontains=search) |
            Q(disease__icontains=search) |
            Q(treatment__icontains=search) |
            Q(file_name__icontains=search) |
            Q(submitter_name__icontains=search)
        )

    all_reqs = qs[:100]
    stats = _dashboard_stats()

    return render(request, "extract/history.html", {
        "requests": all_reqs,
        "stats": stats,
        "decision_filter": decision_filter,
        "risk_filter": risk_filter,
        "search": search,
    })


# ── Dashboard ──────────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def dashboard_view(request):
    stats = _dashboard_stats()
    recent = AuthorizationRequest.objects.order_by("-created_at")[:10]
    # Trend data (last 7 days)
    from datetime import timedelta
    today = timezone.now().date()
    trend = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_qs = AuthorizationRequest.objects.filter(created_at__date=day)
        trend.append({
            "date": day.strftime("%b %d"),
            "total": day_qs.count(),
            "approved": day_qs.filter(decision="APPROVE").count(),
            "rejected": day_qs.filter(decision="REJECT").count(),
        })
    return render(request, "extract/dashboard.html", {
        "stats": stats, "recent": recent, "trend_json": json.dumps(trend)
    })


# ── Compare View ───────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def compare_view(request):
    pk1 = request.GET.get("a")
    pk2 = request.GET.get("b")

    req1 = get_object_or_404(AuthorizationRequest, pk=pk1) if pk1 else None
    req2 = get_object_or_404(AuthorizationRequest, pk=pk2) if pk2 else None

    # ✅ FIX: create list for template
    req_list = [r for r in [req1, req2] if r]

    all_reqs = AuthorizationRequest.objects.filter(
        status=AuthorizationRequest.Status.COMPLETE
    ).order_by("-created_at")[:50]

    return render(request, "extract/compare.html", {
        "req1": req1,
        "req2": req2,
        "req_list": req_list,   # 🔥 important
        "all_reqs": all_reqs
    })


# ── Appeal Builder ─────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def appeal_view(request, pk):
    req = get_object_or_404(AuthorizationRequest, pk=pk)
    return render(request, "extract/appeal.html", {"req": req})


# ── Guidelines / Help ──────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def guidelines_view(request):
    categories = {
        "A": {"icon": "📋", "name": "Clinical Documentation", "rules": [
            {"code":"A001","severity":"CRITICAL","title":"Primary Diagnosis / ICD-10 Code","description":"Checks that at least one confirmed diagnosis with ICD-10-CM code is present in the submission.","policy":"CMS 42 CFR §410.32; NCQA UM Standard 1","action":"Include the confirmed diagnosis with ICD-10 code (e.g., Type 2 Diabetes — E11.9, Hypertension — I10). Must be documented by treating physician."},
            {"code":"A002","severity":"CRITICAL","title":"Requested Service / Treatment / CPT Code","description":"Verifies that the requested treatment, medication, or procedure is explicitly named in the submission.","policy":"NCQA UM Standard 3; AMA PA Reform Principle 1","action":"State the medication name + dose OR procedure name + CPT code. E.g., 'Metformin 1000mg PO BID' or 'Total Knee Replacement CPT 27447'."},
            {"code":"A003","severity":"HIGH","title":"Medical Necessity Statement","description":"Checks for physician-authored narrative explaining why the service is medically necessary.","policy":"URAC UM Standard UM-14; CMS Medicare Benefit Policy Manual Ch.16","action":"Include a narrative: 'This service is medically necessary because [condition] has [severity], and [treatment] is indicated per [guideline]. Alternatives [X] have been tried and failed.'"},
            {"code":"A004","severity":"MEDIUM","title":"Treating Physician Identity / NPI","description":"Validates that the ordering physician name, credentials, and 10-digit NPI number are present.","policy":"CMS CY2023 PA Final Rule; NCQA UM Standard 6","action":"Include: Dr. [Full Name], [MD/DO/NP], NPI: [10-digit number]. Both name and NPI are required. Anonymous requests are auto-rejected."},
            {"code":"A005","severity":"MEDIUM","title":"Patient Identification (minimum 2 of 4)","description":"Requires minimum 2 patient identifiers: name, DOB, MRN, or insurance member ID.","policy":"HIPAA 45 CFR §164.514; CMS CoP §482.13","action":"Include at least 2 of: (1) Patient full name, (2) Date of birth MM/DD/YYYY, (3) MRN, (4) Insurance member ID."},
            {"code":"A006","severity":"LOW","title":"Service Date / Diagnosis Date","description":"Checks for documented date of service and date diagnosis was established.","policy":"CMS Claims Processing Manual Ch.1 §10.1","action":"Document the date of service (DOS), date of diagnosis, and proposed start date for treatment."},
            {"code":"A007","severity":"CRITICAL","title":"Document Readability / Minimum Content","description":"Ensures document is legible and contains sufficient clinical content for evaluation.","policy":"NCQA UM Standard 2; Payer Universal Policy","action":"Submit legible documents. Scan at 300 DPI minimum. Use PDF format. Ensure no text is obscured by shadows, folds, or low contrast."},
            {"code":"A008","severity":"HIGH","title":"Physician Signature / Authentication","description":"Checks for physician signature, attestation, or electronic authentication on the document.","policy":"CMS Signature Guidelines MLN Matters MM6698; OIG Compliance Program Guidance","action":"Every PA document must have physician wet signature or electronic signature with date, printed name, and credentials."},
        ]},
        "B": {"icon": "🔬", "name": "Medical Necessity & Evidence", "rules": [
            {"code":"B001","severity":"MEDIUM","title":"Clinical Guideline / Evidence Reference","description":"Checks whether submission cites evidence-based clinical guidelines to support the request.","policy":"NCQA UM Standard 5; MCG™ Clinical Criteria","action":"Cite a relevant guideline: 'Per 2024 ADA Standards, HbA1c >9% warrants insulin therapy' or 'Per NCCN NSCLC Guidelines v2.2024, pembrolizumab is indicated for PD-L1 ≥50%'."},
            {"code":"B002","severity":"HIGH","title":"Lab Evidence for Diagnosis","description":"For conditions like diabetes, CKD, heart failure — verifies supporting lab values are documented.","policy":"CMS LCD L33822; InterQual® Medical Necessity Criteria","action":"Include recent lab results within 3–6 months: HbA1c for diabetes, eGFR/creatinine for CKD, BNP/EF for heart failure, LDL for hyperlipidemia."},
            {"code":"B003","severity":"MEDIUM","title":"Dosage, Quantity & Duration","description":"Checks that medication dose, frequency, route, and treatment duration are specified.","policy":"CMS Prescription Drug Manual Ch.6; URAC Pharmacy UM Standard 7","action":"For each medication: 'Metformin 1000mg oral twice daily for 90 days'. For procedures: 'PT — 3x/week x 8 weeks'."},
            {"code":"B004","severity":"MEDIUM","title":"Disease Severity / Functional Status","description":"Checks that disease severity is documented using validated clinical tools.","policy":"InterQual® Severity of Illness; MCG™ Level of Care","action":"Document severity: NYHA Class for heart failure, HbA1c level for diabetes, ECOG score for oncology, FEV1% for COPD, CDAI for Crohn's disease."},
            {"code":"B005","severity":"HIGH","title":"Step Therapy Failure Documentation","description":"For step-therapy conditions, verifies prior treatment failure is documented with specific details.","policy":"NCQA PA Reform Guidelines; CMS Medicare Step Therapy Rule (CMS-4182-F)","action":"Name each prior drug tried: 'Metformin 2000mg/day x 3 months — discontinued due to GI intolerance. Glipizide 10mg/day x 6 months — inadequate HbA1c response (9.8% on therapy)'. Must include drug, dose, duration, and specific failure reason."},
            {"code":"B006","severity":"HIGH","title":"Specialty Drug Pre-Auth Criteria","description":"For biologics and specialty drugs, checks that required baseline screening is documented.","policy":"FDA REMS Program; NCQA Specialty Pharmacy Accreditation","action":"For any biologic/specialty drug: include (1) baseline CBC, LFT, CMP, (2) TB screening (QuantiFERON-TB within 12 months), (3) Hepatitis B surface antigen, (4) Hepatitis C antibody, (5) documentation that conventional therapies failed."},
        ]},
        "C": {"icon": "🔄", "name": "Step Therapy & Treatment History", "rules": [
            {"code":"C001","severity":"HIGH","title":"High-Cost Procedure Medical Necessity","description":"For high-cost surgeries, imaging, and biologics — verifies medical necessity and conservative care failure.","policy":"CMS National Coverage Determination; BCBS Medical Policy; Aetna Clinical Policy Bulletin","action":"State: 'This procedure is medically necessary because [condition] has [severity], and conservative treatments [X, Y] have failed after [duration].' For oncology: include tumor board recommendation."},
            {"code":"C002","severity":"MEDIUM","title":"Quantity Limit / Days Supply","description":"For quantity-limited drugs (opioids, growth hormone, testosterone, botox), checks days supply justification.","policy":"CMS Part D Formulary Guidelines; State Medicaid Quantity Limit Policies","action":"Specify exact quantity, days supply, and dosing rationale for quantity-limited medications. For above-limit requests: include clinical justification."},
            {"code":"C003","severity":"MEDIUM","title":"Compounded Medication","description":"Detects compounded medication requests which have strict coverage limitations.","policy":"FDA Compounding Regulations 503A/503B; CMS Part B/D Compounding Coverage Policy","action":"Document: why no FDA-approved equivalent is clinically appropriate, confirm PCAB-accredited pharmacy or 503B outsourcing facility, include prescriber attestation."},
            {"code":"C004","severity":"HIGH","title":"Off-Label Drug Use","description":"Detects off-label medication use and checks for required compendia or literature support.","policy":"CMS Medicare Benefit Policy Manual Ch.15 §50.4.5; NCCN Compendia Policy","action":"Cite an approved compendia (NCCN Guidelines, Micromedex, AHFS Drug Information) or peer-reviewed literature. Document absence of effective FDA-approved alternative."},
        ]},
        "D": {"icon": "👨‍⚕️", "name": "Provider & Facility Credentialing", "rules": [
            {"code":"D001","severity":"MEDIUM","title":"Specialist Required for Complex Conditions","description":"For complex conditions (cancer, MS, transplant, dialysis), checks for specialist involvement.","policy":"CMS CoP §482.22; NCQA HEDIS MMA Measure","action":"Include a specialist consultation note or referral letter. For oncology: oncologist note. For rheumatology: rheumatologist note. For neurology: neurologist note."},
            {"code":"D002","severity":"MEDIUM","title":"Out-of-Network Facility","description":"Detects out-of-network or non-contracted facility references requiring additional justification.","policy":"ACA §2719; CMS Network Adequacy Requirements","action":"Document: (1) no in-network provider available within reasonable distance, (2) in-network refusal documentation, or (3) continuity-of-care circumstances. Emergency services are exempt."},
            {"code":"D003","severity":"CRITICAL","title":"REMS Program Enrollment","description":"For FDA REMS-required drugs (isotretinoin, clozapine, opioids, thalidomide), checks enrollment documentation.","policy":"FDA REMS Authority 21 U.S.C. §355-1; 21 CFR Parts 208, 314, 601","action":"Confirm prescriber REMS enrollment ID and REMS-certified pharmacy. Patient enrollment documentation required for some REMS programs. Include REMS ID number in submission."},
            {"code":"D004","severity":"HIGH","title":"Inpatient Level-of-Care Justification","description":"For inpatient/facility admissions, checks that level-of-care criteria are documented.","policy":"InterQual® Level of Care Criteria; MCG™ Inpatient & Observation Criteria","action":"Document: why outpatient management is unsafe, IV therapy requirement, vital sign instability, inability to perform ADLs, or fall/elopement risk. Include admission order from attending physician."},
        ]},
        "E": {"icon": "📑", "name": "Administrative & Compliance", "rules": [
            {"code":"E001","severity":"CRITICAL","title":"CMS-Excluded Non-Covered Service","description":"Detects services excluded from Medicare/Medicaid coverage (cosmetic, custodial, dental, etc.).","policy":"CMS 42 CFR §411.15; ACA Essential Health Benefits","action":"If service has a medical indication (e.g., reconstructive surgery is not cosmetic): explicitly document the medical reason and applicable exception under 42 CFR §411.15."},
            {"code":"E002","severity":"MEDIUM","title":"Retroactive / Post-Service Authorization","description":"Detects retroactive PA requests and checks for emergency justification.","policy":"NCQA UM Standard 8; CMS Managed Care PA Regulations §438.210","action":"For retroactive requests: document the specific emergency circumstances preventing advance authorization, timeline, and that delay would have harmed the patient."},
            {"code":"E003","severity":"LOW","title":"Benefit Period / Coverage Verification","description":"Checks for benefit period and coverage limit documentation.","policy":"ACA MHPAEA; ERISA §712; CMS Benefit Verification","action":"Verify patient's benefit year, remaining benefits, deductible status, and applicable service limits before submitting."},
            {"code":"E004","severity":"LOW","title":"Duplicate Authorization Risk","description":"Detects potential duplicate submissions or re-authorization requests.","policy":"Payer Anti-Duplication Policy; CMS Correct Coding Initiative","action":"For renewals: include prior authorization number, expiration date, and updated clinical justification."},
            {"code":"E005","severity":"INFO","title":"Expedited Review / Timely Filing","description":"Identifies urgent/emergency requests qualifying for the 72-hour expedited review pathway.","policy":"CMS 42 CFR §422.568–422.572; NCQA UM Standard 9; ACA §2719","action":"For expedited requests: include physician attestation that standard 5-day timeline would jeopardize patient health. CMS mandates payer response within 72 hours."},
        ]},
        "F": {"icon": "🛡️", "name": "Fraud, Waste & Abuse Detection", "rules": [
            {"code":"F001","severity":"MEDIUM","title":"Upcoding / CPT Unbundling Risk","description":"Flags submissions with large numbers of CPT codes that may indicate upcoding or unbundling.","policy":"CMS Correct Coding Initiative (CCI); OIG FWA Guidelines; AMA CPT Bundling Rules","action":"Review CPT selection: confirm no CCI edit pairs, E&M level matches documented complexity, and bundled services are not billed separately."},
            {"code":"F002","severity":"MEDIUM","title":"Controlled Substance / PDMP Check","description":"Detects controlled substances and checks for PDMP verification and opioid safety documentation.","policy":"DEA 21 CFR §1306; CDC Opioid Prescribing Guidelines 2022; State PDMP Laws","action":"For controlled substances: confirm PDMP checked, include urine drug screen for ongoing opioid therapy, pain specialist co-signature for >90 MME/day, and patient-prescriber treatment agreement."},
            {"code":"F003","severity":"MEDIUM","title":"High-Frequency Service Outlier","description":"Detects unusually high-frequency service patterns that may trigger utilization review.","policy":"CMS Utilization Review; OIG Work Plan; ZPIC/RAC Audit Triggers","action":"Document medical necessity for each service unit, expected treatment course, and measurable outcome goals. High-frequency patterns trigger automatic payer audit."},
        ]},
    }
    return render(request, 'extract/guidelines.html', {'categories': categories})


# ── API Endpoints ──────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def api_status(request, pk):
    req = get_object_or_404(AuthorizationRequest, pk=pk)
    return JsonResponse({
        "id": str(req.pk),
        "reference": req.reference_number,
        "status": req.status,
        "decision": req.decision,
        "confidence": req.confidence_percent,
        "risk_level": req.risk_level,
        "processing_time_ms": req.processing_time_ms,
        "document_count": req.document_count,
    })


@require_http_methods(["GET"])
def api_stats(request):
    return JsonResponse(_dashboard_stats())


# ── Pipeline ───────────────────────────────────────────────────────────────────

def _run_pipeline(auth_request: AuthorizationRequest):
    pipeline_start = time.time()
    all_docs = auth_request.uploaded_documents.all()
    combined_text = ""
    total_confidence = 0.0

    # ── OCR ────────────────────────────────────────────────
    stage_start = time.time()
    for doc in all_docs:
        try:
            text, conf = extract_text_from_file(doc.file.path)
            doc.extracted_text = text
            doc.ocr_confidence = conf
            doc.processed = True
            doc.save()

            combined_text += f"\n\n--- DOCUMENT: {doc.file_name} ---\n{text}"
            total_confidence += conf

        except Exception as exc:
            logger.error(f"[OCR] Failed for {doc.file_name}: {exc}")
            _log(auth_request, AuditLog.Stage.OCR, f"OCR failed for {doc.file_name}: {exc}", success=False)

    avg_confidence = total_confidence / len(all_docs) if all_docs else 0.0
    auth_request.extracted_text = combined_text.strip()
    auth_request.ocr_confidence = avg_confidence

    _log(auth_request, AuditLog.Stage.OCR,
         f"OCR complete: {len(all_docs)} docs, {len(combined_text)} chars",
         duration_ms=_ms(stage_start))

    # ── NLP ──────────────────────────────────────────────
    stage_start = time.time()
    try:
        entities = extract_entities(auth_request.extracted_text)

        auth_request.disease = ", ".join(entities["diseases"])
        auth_request.treatment = ", ".join(entities["treatments"])
        auth_request.medications = ", ".join(entities["medications"])
        auth_request.procedures = ", ".join(entities["procedures"])
        auth_request.icd_codes = ", ".join(entities["icd_codes"])
        auth_request.cpt_codes = ", ".join(entities["cpt_codes"])
        auth_request.allergies = ", ".join(entities["allergies"])
        auth_request.dosage_info = entities.get("dosage_info", "")
        auth_request.lab_values = entities.get("lab_values", {})
        auth_request.vitals = entities.get("vitals", {})
        auth_request.patient_info = json.dumps(entities.get("patient_info", {}))

        _log(auth_request, AuditLog.Stage.NLP,
             f"NLP extracted {entities.get('entity_count', 0)} entities",
             duration_ms=_ms(stage_start))

    except Exception as exc:
        logger.error(f"[NLP] Failed: {exc}")
        entities = {}

    # 🔥 ── NEW: VALIDATION LAYER ─────────────────────────
    stage_start = time.time()
    try:
        validation = validate_document(auth_request.extracted_text, entities)

        auth_request.validation_score = validation["validation_score"]
        auth_request.fraud_risk = validation["fraud_risk"]
        auth_request.validation_flags = validation["validation_flags"]
 
        # ── AI CONSISTENCY CHECK (NEW) ───────────────────────
        
        try:
            ai_check = ai_medical_consistency_check(entities, auth_request.extracted_text)
            
            auth_request.consistency_flags = ai_check["issues"]
            auth_request.consistency_level = ai_check["severity"]
        
            _log(auth_request, AuditLog.Stage.ML,
                 f"AI Consistency={ai_check['severity']}",
                 duration_ms=_ms(stage_start))
        
        except Exception as exc:
            logger.error(f"[AI CONSISTENCY] Failed: {exc}")
            auth_request.consistency_level = "UNKNOWN"
 
 
        _log(auth_request, AuditLog.Stage.ML,
             f"Fraud Risk={validation['fraud_risk']} Score={validation['validation_score']:.2f}",
             duration_ms=_ms(stage_start))

    except Exception as exc:
        logger.error(f"[VALIDATION] Failed: {exc}")
        auth_request.fraud_risk = "UNKNOWN"

    # ── RULES ────────────────────────────────────────────
    stage_start = time.time()
    try:
        rule_result = evaluate(entities, auth_request.extracted_text)

        # 🔥 DECISION OVERRIDE
        if auth_request.fraud_risk in ["HIGH", "MEDIUM"]:
            auth_request.decision = "REJECT"
            auth_request.reason = "Document flagged as suspicious (low trust score)"

        else:
            # 🚨 PRIORITY: AI + FRAUD

            if auth_request.fraud_risk in ["HIGH", "MEDIUM"]:
                auth_request.decision = "REJECT"
                auth_request.reason = "Suspicious document detected"
            
            elif auth_request.consistency_level == "HIGH":
                auth_request.decision = "REJECT"
                auth_request.reason = "Medical inconsistency detected"
            
            else:
                auth_request.decision = rule_result["decision"]
                auth_request.reason = rule_result["reason"]
            auth_request.reason = rule_result["reason"]

        auth_request.rule_flags = {
            "flags": rule_result["flags"],
            "rule_summary": rule_result.get("rule_summary", {}),
            "positive_signals": rule_result.get("positive_signals", []),
        }

        auth_request.missing_fields = rule_result["missing_fields"]
        auth_request.confidence_score = rule_result["confidence_score"]
        auth_request.risk_level = rule_result["risk_level"]
        auth_request.completeness_score = rule_result.get("completeness_score", 0)

        _log(auth_request, AuditLog.Stage.RULES,
             f"Decision={auth_request.decision}",
             duration_ms=_ms(stage_start))

    except Exception as exc:
        logger.error(f"[RULES] Failed: {exc}")
        auth_request.decision = "INSUFFICIENT"
        auth_request.reason = "Rule evaluation error."

    # ── LLM ─────────────────────────────────────────────
    try:
        llm_result = run_llm_agent(
            extracted_text=auth_request.extracted_text,
            decision=auth_request.decision,
            reason=auth_request.reason,
            entities=entities,
            flags=rule_result.get("flags", []),
            missing_fields=rule_result.get("missing_fields", []),
            confidence_score=auth_request.confidence_score,
        )

        auth_request.ai_explanation = llm_result.get("explanation", "")
        auth_request.ai_summary = llm_result.get("clinical_summary", "")
        auth_request.ai_action = llm_result.get("suggestions", "")
        auth_request.ai_appeal = llm_result.get("appeal_letter", "")

    except Exception as exc:
        logger.error(f"[LLM] Failed: {exc}")

    # ── FINAL SAVE ──────────────────────────────────────
    auth_request.status = AuthorizationRequest.Status.COMPLETE
    auth_request.processing_time_ms = _ms(pipeline_start)
    auth_request.save()

    logger.info(f"[PIPELINE] {auth_request.reference_number} DONE")

def _dashboard_stats():
    total = AuthorizationRequest.objects.count()
    complete = AuthorizationRequest.objects.filter(status="COMPLETE")
    return {
        "total": total,
        "approved": AuthorizationRequest.objects.filter(decision="APPROVE").count(),
        "rejected": AuthorizationRequest.objects.filter(decision="REJECT").count(),
        "insufficient": AuthorizationRequest.objects.filter(decision="INSUFFICIENT").count(),
        "processing": AuthorizationRequest.objects.filter(status="PROCESSING").count(),
        "avg_confidence": round((complete.aggregate(a=Avg("confidence_score"))["a"] or 0) * 100, 1),
        "avg_time_ms": round(complete.aggregate(a=Avg("processing_time_ms"))["a"] or 0),
        "approval_rate": round(
            AuthorizationRequest.objects.filter(decision="APPROVE").count() / max(total, 1) * 100, 1
        ),
    }


def _log(req, stage, message, success=True, duration_ms=0, metadata=None):
    AuditLog.objects.create(
        request=req, stage=stage, message=message,
        success=success, duration_ms=duration_ms, metadata=metadata or {}
    )


def _ms(t):
    return int((time.time() - t) * 1000)

@require_http_methods(["GET"])
def result_view(request, pk):
    req = get_object_or_404(AuthorizationRequest, pk=pk)
    docs = req.uploaded_documents.all()
    audit_logs = req.audit_logs.all()
    flags = req.rule_flags.get("flags", [])

    context = {
        "req": req,
        "docs": docs,
        "audit_logs": audit_logs,
        "disease_list": req.disease_list,
        "treatment_list": req.treatment_list,
        "medication_list": req.medication_list,
        "procedure_list": req.procedure_list,
        "icd_list": req.icd_list,
        "flags": flags,
        "critical_flags": [f for f in flags if f.get("severity") == "CRITICAL"],
        "high_flags": [f for f in flags if f.get("severity") == "HIGH"],
        "medium_flags": [f for f in flags if f.get("severity") == "MEDIUM"],
        "info_flags": [f for f in flags if f.get("severity") in ("LOW", "INFO")],
        "missing_fields": req.missing_fields,
        "confidence_pct": req.confidence_percent,
        "lab_values": {},
        "vitals": {},
    }

    return render(request, "extract/result.html", context)
