from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, F, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from eventos.models import Evento, UploadIntent
from eventos.upload_quota import reservas_upload


STATE_ORDER = (
    UploadIntent.Estado.PENDING,
    UploadIntent.Estado.FINALIZING,
    UploadIntent.Estado.CONFIRMED,
    UploadIntent.Estado.CANCELLED,
    UploadIntent.Estado.EXPIRED,
    UploadIntent.Estado.CLEANUP_PENDING,
)


def _age_buckets(queryset, anchor, now):
    annotated = queryset.annotate(_health_age_anchor=anchor)
    fifteen_minutes_ago = now - timedelta(minutes=15)
    one_hour_ago = now - timedelta(hours=1)
    one_day_ago = now - timedelta(hours=24)
    values = annotated.aggregate(
        lt_15_min=Count(
            "id",
            filter=Q(_health_age_anchor__gt=fifteen_minutes_ago),
        ),
        min_15_60=Count(
            "id",
            filter=(
                Q(_health_age_anchor__lte=fifteen_minutes_ago)
                & Q(_health_age_anchor__gt=one_hour_ago)
            ),
        ),
        hour_1_24=Count(
            "id",
            filter=(
                Q(_health_age_anchor__lte=one_hour_ago)
                & Q(_health_age_anchor__gte=one_day_ago)
            ),
        ),
        gt_24_hour=Count(
            "id",
            filter=Q(_health_age_anchor__lt=one_day_ago),
        ),
    )
    return {name: value or 0 for name, value in values.items()}


class Command(BaseCommand):
    help = "Reporta salud de UploadIntent sin modificar BD ni consultar R2."

    def handle(self, *args, **options):
        now = timezone.now()
        state_counts = {
            item["estado"]: item["total"]
            for item in (
                UploadIntent.objects
                .values("estado")
                .annotate(total=Count("id"))
            )
        }

        finalizing = UploadIntent.objects.filter(
            estado=UploadIntent.Estado.FINALIZING,
        )
        cleanup_pending = UploadIntent.objects.filter(
            estado=UploadIntent.Estado.CLEANUP_PENDING,
        )
        pending_expired = UploadIntent.objects.filter(
            estado=UploadIntent.Estado.PENDING,
            expires_at__lt=now,
        )

        finalizing_buckets = _age_buckets(
            finalizing,
            Coalesce("finalizing_at", "created_at"),
            now,
        )
        cleanup_buckets = _age_buckets(
            cleanup_pending,
            Coalesce("finalizing_at", "created_at"),
            now,
        )
        pending_buckets = _age_buckets(
            pending_expired,
            F("expires_at"),
            now,
        )

        reserved_intents = 0
        reserved_bytes = 0
        for evento in Evento.objects.only("pk").iterator():
            event_count, event_bytes = reservas_upload(evento, now)
            reserved_intents += event_count
            reserved_bytes += event_bytes

        self.stdout.write("upload_intents_health")
        for state in STATE_ORDER:
            self.stdout.write(
                f"state name={state} count={state_counts.get(state, 0)}"
            )
        self._write_buckets(
            "finalizing",
            "finalizing_at_or_created_at",
            finalizing_buckets,
        )
        self._write_buckets(
            "cleanup_pending",
            "finalizing_at_or_created_at",
            cleanup_buckets,
        )
        self.stdout.write(
            "pending_expired "
            f"total={sum(pending_buckets.values())}"
        )
        self._write_buckets("pending_expired", "expires_at", pending_buckets)
        self.stdout.write(
            "quota "
            f"reserved_intents={reserved_intents} "
            f"reserved_bytes={reserved_bytes}"
        )

    def _write_buckets(self, state, anchor, buckets):
        self.stdout.write(
            "age "
            f"state={state} "
            f"anchor={anchor} "
            f"lt_15_min={buckets['lt_15_min']} "
            f"min_15_60={buckets['min_15_60']} "
            f"hour_1_24={buckets['hour_1_24']} "
            f"gt_24_hour={buckets['gt_24_hour']}"
        )
