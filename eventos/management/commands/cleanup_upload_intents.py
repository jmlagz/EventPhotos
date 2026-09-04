from collections import Counter
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from eventos.models import UploadIntent
from eventos.observability import log_operation
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
        parser.add_argument(
            "--intent-id",
            help="Evalúa únicamente este UUID, sin omitir reglas de seguridad.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Máximo total evaluado, ordenado por expires_at y UUID.",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if batch_size <= 0:
            raise CommandError("--batch-size debe ser mayor que cero.")

        limit = options["limit"]
        if limit is not None and limit <= 0:
            raise CommandError("--limit debe ser mayor que cero.")
        intent_id = options["intent_id"]
        if intent_id is not None:
            try:
                intent_id = UUID(str(intent_id))
            except (ValueError, TypeError, AttributeError):
                raise CommandError("--intent-id debe ser un UUID válido.") from None

        dry_run = options["dry_run"]
        mode = "dry-run" if dry_run else "cleanup"
        ahora = timezone.now()
        candidates = (
            UploadIntent.objects
            .select_related("evento", "mesa", "foto")
            .order_by("expires_at", "pk")
        )
        if intent_id is not None:
            # Incluye el UUID solicitado para poder explicar su inelegibilidad.
            candidates = candidates.filter(pk=intent_id)
        else:
            candidates = candidates.filter(
                cleaned_at__isnull=True,
                estado__in=CLEANABLE_STATES,
                expires_at__lte=cleanup_cutoff(ahora),
            )
        candidate_count = candidates.count()
        if limit is not None:
            candidates = candidates[:limit]
        results = Counter()
        evaluated = 0
        if intent_id is not None and candidate_count == 0:
            results["not_found"] = 1
            if options["verbosity"] >= 1:
                self.stdout.write(f"intent={intent_id} result=not_found")

        for upload_intent in candidates.iterator(chunk_size=batch_size):
            evaluated += 1
            try:
                if dry_run:
                    cleanable, reason = intent_is_cleanable(
                        upload_intent,
                        ahora,
                    )
                    result = "eligible" if cleanable else reason
                else:
                    result = cleanup_upload_intent(upload_intent.pk)
            except Exception:
                # No imprimir excepciones que puedan contener claves o secretos.
                result = "error"
            results[result] += 1
            outcome = (
                "would_clean" if result == "eligible"
                else result if result in {"cleaned", "retry", "error"}
                else "skipped"
            )
            state = (
                upload_intent.estado
                if upload_intent.estado in UploadIntent.Estado.values
                else "unknown"
            )
            if options["verbosity"] >= 2:
                self.stdout.write(
                    f"intent={upload_intent.pk} state_before={state} "
                    f"result={outcome} reason={result}"
                )

        errors = results["retry"] + results["error"]
        skipped = evaluated - results["eligible"] - results["cleaned"] - errors
        protected = results["unsafe_key"] + results["legacy_confirmed"]
        summary = [
            f"candidates={candidate_count}",
            f"evaluated={evaluated}",
            f"would_clean={results['eligible']}",
            f"cleaned={results['cleaned']}",
            f"skipped={skipped}",
            f"protected={protected}",
            f"retry={results['retry']}",
            f"errors={errors}",
            f"limit={limit if limit is not None else 'none'}",
            f"limit_reached={'yes' if limit is not None and evaluated == limit else 'no'}",
        ]
        # Conserva los contadores de motivos de la salida anterior.
        summary.extend(
            f"{key}={value}" for key, value in sorted(results.items())
            if key not in {"cleaned", "retry"}
        )
        if candidate_count == 0:
            summary.append("sin candidatos")
        self.stdout.write(f"{mode}: {', '.join(summary)}")
        log_operation(
            "upload_cleanup_result",
            scope="summary",
            mode=mode,
            candidates=candidate_count,
            evaluated=evaluated,
            would_clean=results["eligible"],
            cleaned=results["cleaned"],
            skipped=skipped,
            protected=protected,
            retry=results["retry"],
            errors=errors,
        )
        if errors:
            raise CommandError(
                f"Cleanup incompleto: {errors} error(es) operativo(s); "
                "revise el resumen antes de reintentar."
            )
