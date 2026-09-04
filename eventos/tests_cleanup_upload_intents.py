from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from io import StringIO
from threading import Lock
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from django.conf import settings
from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Evento, Foto, Mesa, UploadIntent
from .tests import FakeR2Client
from .upload_cleanup import cleanup_upload_intent
from .views import _reservas_upload


class FakeCleanupR2:
    def __init__(self):
        self.lock = Lock()
        self.objects = set()
        self.delete_object = Mock(side_effect=self._delete_object)

    def add(self, key):
        with self.lock:
            self.objects.add(key)

    def _delete_object(self, *, Bucket, Key):
        with self.lock:
            self.objects.discard(Key)
        return {}


@override_settings(UPLOAD_INTENT_CLEANUP_GRACE_SECONDS=15 * 60)
class UploadIntentCleanupTests(TestCase):
    def setUp(self):
        self.event = Evento.objects.create(
            nombre="Evento cleanup",
            fecha=date(2026, 9, 2),
            estado=Evento.Estado.ACTIVE,
        )
        self.table = Mesa.objects.create(evento=self.event, numero=1)

    def create_intent(
        self,
        *,
        estado=UploadIntent.Estado.PENDING,
        expires_at=None,
        object_key=None,
        tamaño_real=None,
    ):
        intent = UploadIntent(
            evento=self.event,
            mesa=self.table,
            nombre_original="foto.jpg",
            content_type_declarado="image/jpeg",
            tamaño_declarado=100,
            tamaño_real=tamaño_real,
            hash_declarado="a" * 64,
            estado=estado,
            expires_at=(
                expires_at
                or timezone.now() - timedelta(minutes=20)
            ),
        )
        intent.object_key = object_key or (
            f"eventos/{self.event.slug}/mesas/{self.table.token}/"
            f"upload-intents/{intent.id}.jpg"
        )
        intent.save()
        return intent

    def run_command(self, *args):
        output = StringIO()
        call_command("cleanup_upload_intents", *args, stdout=output)
        return output.getvalue()

    def create_referencing_photo(self, intent, **overrides):
        fields = {
            "evento": self.event,
            "mesa": self.table,
            "object_key": intent.object_key,
            "nombre_original": "historica.jpg",
            "content_type": "image/jpeg",
            "tamaño": 100,
            "hash_sha256": intent.id.hex * 2,
        }
        fields.update(overrides)
        return Foto.objects.create(**fields)

    def test_unlinked_photo_protects_temporary_key_in_all_cleanup_states(self):
        for estado in (
            UploadIntent.Estado.PENDING,
            UploadIntent.Estado.EXPIRED,
            UploadIntent.Estado.CANCELLED,
            UploadIntent.Estado.CLEANUP_PENDING,
            UploadIntent.Estado.CONFIRMED,
        ):
            with self.subTest(estado=estado):
                intent = self.create_intent(estado=estado)
                historical_photo = self.create_referencing_photo(intent)
                if estado == UploadIntent.Estado.CONFIRMED:
                    intent.final_object_key = (
                        f"eventos/{self.event.slug}/fotos/{intent.id}.jpg"
                    )
                    intent.foto = self.create_referencing_photo(
                        intent,
                        object_key=intent.final_object_key,
                        hash_sha256="f" * 64,
                    )
                    intent.confirmed_at = timezone.now()
                    intent.save()
                self.assertNotEqual(intent.foto_id, historical_photo.pk)
                intent_before = UploadIntent.objects.filter(pk=intent.pk).values().get()
                photo_before = Foto.objects.filter(pk=historical_photo.pk).values().get()
                r2 = FakeCleanupR2()
                r2.add(intent.object_key)
                if intent.final_object_key:
                    r2.add(intent.final_object_key)

                result = cleanup_upload_intent(intent.pk, r2=r2)

                r2.delete_object.assert_not_called()
                self.assertEqual(result, "unsafe_key")
                self.assertIn(intent.object_key, r2.objects)
                intent.refresh_from_db()
                self.assertIsNone(intent.cleaned_at)
                self.assertEqual(
                    UploadIntent.objects.filter(pk=intent.pk).values().get(),
                    intent_before,
                )
                self.assertEqual(
                    Foto.objects.filter(pk=historical_photo.pk).values().get(),
                    photo_before,
                )

    def test_photo_reference_is_protected_even_in_another_event(self):
        intent = self.create_intent()
        other_event = Evento.objects.create(
            nombre="Otro evento con referencia historica",
            fecha=date(2026, 9, 2),
        )
        other_table = Mesa.objects.create(evento=other_event, numero=1)
        photo = self.create_referencing_photo(
            intent, evento=other_event, mesa=other_table,
        )
        r2 = FakeCleanupR2()
        r2.add(intent.object_key)

        result = cleanup_upload_intent(intent.pk, r2=r2)

        self.assertEqual(result, "unsafe_key")
        r2.delete_object.assert_not_called()
        intent.refresh_from_db()
        photo.refresh_from_db()
        self.assertIsNone(intent.cleaned_at)
        self.assertEqual(photo.object_key, intent.object_key)
        self.assertIsNone(photo.eliminada_at)

    def test_deleted_unlinked_photo_does_not_protect_temporary_key(self):
        intent = self.create_intent()
        photo = self.create_referencing_photo(intent, eliminada_at=timezone.now())
        photo_before = Foto.objects.filter(pk=photo.pk).values().get()
        r2 = FakeCleanupR2()
        r2.add(intent.object_key)

        result = cleanup_upload_intent(intent.pk, r2=r2)

        self.assertEqual(result, "cleaned")
        r2.delete_object.assert_called_once_with(
            Bucket=settings.R2_BUCKET_NAME, Key=intent.object_key,
        )
        intent.refresh_from_db()
        self.assertEqual(intent.estado, UploadIntent.Estado.EXPIRED)
        self.assertIsNotNone(intent.cleaned_at)
        self.assertEqual(
            Foto.objects.filter(pk=photo.pk).values().get(), photo_before,
        )

    def test_photo_with_similar_key_does_not_protect_temporary_key(self):
        intent = self.create_intent()
        photo = self.create_referencing_photo(
            intent, object_key=intent.object_key + ".backup",
        )
        r2 = FakeCleanupR2()
        r2.add(intent.object_key)
        r2.add(photo.object_key)

        result = cleanup_upload_intent(intent.pk, r2=r2)

        self.assertEqual(result, "cleaned")
        r2.delete_object.assert_called_once_with(
            Bucket=settings.R2_BUCKET_NAME, Key=intent.object_key,
        )
        self.assertNotIn(intent.object_key, r2.objects)
        self.assertIn(photo.object_key, r2.objects)
        photo.refresh_from_db()
        self.assertIsNone(photo.eliminada_at)

    @patch("eventos.upload_cleanup.get_r2_client")
    def test_dry_run_reports_unlinked_photo_as_unsafe(self, get_r2):
        intent = self.create_intent()
        photo = self.create_referencing_photo(intent)

        output = self.run_command("--dry-run")

        self.assertIn("unsafe_key=1", output)
        get_r2.assert_not_called()
        intent.refresh_from_db()
        photo.refresh_from_db()
        self.assertIsNone(intent.cleaned_at)
        self.assertEqual(intent.estado, UploadIntent.Estado.PENDING)
        self.assertEqual(photo.object_key, intent.object_key)
        self.assertIsNone(photo.eliminada_at)

    @patch("eventos.upload_cleanup.get_r2_client")
    def test_current_pending_is_not_touched(self, get_r2):
        intent = self.create_intent(
            expires_at=timezone.now() + timedelta(minutes=1)
        )

        self.run_command()

        intent.refresh_from_db()
        self.assertIsNone(intent.cleaned_at)
        self.assertEqual(intent.estado, UploadIntent.Estado.PENDING)
        get_r2.assert_not_called()

    @patch("eventos.upload_cleanup.get_r2_client")
    def test_expired_pending_inside_grace_is_not_touched(self, get_r2):
        intent = self.create_intent(
            expires_at=timezone.now() - timedelta(minutes=5)
        )

        self.run_command()

        intent.refresh_from_db()
        self.assertIsNone(intent.cleaned_at)
        self.assertEqual(intent.estado, UploadIntent.Estado.PENDING)
        get_r2.assert_not_called()

    @patch("eventos.upload_cleanup.get_r2_client")
    def test_pending_after_grace_is_cleaned_and_marked_expired(self, get_r2):
        intent = self.create_intent()
        r2 = FakeCleanupR2()
        r2.add(intent.object_key)
        get_r2.return_value = r2

        self.run_command()

        intent.refresh_from_db()
        self.assertIsNotNone(intent.cleaned_at)
        self.assertEqual(intent.estado, UploadIntent.Estado.EXPIRED)
        r2.delete_object.assert_called_once_with(
            Bucket=settings.R2_BUCKET_NAME,
            Key=intent.object_key,
        )

    @patch("eventos.upload_cleanup.get_r2_client")
    def test_terminal_cleanup_states_are_cleaned(self, get_r2):
        r2 = FakeCleanupR2()
        get_r2.return_value = r2

        for index, estado in enumerate(
            (
                UploadIntent.Estado.EXPIRED,
                UploadIntent.Estado.CANCELLED,
                UploadIntent.Estado.CLEANUP_PENDING,
            )
        ):
            with self.subTest(estado=estado):
                intent = self.create_intent(
                    estado=estado,
                    tamaño_real=100,
                )
                intent.hash_declarado = f"{index + 1:064x}"
                intent.save(update_fields=["hash_declarado"])
                r2.add(intent.object_key)

                self.run_command()

                intent.refresh_from_db()
                self.assertEqual(intent.estado, estado)
                self.assertIsNotNone(intent.cleaned_at)
                self.assertNotIn(intent.object_key, r2.objects)

    @patch("eventos.upload_cleanup.get_r2_client")
    def test_confirmed_e1_deletes_only_temporary_key(self, get_r2):
        intent = self.create_intent(estado=UploadIntent.Estado.CONFIRMED)
        intent.final_object_key = (
            f"eventos/{self.event.slug}/fotos/{intent.id}.jpg"
        )
        foto = Foto.objects.create(
            evento=self.event,
            mesa=self.table,
            object_key=intent.final_object_key,
            nombre_original="foto.jpg",
            content_type="image/jpeg",
            tamaño=100,
            hash_sha256=intent.hash_declarado,
        )
        intent.foto = foto
        intent.confirmed_at = timezone.now()
        intent.save(
            update_fields=["final_object_key", "foto", "confirmed_at"]
        )
        r2 = FakeCleanupR2()
        r2.add(intent.object_key)
        r2.add(intent.final_object_key)
        get_r2.return_value = r2

        self.run_command()

        intent.refresh_from_db()
        self.assertEqual(intent.estado, UploadIntent.Estado.CONFIRMED)
        self.assertIsNotNone(intent.cleaned_at)
        self.assertNotIn(intent.object_key, r2.objects)
        self.assertIn(intent.final_object_key, r2.objects)
        self.assertEqual(
            r2.delete_object.call_args.kwargs["Key"],
            intent.object_key,
        )

    @patch("eventos.upload_cleanup.get_r2_client")
    def test_legacy_confirmed_never_deletes_photo_key(self, get_r2):
        intent = self.create_intent(estado=UploadIntent.Estado.CONFIRMED)
        foto = Foto.objects.create(
            evento=self.event,
            mesa=self.table,
            object_key=intent.object_key,
            nombre_original="legacy.jpg",
            content_type="image/jpeg",
            tamaño=100,
            hash_sha256=intent.hash_declarado,
        )
        intent.foto = foto
        intent.confirmed_at = timezone.now()
        intent.save(update_fields=["foto", "confirmed_at"])

        self.run_command()

        intent.refresh_from_db()
        self.assertIsNone(intent.cleaned_at)
        get_r2.assert_not_called()

    @patch("eventos.upload_cleanup.get_r2_client")
    def test_finalizing_is_never_cleaned_automatically(self, get_r2):
        intent = self.create_intent(
            estado=UploadIntent.Estado.FINALIZING,
            tamaño_real=100,
        )
        intent.final_object_key = (
            f"eventos/{self.event.slug}/fotos/{intent.id}.jpg"
        )
        intent.source_etag = '"etag"'
        intent.finalizing_at = timezone.now() - timedelta(days=1)
        intent.save(
            update_fields=[
                "final_object_key",
                "source_etag",
                "finalizing_at",
            ]
        )

        self.run_command()

        intent.refresh_from_db()
        self.assertIsNone(intent.cleaned_at)
        self.assertEqual(intent.estado, UploadIntent.Estado.FINALIZING)
        get_r2.assert_not_called()

    @patch("eventos.upload_cleanup.get_r2_client")
    def test_malformed_key_is_rejected(self, get_r2):
        intent = self.create_intent(
            estado=UploadIntent.Estado.CLEANUP_PENDING,
            object_key="eventos/otro/fotos/no-borrar.jpg",
            tamaño_real=100,
        )

        output = self.run_command()

        intent.refresh_from_db()
        self.assertIsNone(intent.cleaned_at)
        self.assertIn("unsafe_key=1", output)
        get_r2.assert_not_called()

    @patch("eventos.upload_cleanup.get_r2_client")
    def test_no_such_key_is_idempotent_success(self, get_r2):
        intent = self.create_intent(estado=UploadIntent.Estado.EXPIRED)
        get_r2.return_value.delete_object.side_effect = ClientError(
            {
                "Error": {"Code": "NoSuchKey"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "DeleteObject",
        )

        self.run_command()

        intent.refresh_from_db()
        self.assertIsNotNone(intent.cleaned_at)

    @patch("eventos.upload_cleanup.get_r2_client")
    def test_transient_failure_keeps_state_and_quota_for_retry(self, get_r2):
        intent = self.create_intent(
            estado=UploadIntent.Estado.CLEANUP_PENDING,
            tamaño_real=100,
        )
        get_r2.return_value.delete_object.side_effect = RuntimeError(
            "red no disponible"
        )

        self.run_command()

        intent.refresh_from_db()
        self.assertIsNone(intent.cleaned_at)
        self.assertEqual(intent.estado, UploadIntent.Estado.CLEANUP_PENDING)
        cantidad, storage = _reservas_upload(self.event, timezone.now())
        self.assertEqual(cantidad, 1)
        self.assertEqual(storage, 100)

    @patch("eventos.upload_cleanup.get_r2_client")
    def test_error_after_delete_can_retry_safely(self, get_r2):
        intent = self.create_intent(estado=UploadIntent.Estado.EXPIRED)
        r2 = FakeCleanupR2()
        r2.add(intent.object_key)

        def delete_then_fail(*, Bucket, Key):
            r2.objects.discard(Key)
            raise RuntimeError("respuesta perdida")

        r2.delete_object.side_effect = delete_then_fail
        get_r2.return_value = r2

        self.run_command()
        intent.refresh_from_db()
        self.assertIsNone(intent.cleaned_at)

        r2.delete_object.side_effect = r2._delete_object
        self.run_command()

        intent.refresh_from_db()
        self.assertIsNotNone(intent.cleaned_at)

    @patch("eventos.upload_cleanup.get_r2_client")
    def test_dry_run_changes_neither_db_nor_r2(self, get_r2):
        intent = self.create_intent(
            estado=UploadIntent.Estado.CLEANUP_PENDING,
            tamaño_real=100,
        )

        output = self.run_command("--dry-run", "--batch-size", "1")

        intent.refresh_from_db()
        self.assertIsNone(intent.cleaned_at)
        self.assertEqual(intent.estado, UploadIntent.Estado.CLEANUP_PENDING)
        self.assertIn("eligible=1", output)
        get_r2.assert_not_called()

    @patch("eventos.upload_cleanup.get_r2_client")
    def test_quota_is_released_only_after_confirmed_cleanup(self, get_r2):
        intent = self.create_intent(
            estado=UploadIntent.Estado.CLEANUP_PENDING,
            tamaño_real=100,
        )
        r2 = FakeCleanupR2()
        r2.add(intent.object_key)
        get_r2.return_value = r2
        before = _reservas_upload(self.event, timezone.now())

        self.run_command()

        intent.refresh_from_db()
        after = _reservas_upload(self.event, timezone.now())
        self.assertEqual(before, (1, 100))
        self.assertEqual(after, (0, 0))
        self.assertIsNotNone(intent.cleaned_at)

    def test_materialized_orphan_keeps_quota_after_temporary_cleanup(self):
        intent = self.create_intent(
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        intent.final_object_key = (
            f"eventos/{self.event.slug}/fotos/{intent.id}.jpg"
        )
        intent.save(update_fields=["final_object_key"])
        r2 = FakeR2Client()
        r2.add_temporary(intent, size=2048)
        original_copy = r2._copy_object

        def copy_then_create_duplicate(**kwargs):
            result = original_copy(**kwargs)
            Foto.objects.create(
                evento=self.event,
                mesa=self.table,
                object_key="eventos/test/duplicate-existing.jpg",
                nombre_original="duplicate-existing.jpg",
                content_type="image/jpeg",
                tamaño=100,
                hash_sha256=intent.hash_declarado,
            )
            return result

        r2.copy_object.side_effect = copy_then_create_duplicate
        client = Client()
        session = client.session
        session["mesa_id"] = self.table.id
        session["evento_id"] = self.event.id
        session["instrucciones_aceptadas"] = True
        session.save()

        with patch("eventos.views.get_r2_client", return_value=r2):
            response = client.post(
                reverse(
                    "confirmar_subida",
                    args=[self.event.slug, self.table.token],
                ),
                {
                    "upload_intent_id": str(intent.id),
                    "object_key": intent.object_key,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["duplicada"])
        intent.refresh_from_db()
        self.assertEqual(
            intent.estado,
            UploadIntent.Estado.CLEANUP_PENDING,
        )
        self.assertIsNone(intent.foto_id)
        self.assertIsNotNone(intent.finalizing_at)
        self.assertIn(intent.object_key, r2.objects)
        self.assertIn(intent.final_object_key, r2.objects)

        intent.expires_at = timezone.now() - timedelta(minutes=20)
        intent.save(update_fields=["expires_at"])

        def delete_temporary(*, Bucket, Key):
            r2.objects.pop(Key, None)
            return {}

        r2.delete_object.side_effect = delete_temporary
        result = cleanup_upload_intent(intent.pk, r2=r2)

        intent.refresh_from_db()
        cantidad, storage = _reservas_upload(self.event, timezone.now())
        self.assertEqual(result, "cleaned")
        self.assertIsNotNone(intent.cleaned_at)
        self.assertNotIn(intent.object_key, r2.objects)
        self.assertIn(intent.final_object_key, r2.objects)
        self.assertEqual(cantidad, 1)
        self.assertEqual(storage, 2048)


@override_settings(UPLOAD_INTENT_CLEANUP_GRACE_SECONDS=15 * 60)
class UploadIntentCleanupConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.event = Evento.objects.create(
            nombre="Evento cleanup concurrente",
            fecha=date(2026, 9, 2),
            estado=Evento.Estado.ACTIVE,
        )
        self.table = Mesa.objects.create(evento=self.event, numero=1)
        self.intent = UploadIntent(
            evento=self.event,
            mesa=self.table,
            nombre_original="foto.jpg",
            content_type_declarado="image/jpeg",
            tamaño_declarado=100,
            tamaño_real=100,
            hash_declarado="c" * 64,
            estado=UploadIntent.Estado.CLEANUP_PENDING,
            expires_at=timezone.now() - timedelta(minutes=20),
        )
        self.intent.object_key = (
            f"eventos/{self.event.slug}/mesas/{self.table.token}/"
            f"upload-intents/{self.intent.id}.jpg"
        )
        self.intent.save()

    def test_two_workers_only_delete_the_exact_temporary_key(self):
        if connection.vendor != "postgresql":
            self.skipTest("La prueba requiere select_for_update de PostgreSQL.")

        r2 = FakeCleanupR2()
        r2.add(self.intent.object_key)

        def run_worker(_index):
            close_old_connections()
            try:
                return cleanup_upload_intent(self.intent.pk, r2=r2)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run_worker, range(2)))

        self.intent.refresh_from_db()
        self.assertIsNotNone(self.intent.cleaned_at)
        self.assertIn("cleaned", results)
        self.assertTrue(
            all(
                call.kwargs["Key"] == self.intent.object_key
                for call in r2.delete_object.call_args_list
            )
        )
        self.assertNotIn(self.intent.object_key, r2.objects)
