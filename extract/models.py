"""
IPAA v2 — Database Models
Full lifecycle of a multi-document prior authorization case.
"""

import uuid
from django.db import models
from django.utils import timezone


class AuthorizationRequest(models.Model):

    class Decision(models.TextChoices):
        APPROVE = "APPROVE", "Approved"
        REJECT = "REJECT", "Rejected"
        PENDING = "PENDING", "Pending Review"
        INSUFFICIENT = "INSUFFICIENT", "Insufficient Data"
        PARTIAL = "PARTIAL", "Partial Approval"

    class Status(models.TextChoices):
        PROCESSING = "PROCESSING", "Processing"
        COMPLETE = "COMPLETE", "Complete"
        ERROR = "ERROR", "Error"

    class RequestType(models.TextChoices):
        INITIAL = "INITIAL", "Initial Authorization"
        RENEWAL = "RENEWAL", "Renewal / Continuation"
        EMERGENCY = "EMERGENCY", "Emergency Authorization"
        APPEAL = "APPEAL", "Appeal"

    # ── Identity ───────────────────────────────────────────────────────────
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference_number = models.CharField(max_length=20, unique=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    # ── Request Metadata ───────────────────────────────────────────────────
    request_type = models.CharField(max_length=20, choices=RequestType.choices, default=RequestType.INITIAL)
    submitter_name = models.CharField(max_length=200, blank=True)
    submitter_notes = models.TextField(blank=True, help_text="Additional context from submitter")
    priority = models.CharField(max_length=10, choices=[
        ("ROUTINE", "Routine"), ("URGENT", "Urgent"), ("STAT", "STAT/Emergency")
    ], default="ROUTINE")

    # ── Primary File (backward compat) ─────────────────────────────────────
    file = models.FileField(upload_to="documents/%Y/%m/%d/", blank=True, null=True)
    file_name = models.CharField(max_length=255, blank=True)
    file_type = models.CharField(max_length=20, blank=True)

    # ── OCR / Combined Text ────────────────────────────────────────────────
    extracted_text = models.TextField(blank=True)
    ocr_confidence = models.FloatField(default=0.0)
    document_count = models.IntegerField(default=1)

    # ── NLP Entities ───────────────────────────────────────────────────────
    disease = models.TextField(blank=True)
    treatment = models.TextField(blank=True)
    patient_info = models.TextField(blank=True)
    icd_codes = models.TextField(blank=True)
    cpt_codes = models.TextField(blank=True)
    medications = models.TextField(blank=True)
    procedures = models.TextField(blank=True)
    lab_values = models.JSONField(default=dict, blank=True)
    vitals = models.JSONField(default=dict, blank=True)
    allergies = models.TextField(blank=True)
    dosage_info = models.TextField(blank=True)

    # ── Rule Engine ────────────────────────────────────────────────────────
    decision = models.CharField(max_length=20, choices=Decision.choices, default=Decision.PENDING)
    reason = models.TextField(blank=True)
    rule_flags = models.JSONField(default=dict, blank=True)

    # ── Scoring ────────────────────────────────────────────────────────────
    confidence_score = models.FloatField(default=0.0)
    risk_level = models.CharField(max_length=10, blank=True)
    completeness_score = models.FloatField(default=0.0, help_text="0-100 document completeness")
    
    # ── Validation / Fraud Detection ───────────────────────
    validation_score = models.FloatField(default=1.0)
    fraud_risk = models.CharField(max_length=10, default="LOW")
    validation_flags = models.JSONField(default=list, blank=True)
    
    # ── Consistent level ────────────────────────────────────────────────────────────
    consistency_level = models.CharField(max_length=10, default="LOW")

    # ── LLM Output ────────────────────────────────────────────────────────
    ai_explanation = models.TextField(blank=True)
    ai_suggestions = models.TextField(blank=True)
    appeal_letter = models.TextField(blank=True)
    clinical_summary = models.TextField(blank=True)
    missing_fields = models.JSONField(default=list, blank=True)
    
    ai_summary = models.TextField(blank=True)
    ai_action = models.TextField(blank=True)
    ai_appeal = models.TextField(blank=True)

    # ── Pipeline ───────────────────────────────────────────────────────────
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    processing_time_ms = models.IntegerField(default=0)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Authorization Request"
        verbose_name_plural = "Authorization Requests"

    def __str__(self):
        return f"[{self.reference_number}] {self.decision} — {self.created_at.strftime('%Y-%m-%d %H:%M')}"

    def save(self, *args, **kwargs):
        if not self.reference_number:
            self.reference_number = f"IPAA-{str(self.id)[:8].upper()}"
        super().save(*args, **kwargs)

    @property
    def confidence_percent(self):
        return round(self.confidence_score * 100, 1)

    @property
    def disease_list(self):
        return [d.strip() for d in self.disease.split(",") if d.strip()]

    @property
    def treatment_list(self):
        return [t.strip() for t in self.treatment.split(",") if t.strip()]

    @property
    def medication_list(self):
        return [m.strip() for m in self.medications.split(",") if m.strip()]

    @property
    def procedure_list(self):
        return [p.strip() for p in self.procedures.split(",") if p.strip()]

    @property
    def icd_list(self):
        return [c.strip() for c in self.icd_codes.split(",") if c.strip()]

    @property
    def documents(self):
        return self.uploaded_documents.all()


class UploadedDocument(models.Model):
    """Each individual file attached to an authorization request."""

    request = models.ForeignKey(
        AuthorizationRequest, on_delete=models.CASCADE, related_name="uploaded_documents"
    )
    file = models.FileField(upload_to="documents/%Y/%m/%d/")
    file_name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=20)
    file_size = models.IntegerField(default=0)
    page_count = models.IntegerField(default=1)
    extracted_text = models.TextField(blank=True)
    ocr_confidence = models.FloatField(default=0.0)
    document_label = models.CharField(max_length=100, blank=True,
        help_text="e.g. 'Prescription', 'Lab Report', 'Referral Letter'")
    uploaded_at = models.DateTimeField(default=timezone.now)
    processed = models.BooleanField(default=False)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return f"{self.file_name} ({self.file_type}) — {self.request.reference_number}"


class AuditLog(models.Model):
    """Immutable audit trail per pipeline stage."""

    class Stage(models.TextChoices):
        UPLOAD = "UPLOAD", "Document Upload"
        OCR = "OCR", "OCR Extraction"
        NLP = "NLP", "NLP Analysis"
        RULES = "RULES", "Rule Evaluation"
        ML = "ML", "ML Prediction"
        LLM = "LLM", "LLM Reasoning"
        OUTPUT = "OUTPUT", "Output Generated"

    request = models.ForeignKey(AuthorizationRequest, on_delete=models.CASCADE, related_name="audit_logs")
    stage = models.CharField(max_length=20, choices=Stage.choices)
    timestamp = models.DateTimeField(default=timezone.now)
    success = models.BooleanField(default=True)
    message = models.TextField(blank=True)
    duration_ms = models.IntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["timestamp"]

    def __str__(self):
        return f"{'✓' if self.success else '✗'} [{self.stage}] {self.request.reference_number}"


class PolicyTemplate(models.Model):
    """Reusable insurance policy rule templates (admin-configurable)."""
    name = models.CharField(max_length=200)
    payer = models.CharField(max_length=200, blank=True)
    policy_code = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    requires_step_therapy = models.BooleanField(default=False)
    requires_prior_auth = models.BooleanField(default=True)
    max_days_supply = models.IntegerField(default=30)
    covered_diagnoses = models.TextField(blank=True)
    excluded_treatments = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.payer})"
