import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from auditor_bridge.config import BridgeError
from auditor_bridge.models import Profile
from auditor_bridge.services import pull_inventory, synchronize_profile


class Command(BaseCommand):
    help = "Reconcile Auditor inventory and retry pending profile synchronization"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=30)

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            try:
                pull_inventory()
            except BridgeError as exc:
                self.stderr.write(str(exc))
            now = timezone.now()
            keys = Profile.objects.exclude(state="deleted").filter(
                Q(state="synced", updated_at__lte=now - timedelta(seconds=60))
                | (~Q(state="synced") & (Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))),
            ).values_list("key", flat=True)[:200]
            for key in keys:
                synchronize_profile(key)
            if options["once"]:
                return
            time.sleep(max(5, options["interval"]))
