from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from auditor_bridge.config import BridgeError
from auditor_bridge.models import RepositoryLink
from auditor_bridge.services import bind_repository, generic_report, upsert_repository
from auditor_bridge.views import RepositoryBindingView


class RepositoryBindingTests(TestCase):
    def setUp(self):
        from dojo.models import Product, Product_Type  # noqa: PLC0415 -- model bootstrap

        self.metadata = {"provider": "gitlab", "instance": "https://git.example",
                         "repository_id": "42", "path": "team/app"}
        self.first = upsert_repository(self.metadata)
        self.second = upsert_repository({**self.metadata, "repository_id": "73"})
        kind = Product_Type.objects.create(name="Applications")
        self.product = Product.objects.create(name="Existing ASOC product", prod_type=kind,
                                              description="operator maintained")

    def test_multiple_repositories_share_product_and_keep_distinct_engagements(self):
        first = bind_repository(self.first.pk, self.product.pk, self.first.product_id)
        second = bind_repository(self.second.pk, self.product.pk, self.second.product_id)
        self.assertEqual(first.product_id, second.product_id)
        self.assertNotEqual(first.engagement_id, second.engagement_id)
        self.assertEqual(second.engagement.product_id, self.product.pk)
        self.assertFalse(second.product_managed)
        renamed = upsert_repository({**self.metadata, "repository_id": "73", "path": "other/moved"})
        self.assertEqual(renamed.pk, second.pk)
        self.assertEqual(renamed.product_id, self.product.pk)
        self.product.refresh_from_db()
        self.assertEqual(self.product.description, "operator maintained")
        self.assertEqual(RepositoryLink.objects.count(), 2)

    def test_stale_binding_and_existing_imports_are_refused(self):
        with self.assertRaisesMessage(BridgeError, "repository_binding_conflict"):
            bind_repository(self.first.pk, self.product.pk, self.second.product_id)
        with patch("auditor_bridge.services.ScanImport.objects.filter") as imports:
            imports.return_value.exists.return_value = True
            with self.assertRaisesMessage(BridgeError, "repository_has_imports_conflict"):
                bind_repository(self.first.pk, self.product.pk, self.first.product_id)
        self.first.refresh_from_db()
        self.assertNotEqual(self.first.product_id, self.product.pk)

    def test_binding_api_is_restricted_and_readback_is_concrete(self):
        from django.contrib.auth import get_user_model  # noqa: PLC0415 -- model bootstrap

        factory = APIRequestFactory()
        user = get_user_model().objects.create_user(username="operator")
        payload = {"product_id": self.product.pk, "expected_product_id": self.first.product_id}
        request = factory.put("/binding/", payload, format="json")
        force_authenticate(request, user=user)
        view = RepositoryBindingView.as_view()
        self.assertEqual(view(request, repository_pk=self.first.pk).status_code, 403)
        user.is_superuser = True
        user.save()
        request = factory.put("/binding/", payload, format="json")
        force_authenticate(request, user=user)
        response = view(request, repository_pk=self.first.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["product_id"], self.product.pk)
        self.assertEqual(response.data["repository_id"], "42")
        request = factory.put("/binding/", {**payload, "token": "forbidden"}, format="json")
        force_authenticate(request, user=user)
        self.assertEqual(view(request, repository_pk=self.first.pk).status_code, 400)

    def test_unknown_coverage_is_not_imported_as_clean(self):
        report = {"status": "COMPLETED", "finding_count": 0, "findings": [],
                  "job_runs": [{"job_slug": "opengrep", "status": "COMPLETED"}]}
        with self.assertRaisesMessage(BridgeError, "unknown_scan_coverage"):
            generic_report({**report, "coverage": {"status": "unknown"}})
        self.assertEqual(generic_report({**report, "coverage": {"status": "partial"}}),
                         {"findings": []})

    def test_callback_wire_ack_excludes_internal_coverage_metadata(self):
        from types import SimpleNamespace  # noqa: PLC0415
        from auditor_bridge.views import CallbackView  # noqa: PLC0415
        payload = {"callback_id": "11111111-1111-4111-8111-111111111111",
                   "scan_id": "22222222-2222-4222-8222-222222222222", "scan_status": "COMPLETED",
                   "provider": "gitlab", "instance": "https://git.example", "repository_id": "42",
                   "commit_sha": "a" * 40, "report_url": "https://auditor.example/report"}
        ack = {key: payload[key] for key in ("scan_id", "provider", "instance", "repository_id")}
        ack.update(project_found=True, project_id="7", report_status="imported")
        request = APIRequestFactory().post("/callback/", payload, format="json")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with patch("auditor_bridge.views.services.callback", return_value={**ack, "coverage_status": "partial"}):
            response = CallbackView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, ack)

    def test_missing_secret_returns_sanitized_configuration_error(self):
        from auditor_bridge.config import secret  # noqa: PLC0415
        with patch.dict("os.environ", {"TEST_SECRET_FILE": "/unavailable/secret"}):
            with self.assertRaisesMessage(BridgeError, "unavailable_secret_configuration"):
                secret("TEST_SECRET")
