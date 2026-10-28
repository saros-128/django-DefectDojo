import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from auditor_bridge import client, services
from auditor_bridge.authentication import CallbackAuthentication
from auditor_bridge.config import BridgeError
from auditor_bridge.models import InventoryState, Profile, RepositoryLink, ScanImport
from auditor_bridge.serializers import CallbackSerializer, ProfileSerializer, RepositoryBindingSerializer


class SuperuserOnly(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_active and request.user.is_superuser)


class AdminView(APIView):
    # Inherit Dojo's configured authentication, including token expiry policy.
    permission_classes = [SuperuserOnly]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if len(kwargs.get("key", "")) > 100:
            raise ValidationError({"key": "Profile keys must not exceed 100 characters"})


def profile_response(profile):
    return {"key": profile.key, "sequence_id": profile.sequence_id, "desired": profile.desired,
            "state": profile.state, "last_error": profile.last_error, "attempts": profile.attempts,
            "next_attempt_at": profile.next_attempt_at, "remote": profile.remote}


@extend_schema_view(get=extend_schema(operation_id="auditor_profiles_list", responses=OpenApiTypes.ANY))
class ProfileListView(AdminView):
    def get(self, request):
        return Response([profile_response(p) for p in Profile.objects.order_by("key")[:200]])


@extend_schema_view(
    get=extend_schema(responses=OpenApiTypes.ANY),
    put=extend_schema(request=ProfileSerializer, responses=OpenApiTypes.OBJECT),
    delete=extend_schema(request=None, responses=OpenApiTypes.OBJECT),
)
class ProfilesView(AdminView):
    def get(self, request, key):
        return Response(profile_response(get_object_or_404(Profile, key=key)))

    def put(self, request, key):
        serializer = ProfileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        desired = json.loads(json.dumps(serializer.validated_data, cls=DjangoJSONEncoder))
        try:
            profile = services.save_profile(key, desired)
        except BridgeError as exc:
            return Response({"error": str(exc)}, status=409)
        return Response(profile_response(profile), status=202)

    def delete(self, request, key):
        with transaction.atomic():
            profile = get_object_or_404(Profile.objects.select_for_update(), key=key)
            if profile.state == "deleted":
                return Response(profile_response(profile))
            profile.state, profile.next_attempt_at = "delete_pending", None
            profile.save(update_fields=["state", "next_attempt_at"])
        return Response(profile_response(services.synchronize_profile(key)), status=202)


@extend_schema_view(post=extend_schema(request=None, responses=OpenApiTypes.OBJECT))
class ProfileDisableView(AdminView):
    def post(self, request, key):
        get_object_or_404(Profile, key=key)
        try:
            return Response(profile_response(services.disable_profile(key)), status=202)
        except BridgeError as exc:
            return Response({"error": str(exc)}, status=409)


@extend_schema_view(post=extend_schema(request=None, responses=OpenApiTypes.OBJECT))
class ProfileSyncView(AdminView):
    def post(self, request, key):
        get_object_or_404(Profile, key=key)
        profile = services.synchronize_profile(key)
        return Response(profile_response(profile), status=200 if profile.state == "synced" else 202)


@extend_schema_view(get=extend_schema(responses=OpenApiTypes.OBJECT))
class CatalogView(AdminView):
    def get(self, request):
        try:
            return Response({"jobs": services.catalog_jobs(),
                             "triggers": client.request("GET", "/api/v1/scan-trigger-events")})
        except BridgeError as exc:
            return Response({"error": str(exc)}, status=502)


@extend_schema_view(get=extend_schema(responses=OpenApiTypes.OBJECT), post=extend_schema(request=None, responses=OpenApiTypes.OBJECT))
class InventoryView(AdminView):
    def get(self, request):
        try:
            offset = max(0, int(request.query_params.get("offset", 0)))
        except ValueError:
            return Response({"error": "invalid_offset"}, status=400)
        items = list(RepositoryLink.objects.order_by("pk").values(
            "id", "provider", "instance", "repository_id", "product_id", "engagement_id", "inventory", "updated_at",
        )[offset:offset + 201])
        state = InventoryState.objects.filter(pk="default").values().first()
        return Response({"items": items[:200], "next_offset": offset + 200 if len(items) > 200 else None, "sync": state})

    def post(self, request):
        try:
            return Response({"upserted": services.pull_inventory()})
        except BridgeError as exc:
            return Response({"error": str(exc)}, status=502)


@extend_schema_view(put=extend_schema(request=RepositoryBindingSerializer, responses=OpenApiTypes.OBJECT))
class RepositoryBindingView(AdminView):
    def put(self, request, repository_pk):
        from dojo.models import Product  # noqa: PLC0415 -- avoid model import cycles

        serializer = RepositoryBindingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        get_object_or_404(RepositoryLink, pk=repository_pk)
        get_object_or_404(Product, pk=serializer.validated_data["product_id"])
        try:
            row = services.bind_repository(repository_pk, **serializer.validated_data)
        except BridgeError as exc:
            return Response({"error": str(exc)}, status=409)
        return Response({"id": row.pk, "provider": row.provider, "instance": row.instance,
                         "repository_id": row.repository_id, "product_id": row.product_id,
                         "engagement_id": row.engagement_id, "product_managed": row.product_managed})


@extend_schema_view(get=extend_schema(responses=OpenApiTypes.ANY))
class ImportsView(AdminView):
    def get(self, request):
        return Response(list(ScanImport.objects.order_by("-created_at").values()[:200]))


@extend_schema_view(post=extend_schema(request=CallbackSerializer, responses=OpenApiTypes.OBJECT))
class CallbackView(APIView):
    authentication_classes = [CallbackAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = json.loads(json.dumps(serializer.validated_data, cls=DjangoJSONEncoder))
        key = request.headers.get("Idempotency-Key")
        if key and key != payload["callback_id"]:
            return Response({"error": "idempotency_key_mismatch"}, status=409)
        try:
            acknowledgement = services.callback(payload)
            # The wire ACK is deliberately closed. Coverage stays in ScanImport
            # and /imports; adding internal metadata must not break old Auditors.
            keys = {"scan_id", "provider", "instance", "repository_id", "project_found",
                    "project_id", "report_status"}
            return Response({key: value for key, value in acknowledgement.items() if key in keys})
        except BridgeError as exc:
            code = str(exc)
            status = 409 if code.endswith("conflict") else 502 if code.startswith("auditor_") else 400
            return Response({"error": code}, status=status)
