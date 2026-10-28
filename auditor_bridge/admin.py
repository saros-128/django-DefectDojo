from django.contrib import admin

from auditor_bridge.models import CallbackReceipt, InventoryState, Profile, RepositoryLink, ScanImport


class ReadOnlyBridgeAdmin(admin.ModelAdmin):

    """Inspect durable integration state; mutations use the authenticated API."""

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Profile)
class ProfileAdmin(ReadOnlyBridgeAdmin):
    list_display = ("key", "sequence_id", "state", "last_error", "attempts", "updated_at")
    list_filter = ("state",)
    search_fields = ("key",)


@admin.register(RepositoryLink)
class RepositoryAdmin(ReadOnlyBridgeAdmin):
    list_display = ("provider", "instance", "repository_id", "product_id", "engagement_id", "updated_at")
    search_fields = ("repository_id", "instance")


@admin.register(ScanImport)
class ScanImportAdmin(ReadOnlyBridgeAdmin):
    list_display = ("scan_id", "repository", "test_id", "finding_count", "commit_sha", "created_at")
    search_fields = ("scan_id", "commit_sha")


admin.site.register(CallbackReceipt, ReadOnlyBridgeAdmin)
admin.site.register(InventoryState, ReadOnlyBridgeAdmin)
