from django.contrib import admin
from .models import AuthorizationRequest, UploadedDocument, AuditLog, PolicyTemplate


@admin.register(AuthorizationRequest)
class AuthorizationRequestAdmin(admin.ModelAdmin):
    list_display = ["reference_number", "file_name", "document_count", "decision",
                    "confidence_score", "risk_level", "completeness_score", "status", "created_at"]
    list_filter = ["decision", "status", "risk_level", "request_type", "priority"]
    search_fields = ["reference_number", "disease", "treatment", "file_name", "submitter_name"]
    readonly_fields = ["id", "reference_number", "created_at", "updated_at", "extracted_text",
                       "ocr_confidence", "processing_time_ms", "ai_explanation", "ai_suggestions",
                       "appeal_letter", "clinical_summary", "rule_flags", "missing_fields"]


@admin.register(UploadedDocument)
class UploadedDocumentAdmin(admin.ModelAdmin):
    list_display = ["file_name", "file_type", "file_size", "ocr_confidence", "processed", "uploaded_at"]
    list_filter = ["file_type", "processed"]
    readonly_fields = ["extracted_text", "ocr_confidence"]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["request", "stage", "success", "duration_ms", "timestamp"]
    list_filter = ["stage", "success"]
    readonly_fields = ["request", "stage", "timestamp", "message", "duration_ms", "metadata"]


@admin.register(PolicyTemplate)
class PolicyTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "payer", "policy_code", "requires_prior_auth", "active"]
    list_filter = ["active", "requires_step_therapy", "requires_prior_auth"]
