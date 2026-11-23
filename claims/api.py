import hashlib
import uuid
from decimal import Decimal
from django.http import FileResponse
from django.conf import settings
from common.storage import StorageUnavailable
from rest_framework.exceptions import APIException
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from claims.models import Claim, Document
from claims.services import submit


class ClaimInput(serializers.Serializer):
    policy_reference = serializers.CharField(max_length=60)
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01")
    )
    incident_date = serializers.DateField()
    description = serializers.CharField(min_length=10, max_length=4000)
    document_ids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, max_length=10
    )

    supporting_document_ids = serializers.ListField(
        child=serializers.UUIDField(), max_length=9, required=False
    )

    def validate(self, attrs):
        supporting = attrs.get("supporting_document_ids", [])
        if len(set(supporting)) != len(supporting) or not set(supporting).issubset(
            attrs["document_ids"]
        ):
            raise serializers.ValidationError(
                "Supporting documents must be unique and included in document_ids."
            )
        if supporting and len(set(attrs["document_ids"]) - set(supporting)) != 1:
            raise serializers.ValidationError("Provide exactly one claim document.")
        return attrs

    def validate_incident_date(self, v):
        if v > timezone.localdate():
            raise serializers.ValidationError("Future incident date.")
        return v

    def validate_document_ids(self, v):
        if len(set(v)) != len(v):
            raise serializers.ValidationError("Duplicate document IDs.")
        return v


class ClaimOutput(serializers.ModelSerializer):
    checks = serializers.SerializerMethodField()
    rejection_reasons = serializers.ReadOnlyField()
    rag_verification = serializers.SerializerMethodField()

    def get_rag_verification(self, obj):
        from verification.models import ClaimAssessment

        try:
            assessment = obj.rag_assessment
        except ClaimAssessment.DoesNotExist:
            return None
        # Raw medical text and internal error details stay in the staff portal.
        return {
            "status": assessment.status,
            "staff_review_required": True,
            "created_at": assessment.created_at,
        }

    def get_checks(self, obj):
        return list(obj.checks.values("kind", "outcome", "reason"))

    class Meta:
        model = Claim
        fields = [
            "id",
            "policy_id",
            "amount",
            "incident_date",
            "description",
            "status",
            "created_at",
            "checks",
            "rejection_reasons",
            "rag_verification",
        ]


class ClaimViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ClaimOutput
    lookup_value_regex = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

    def get_queryset(self):
        return (
            Claim.objects.filter(customer=self.request.user)
            .select_related("rag_assessment")
            .prefetch_related("checks", "reviewdecision_set")
            .order_by("-created_at")
        )

    def create(self, request):
        key = request.headers.get("Idempotency-Key", "")
        if (
            not key
            or len(key) > 100
            or not key.isascii()
            or any(ord(c) < 33 for c in key)
        ):
            raise ValidationError(
                {"Idempotency-Key": "Provide 1–100 printable ASCII characters."}
            )
        data = ClaimInput(data=request.data)
        data.is_valid(raise_exception=True)
        claim, created = submit(request.user, data.validated_data, key)
        return Response(ClaimOutput(claim).data, status=201 if created else 200)


class DocumentViewSet(viewsets.ViewSet):
    lookup_value_regex = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

    def create(self, request):
        file = request.FILES.get("file")
        if not file or file.size > settings.MAX_DOCUMENT_BYTES:
            raise ValidationError("Provide a PDF within the upload size limit.")
        head = file.read(5)
        file.seek(0)
        if head != b"%PDF-":
            raise ValidationError("Only PDF files accepted.")
        digest = hashlib.sha256()
        for chunk in file.chunks():
            digest.update(chunk)
        file.seek(0)
        file.name = f"{uuid.uuid4()}.pdf"
        try:
            doc = Document.objects.create(
                owner=request.user, file=file, sha256=digest.hexdigest(), size=file.size
            )
        except StorageUnavailable:
            error = APIException("Document storage is temporarily unavailable.")
            error.status_code = 503
            raise error from None
        return Response(
            {"id": str(doc.pk), "sha256": doc.sha256, "size": doc.size}, status=201
        )

    def retrieve(self, request, pk=None):
        docs = (
            Document.objects.all()
            if request.user.is_staff and request.user.has_perm("claims.change_claim")
            else Document.objects.filter(owner=request.user)
        )
        doc = get_object_or_404(docs, pk=pk)
        return FileResponse(
            doc.file.open("rb"),
            as_attachment=True,
            filename=f"{doc.pk}.pdf",
            content_type="application/pdf",
        )
