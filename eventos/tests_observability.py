import json
import re
from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import Evento, Mesa, UploadIntent
from .tests import FakeR2Client
from .upload_quota import reservas_upload


class UploadIntentsHealthCommandTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.event = Evento.objects.create(
            nombre="Evento health",
            fecha=date(2026, 9, 4),
            estado=Evento.Estado.ACTIVE,
        )
        self.table = Mesa.objects.create(evento=self.event, numero=1)

    def create_intent(
        self,
        state,
        suffix,
        *,
        created_delta=timedelta(minutes=-5),
        expires_delta=timedelta(minutes=5),
        finalizing_delta=None,
        declared_size=100,
        real_size=None,
        cleaned=False,
    ):
        intent = UploadIntent.objects.create(
            evento=self.event,
            mesa=self.table,
            object_key=f"secret-temporary-key-{suffix}",
            final_object_key=f"secret-final-key-{suffix}",
            nombre_original=f"secret-name-{suffix}.jpg",
            content_type_declarado="image/jpeg",
            tamaño_declarado=declared_size,
            tamaño_real=real_size,
            hash_declarado=f"{suffix:064x}",
            source_etag=f"secret-etag-{suffix}",
            estado=state,
            expires_at=self.now + expires_delta,
            finalizing_at=(
                self.now + finalizing_delta
                if finalizing_delta is not None
                else None
            ),
            cleaned_at=self.now if cleaned else None,
        )
        UploadIntent.objects.filter(pk=intent.pk).update(
            created_at=self.now + created_delta,
        )
        intent.refresh_from_db()
        return intent

    def run_health(self):
        output = StringIO()
        with patch(
            "eventos.management.commands.upload_intents_health.timezone.now",
            return_value=self.now,
        ):
            call_command("upload_intents_health", stdout=output)
        return output.getvalue()

    def populate_inventory(self):
        suffix = 1
        for delta in (
            timedelta(minutes=-5),
            timedelta(minutes=-30),
            timedelta(hours=-2),
            timedelta(hours=-48),
        ):
            self.create_intent(
                UploadIntent.Estado.FINALIZING,
                suffix,
                finalizing_delta=delta,
                declared_size=100 + suffix,
                real_size=200 + suffix,
            )
            suffix += 1

        cleanup_specs = (
            (timedelta(minutes=-5), False, True),
            (timedelta(minutes=-30), True, True),
            (timedelta(hours=-2), True, False),
            (timedelta(hours=-48), False, False),
        )
        for delta, cleaned, has_finalizing_at in cleanup_specs:
            self.create_intent(
                UploadIntent.Estado.CLEANUP_PENDING,
                suffix,
                created_delta=delta,
                finalizing_delta=delta if has_finalizing_at else None,
                declared_size=100 + suffix,
                real_size=None,
                cleaned=cleaned,
            )
            suffix += 1

        for delta in (
            timedelta(minutes=-5),
            timedelta(minutes=-30),
            timedelta(hours=-2),
            timedelta(hours=-48),
        ):
            self.create_intent(
                UploadIntent.Estado.PENDING,
                suffix,
                expires_delta=delta,
                declared_size=100 + suffix,
            )
            suffix += 1

        self.create_intent(
            UploadIntent.Estado.PENDING,
            suffix,
            expires_delta=timedelta(minutes=5),
            declared_size=123,
        )
        suffix += 1
        for state in (
            UploadIntent.Estado.CONFIRMED,
            UploadIntent.Estado.CANCELLED,
            UploadIntent.Estado.EXPIRED,
        ):
            self.create_intent(state, suffix)
            suffix += 1

    def test_empty_states_report_zero(self):
        output = self.run_health()

        for state in UploadIntent.Estado.values:
            self.assertIn(f"state name={state} count=0", output)
        self.assertIn("pending_expired total=0", output)
        self.assertIn("quota reserved_intents=0 reserved_bytes=0", output)

    def test_counts_and_age_buckets_are_correct(self):
        self.populate_inventory()

        output = self.run_health()

        expected_counts = {
            "pending": 5,
            "finalizing": 4,
            "confirmed": 1,
            "cancelled": 1,
            "expired": 1,
            "cleanup_pending": 4,
        }
        for state, count in expected_counts.items():
            self.assertIn(f"state name={state} count={count}", output)
        for state in ("finalizing", "cleanup_pending", "pending_expired"):
            line = next(
                item for item in output.splitlines()
                if item.startswith(f"age state={state} ")
            )
            self.assertIn("lt_15_min=1", line)
            self.assertIn("min_15_60=1", line)
            self.assertIn("hour_1_24=1", line)
            self.assertIn("gt_24_hour=1", line)
        self.assertIn("pending_expired total=4", output)

    def test_quota_matches_shared_reservation_logic(self):
        self.populate_inventory()
        expected_count, expected_bytes = reservas_upload(self.event, self.now)

        output = self.run_health()

        self.assertIn(
            f"quota reserved_intents={expected_count} "
            f"reserved_bytes={expected_bytes}",
            output,
        )

    @patch("eventos.r2.get_r2_client")
    def test_command_is_read_only_and_does_not_create_r2_client(self, get_r2):
        self.populate_inventory()

        with CaptureQueriesContext(connection) as queries:
            self.run_health()

        writes = [
            query["sql"] for query in queries.captured_queries
            if re.match(
                r"\s*(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE)\b",
                query["sql"],
                re.IGNORECASE,
            )
        ]
        self.assertEqual(writes, [])
        get_r2.assert_not_called()

    def test_output_does_not_contain_sensitive_fields(self):
        intent = self.create_intent(
            UploadIntent.Estado.FINALIZING,
            999,
            finalizing_delta=timedelta(hours=-2),
        )

        output = self.run_health()

        forbidden = (
            str(intent.id),
            intent.object_key,
            intent.final_object_key,
            intent.source_etag,
            intent.nombre_original,
            self.table.token,
            self.table.codigo_acceso,
            "https://",
            "R2_SECRET_ACCESS_KEY",
            "DATABASE_URL",
        )
        for value in forbidden:
            self.assertNotIn(value, output)


class UploadObservabilityLoggingTests(TestCase):
    def setUp(self):
        self.event = Evento.objects.create(
            nombre="Evento logging",
            fecha=date(2026, 9, 4),
            estado=Evento.Estado.ACTIVE,
        )
        self.table = Mesa.objects.create(evento=self.event, numero=1)
        session = self.client.session
        session["mesa_id"] = self.table.id
        session["evento_id"] = self.event.id
        session["instrucciones_aceptadas"] = True
        session.save()

    def upload_url(self):
        return reverse(
            "solicitar_url_subida",
            args=[self.event.slug, self.table.token],
        )

    def confirmation_url(self):
        return reverse(
            "confirmar_subida",
            args=[self.event.slug, self.table.token],
        )

    def upload_data(self):
        return {
            "nombre": "private-name.jpg",
            "content_type": "image/jpeg",
            "hash_sha256": "a" * 64,
            "tamaño": "1024",
        }

    def create_intent(self, suffix="normal"):
        intent = UploadIntent(
            evento=self.event,
            mesa=self.table,
            nombre_original="private-name.jpg",
            content_type_declarado="image/jpeg",
            tamaño_declarado=1024,
            hash_declarado="b" * 64,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        intent.object_key = (
            f"eventos/{self.event.slug}/mesas/{self.table.token}/"
            f"upload-intents/{intent.id}.jpg"
        )
        intent.final_object_key = (
            f"eventos/{self.event.slug}/fotos/{intent.id}.jpg"
        )
        intent.save()
        return intent

    def parsed_events(self, records):
        return [json.loads(record.getMessage()) for record in records]

    def assert_logs_are_sanitized(self, records, intent):
        output = "\n".join(record.getMessage() for record in records)
        forbidden = (
            "RAW_EXCEPTION_SECRET",
            "https://private-presigned.invalid/secret",
            intent.object_key,
            intent.final_object_key,
            intent.source_etag or "private-etag",
            intent.nombre_original,
            self.table.token,
            self.table.codigo_acceso,
            "R2_SECRET_ACCESS_KEY",
            "DATABASE_URL",
        )
        for value in forbidden:
            self.assertNotIn(value, output)

    @patch(
        "eventos.views.generar_url_subida",
        return_value="https://private-presigned.invalid/secret",
    )
    def test_created_signal_is_structured_and_sanitized(self, _generate_url):
        with self.assertLogs("eventos.operations", level="INFO") as captured:
            response = self.client.post(self.upload_url(), self.upload_data())

        self.assertEqual(response.status_code, 200)
        intent = UploadIntent.objects.get()
        events = self.parsed_events(captured.records)
        self.assertIn(
            {
                "event": "upload_intent_created",
                "intent_id": str(intent.id),
                "state": "pending",
            },
            events,
        )
        self.assert_logs_are_sanitized(captured.records, intent)

    @patch(
        "eventos.views.generar_url_subida",
        side_effect=RuntimeError("RAW_EXCEPTION_SECRET"),
    )
    def test_presign_failure_logs_r2_and_cancelled_without_raw_error(self, _generate_url):
        with self.assertLogs("eventos.operations", level="INFO") as captured:
            response = self.client.post(self.upload_url(), self.upload_data())

        self.assertEqual(response.status_code, 500)
        intent = UploadIntent.objects.get()
        events = self.parsed_events(captured.records)
        self.assertTrue(any(item["event"] == "r2_operation_failed" for item in events))
        self.assertTrue(any(item["event"] == "upload_intent_cancelled" for item in events))
        self.assert_logs_are_sanitized(captured.records, intent)

    @patch("eventos.views.get_r2_client")
    def test_finalizing_and_confirmed_signals_are_emitted(self, get_r2):
        intent = self.create_intent()
        r2 = FakeR2Client()
        r2.add_temporary(intent, size=1024, etag='"private-etag"')
        get_r2.return_value = r2

        with self.assertLogs("eventos.operations", level="INFO") as captured:
            response = self.client.post(
                self.confirmation_url(),
                {"upload_intent_id": str(intent.id)},
            )

        self.assertEqual(response.status_code, 200)
        intent.refresh_from_db()
        events = self.parsed_events(captured.records)
        self.assertTrue(any(item["event"] == "upload_intent_finalizing" for item in events))
        self.assertTrue(any(item["event"] == "upload_intent_confirmed" for item in events))
        self.assert_logs_are_sanitized(captured.records, intent)

    @patch("eventos.views.get_r2_client")
    def test_r2_failure_signal_is_sanitized(self, get_r2):
        intent = self.create_intent("failure")
        r2 = FakeR2Client()
        r2.add_temporary(intent, size=1024, etag='"private-etag"')
        r2.copy_object.side_effect = RuntimeError("RAW_EXCEPTION_SECRET")
        get_r2.return_value = r2

        with self.assertLogs("eventos.operations", level="INFO") as captured:
            response = self.client.post(
                self.confirmation_url(),
                {"upload_intent_id": str(intent.id)},
            )

        self.assertEqual(response.status_code, 503)
        intent.refresh_from_db()
        events = self.parsed_events(captured.records)
        r2_event = next(
            item for item in events
            if item["event"] == "r2_operation_failed"
            and item["operation"] == "copy_to_final"
        )
        self.assertEqual(r2_event["error_class"], "unexpected_error")
        self.assertTrue(
            any(item["event"] == "upload_intent_confirmation_failed" for item in events)
        )
        self.assert_logs_are_sanitized(captured.records, intent)

    def test_cleanup_dry_run_emits_safe_result_and_summary(self):
        intent = self.create_intent("cleanup")
        UploadIntent.objects.filter(pk=intent.pk).update(
            expires_at=timezone.now() - timedelta(hours=1),
        )
        output = StringIO()

        with self.assertLogs("eventos.operations", level="INFO") as captured:
            call_command(
                "cleanup_upload_intents",
                "--dry-run",
                "--intent-id",
                str(intent.id),
                "--limit",
                "1",
                "--verbosity",
                "2",
                stdout=output,
            )

        events = self.parsed_events(captured.records)
        self.assertTrue(
            any(
                item["event"] == "upload_cleanup_result"
                and item.get("scope") == "summary"
                for item in events
            )
        )
        self.assertIn(str(intent.id), output.getvalue())
        self.assert_logs_are_sanitized(captured.records, intent)
