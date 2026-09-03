from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from eventos.models import UploadIntent
from eventos.upload_cleanup import (
    CLEANABLE_STATES,
    cleanup_cutoff,
    cleanup_upload_intent,
    intent_is_cleanable,
)


class Command(BaseCommand):
    help = "Limpia temporales seguras de UploadIntent en batches."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Muestra elegibilidad sin modificar DB ni R2.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Tamaño del batch de lectura (default: 100).",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if batch_size <= 0:
            raise CommandError("--batch-size debe ser mayor que cero.")

        dry_run = options["dry_run"]
        ahora = timezone.now()
        candidates = (
            UploadIntent.objects
            .filter(
                cleaned_at__isnull=True,
                estado__in=CLEANABLE_STATES,
                expires_at__lte=cleanup_cutoff(ahora),
            )
            .select_related("evento", "mesa", "foto")
            .order_by("created_at", "pk")
        )
        results = Counter()

        for upload_intent in candidates.iterator(chunk_size=batch_size):
            if dry_run:
                cleanable, reason = intent_is_cleanable(
                    upload_intent,
                    ahora,
                )
                result = "eligible" if cleanable else reason
            else:
                result = cleanup_upload_intent(upload_intent.pk)
            results[result] += 1

        mode = "dry-run" if dry_run else "cleanup"
        summary = ", ".join(
            f"{key}={value}" for key, value in sorted(results.items())
        ) or "sin candidatos"
        self.stdout.write(f"{mode}: {summary}")
