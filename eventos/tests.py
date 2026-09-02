from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Barrier
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.db.models import Sum
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from dateutil.relativedelta import relativedelta

from .limites import (
    MAX_FOTOS_POR_EVENTO,
    MAX_STORAGE_POR_EVENTO,
    MAX_TAMANO_FOTO,
)
from .models import Evento, Foto, Mesa, UploadIntent
from .r2 import UPLOAD_URL_EXPIRATION_SECONDS


class EventTemporalFieldsTests(TestCase):
    def create_event(self, nombre="Evento temporal", **kwargs):
        return Evento.objects.create(
            nombre=nombre,
            fecha=date(2026, 8, 29),
            **kwargs,
        )

    def test_legacy_event_can_keep_temporal_fields_null(self):
        event = self.create_event()

        self.assertIsNone(event.fin_planeado)
        self.assertIsNone(event.timezone)
        self.assertIsNone(event.upload_until)
        self.assertIsNone(event.available_until)

    def test_new_event_can_store_temporal_configuration(self):
        event_timezone = ZoneInfo("America/Mexico_City")
        planned_end = datetime(2026, 8, 29, 23, 0, tzinfo=event_timezone)
        upload_until = planned_end + timedelta(hours=48)
        available_until = planned_end + timedelta(days=182)

        event = self.create_event(
            nombre="Evento configurado",
            fin_planeado=planned_end,
            timezone="America/Mexico_City",
            upload_until=upload_until,
            available_until=available_until,
        )
        event.refresh_from_db()

        self.assertEqual(event.timezone, "America/Mexico_City")
        self.assertEqual(event.fin_planeado, planned_end)
        self.assertEqual(event.upload_until, upload_until)
        self.assertEqual(event.available_until, available_until)

    def test_temporal_datetimes_are_timezone_aware_with_use_tz(self):
        event_timezone = ZoneInfo("America/Mexico_City")
        planned_end = datetime(2026, 8, 29, 23, 0, tzinfo=event_timezone)
        event = self.create_event(
            nombre="Evento con zona horaria",
            fin_planeado=planned_end,
        )
        event.refresh_from_db()

        self.assertTrue(settings.USE_TZ)
        self.assertTrue(timezone.is_aware(event.fin_planeado))
        self.assertEqual(
            timezone.localtime(event.fin_planeado, event_timezone),
            planned_end,
        )


class EventTemporalPolicyTests(TestCase):
    def create_event(self, nombre="Evento temporal", **kwargs):
        estado = kwargs.pop("estado", Evento.Estado.ACTIVE)

        return Evento.objects.create(
            nombre=nombre,
            fecha=date(2026, 8, 29),
            estado=estado,
            **kwargs,
        )

    def create_table(self, event):
        return Mesa.objects.create(evento=event, numero=1)

    def authorize_public_upload(self, table):
        session = self.client.session
        session["mesa_id"] = table.id
        session["evento_id"] = table.evento_id
        session["instrucciones_aceptadas"] = True
        session.save()

    def upload_request_data(self):
        return {
            "nombre": "foto.jpg",
            "content_type": "image/jpeg",
            "hash_sha256": "d" * 64,
            "tamaño": "1024",
        }

    def upload_url(self, event, table):
        return reverse(
            "solicitar_url_subida",
            args=[event.slug, table.token],
        )

    @patch("eventos.views.generar_url_subida", return_value="https://upload.test/")
    def test_active_event_with_null_upload_until_keeps_legacy_upload_access(
        self,
        _generate_url,
    ):
        event = self.create_event()
        table = self.create_table(event)
        self.authorize_public_upload(table)

        response = self.client.post(self.upload_url(event, table), self.upload_request_data())

        self.assertEqual(response.status_code, 200)

    @patch("eventos.views.generar_url_subida", return_value="https://upload.test/")
    def test_active_event_with_future_upload_until_allows_upload(
        self,
        _generate_url,
    ):
        event = self.create_event(upload_until=timezone.now() + timedelta(hours=1))
        table = self.create_table(event)
        self.authorize_public_upload(table)

        response = self.client.post(self.upload_url(event, table), self.upload_request_data())

        self.assertEqual(response.status_code, 200)

    @patch("eventos.views.generar_url_subida", return_value="https://upload.test/")
    def test_valid_size_allows_presign(self, generate_url):
        event = self.create_event()
        table = self.create_table(event)
        self.authorize_public_upload(table)

        response = self.client.post(
            self.upload_url(event, table),
            self.upload_request_data(),
        )

        self.assertEqual(response.status_code, 200)
        generate_url.assert_called_once()

    @patch("eventos.views.generar_url_subida")
    def test_missing_or_invalid_size_rejects_before_presign(self, generate_url):
        event = self.create_event()
        table = self.create_table(event)
        self.authorize_public_upload(table)

        for tamaño in ("", "invalido"):
            with self.subTest(tamaño=tamaño):
                data = self.upload_request_data()
                data["tamaño"] = tamaño

                response = self.client.post(self.upload_url(event, table), data)

                self.assertEqual(response.status_code, 400)
                generate_url.assert_not_called()

    @patch("eventos.views.generar_url_subida")
    def test_zero_or_negative_size_rejects_before_presign(self, generate_url):
        event = self.create_event()
        table = self.create_table(event)
        self.authorize_public_upload(table)

        for tamaño in ("0", "-1"):
            with self.subTest(tamaño=tamaño):
                data = self.upload_request_data()
                data["tamaño"] = tamaño

                response = self.client.post(self.upload_url(event, table), data)

                self.assertEqual(response.status_code, 400)
                generate_url.assert_not_called()

    @patch("eventos.views.generar_url_subida")
    def test_oversized_photo_rejects_before_presign(self, generate_url):
        event = self.create_event()
        table = self.create_table(event)
        self.authorize_public_upload(table)
        data = self.upload_request_data()
        data["tamaño"] = str(MAX_TAMANO_FOTO + 1)

        response = self.client.post(self.upload_url(event, table), data)

        self.assertEqual(response.status_code, 400)
        generate_url.assert_not_called()

    @patch("eventos.views.generar_url_subida")
    def test_event_at_photo_limit_rejects_before_presign(self, generate_url):
        event = self.create_event()
        table = self.create_table(event)
        self.authorize_public_upload(table)
        Foto.objects.bulk_create(
            [
                Foto(
                    evento=event,
                    mesa=table,
                    object_key=f"eventos/test/{index}.jpg",
                    nombre_original=f"{index}.jpg",
                    content_type="image/jpeg",
                    tamaño=1,
                    hash_sha256=f"{index:064x}",
                )
                for index in range(MAX_FOTOS_POR_EVENTO)
            ]
        )

        response = self.client.post(
            self.upload_url(event, table),
            self.upload_request_data(),
        )

        self.assertEqual(response.status_code, 400)
        generate_url.assert_not_called()

    @patch("eventos.views.generar_url_subida")
    def test_storage_limit_rejects_before_presign(self, generate_url):
        event = self.create_event()
        table = self.create_table(event)
        self.authorize_public_upload(table)
        Foto.objects.create(
            evento=event,
            mesa=table,
            object_key="eventos/test/ocupada.jpg",
            nombre_original="ocupada.jpg",
            content_type="image/jpeg",
            tamaño=MAX_STORAGE_POR_EVENTO,
            hash_sha256="f" * 64,
        )

        response = self.client.post(
            self.upload_url(event, table),
            self.upload_request_data(),
        )

        self.assertEqual(response.status_code, 400)
        generate_url.assert_not_called()

    def test_active_event_with_expired_upload_until_blocks_upload_pages(self):
        event = self.create_event(upload_until=timezone.now() - timedelta(seconds=1))
        table = self.create_table(event)

        mesa_response = self.client.get(
            reverse("mesa_publica", args=[event.slug, table.token])
        )
        upload_response = self.client.get(
            reverse("subir_fotos", args=[event.slug, table.token])
        )

        self.assertTemplateUsed(mesa_response, "eventos/evento_cerrado.html")
        self.assertTemplateUsed(upload_response, "eventos/evento_cerrado.html")

    def test_closed_event_with_future_upload_until_blocks_upload(self):
        event = self.create_event(
            estado=Evento.Estado.CLOSED,
            upload_until=timezone.now() + timedelta(hours=1),
        )
        table = self.create_table(event)
        self.authorize_public_upload(table)

        response = self.client.post(self.upload_url(event, table), self.upload_request_data())

        self.assertEqual(response.status_code, 403)

    @patch("eventos.views.generar_url_subida", return_value="https://upload.test/")
    def test_reopening_with_future_upload_until_restores_upload(
        self,
        _generate_url,
    ):
        event = self.create_event(
            estado=Evento.Estado.CLOSED,
            upload_until=timezone.now() + timedelta(hours=1),
        )
        table = self.create_table(event)
        host = User.objects.create_user("host@example.com", password="test-password")
        event.anfitriones.add(host)
        self.client.force_login(host)

        self.client.post(reverse("reabrir_evento", args=[event.slug]))
        event.refresh_from_db()
        self.authorize_public_upload(table)
        response = self.client.post(self.upload_url(event, table), self.upload_request_data())

        self.assertEqual(event.estado, Evento.Estado.ACTIVE)
        self.assertEqual(response.status_code, 200)

    def test_reopening_with_expired_upload_until_keeps_upload_blocked(self):
        event = self.create_event(
            estado=Evento.Estado.CLOSED,
            upload_until=timezone.now() - timedelta(seconds=1),
        )
        table = self.create_table(event)
        host = User.objects.create_user("host@example.com", password="test-password")
        event.anfitriones.add(host)
        self.client.force_login(host)

        self.client.post(reverse("reabrir_evento", args=[event.slug]))
        event.refresh_from_db()
        self.authorize_public_upload(table)
        response = self.client.post(self.upload_url(event, table), self.upload_request_data())

        self.assertEqual(event.estado, Evento.Estado.ACTIVE)
        self.assertEqual(response.status_code, 403)

    @patch("eventos.views.generar_url_subida")
    def test_expired_upload_until_does_not_generate_signed_url(self, generate_url):
        event = self.create_event(upload_until=timezone.now() - timedelta(seconds=1))
        table = self.create_table(event)
        self.authorize_public_upload(table)

        response = self.client.post(self.upload_url(event, table), self.upload_request_data())

        self.assertEqual(response.status_code, 403)
        generate_url.assert_not_called()

    @patch("eventos.views.get_r2_client")
    def test_expired_upload_until_does_not_confirm_photo(self, get_r2_client):
        event = self.create_event(upload_until=timezone.now() - timedelta(seconds=1))
        table = self.create_table(event)
        self.authorize_public_upload(table)

        response = self.client.post(
            reverse("confirmar_subida", args=[event.slug, table.token]),
            {
                "object_key": "eventos/test/foto.jpg",
                "nombre": "foto.jpg",
                "content_type": "image/jpeg",
                "hash_sha256": "e" * 64,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Foto.objects.filter(evento=event).count(), 0)
        get_r2_client.assert_not_called()

    def test_legacy_active_and_closed_albums_remain_visible(self):
        for state in (Evento.Estado.ACTIVE, Evento.Estado.CLOSED):
            with self.subTest(state=state):
                event = self.create_event(nombre=f"Evento legacy {state}", estado=state)

                response = self.client.get(
                    reverse("album_publico", args=[event.slug])
                )

                self.assertEqual(response.status_code, 200)

    def test_active_and_closed_albums_are_visible_before_available_until(self):
        for state in (Evento.Estado.ACTIVE, Evento.Estado.CLOSED):
            with self.subTest(state=state):
                event = self.create_event(
                    nombre=f"Evento vigente {state}",
                    estado=state,
                    available_until=timezone.now() + timedelta(hours=1),
                )

                response = self.client.get(
                    reverse("album_publico", args=[event.slug])
                )

                self.assertEqual(response.status_code, 200)

    def test_active_and_closed_albums_are_hidden_after_available_until(self):
        for state in (Evento.Estado.ACTIVE, Evento.Estado.CLOSED):
            with self.subTest(state=state):
                event = self.create_event(
                    nombre=f"Evento vencido {state}",
                    estado=state,
                    available_until=timezone.now() - timedelta(seconds=1),
                )

                response = self.client.get(
                    reverse("album_publico", args=[event.slug])
                )

                self.assertEqual(response.status_code, 404)

    def test_archived_event_remains_not_public_with_future_available_until(self):
        event = self.create_event(
            estado=Evento.Estado.ARCHIVED,
            available_until=timezone.now() + timedelta(hours=1),
        )

        response = self.client.get(reverse("album_publico", args=[event.slug]))

        self.assertEqual(response.status_code, 404)

    def test_upload_boundary_includes_exact_upload_until(self):
        now = timezone.now()
        event = self.create_event(upload_until=now)

        self.assertTrue(event.permite_carga(now))
        self.assertFalse(event.permite_carga(now + timedelta(microseconds=1)))

    def test_album_boundary_includes_exact_available_until(self):
        now = timezone.now()
        event = self.create_event(available_until=now)

        self.assertTrue(event.permite_album_publico(now))
        self.assertFalse(event.permite_album_publico(now + timedelta(microseconds=1)))


class UploadIntentPresignTests(TestCase):
    def setUp(self):
        self.event = Evento.objects.create(
            nombre="Evento con reservas",
            fecha=date(2026, 9, 2),
            estado=Evento.Estado.ACTIVE,
        )
        self.table = Mesa.objects.create(evento=self.event, numero=1)
        self.authorize_upload()

    def authorize_upload(self, client=None, consent=True):
        client = client or self.client
        session = client.session
        session["mesa_id"] = self.table.id
        session["evento_id"] = self.event.id
        if consent:
            session["instrucciones_aceptadas"] = True
        session.save()

    def upload_url(self, table=None):
        table = table or self.table
        return reverse(
            "solicitar_url_subida",
            args=[self.event.slug, table.token],
        )

    def upload_data(self, hash_sha256="a" * 64, tamaño=1024):
        return {
            "nombre": "foto-invitado.jpg",
            "content_type": "image/jpeg",
            "hash_sha256": hash_sha256,
            "tamaño": str(tamaño),
        }

    def create_pending_intent(self, tamaño, expires_at):
        return UploadIntent.objects.create(
            evento=self.event,
            mesa=self.table,
            object_key=f"eventos/{self.event.slug}/reserva-{UploadIntent.objects.count()}.jpg",
            nombre_original="reserva.jpg",
            content_type_declarado="image/jpeg",
            tamaño_declarado=tamaño,
            hash_declarado="b" * 64,
            expires_at=expires_at,
        )

    def create_photos(self, cantidad):
        Foto.objects.bulk_create(
            [
                Foto(
                    evento=self.event,
                    mesa=self.table,
                    object_key=f"eventos/test/reserva-{index}.jpg",
                    nombre_original=f"reserva-{index}.jpg",
                    content_type="image/jpeg",
                    tamaño=1,
                    hash_sha256=f"{index:064x}",
                )
                for index in range(cantidad)
            ]
        )

    @patch("eventos.views.generar_url_subida", return_value="https://upload.test/")
    def test_valid_presign_creates_pending_intent_and_returns_id(self, generate_url):
        response = self.client.post(self.upload_url(), self.upload_data())

        self.assertEqual(response.status_code, 200)
        intent = UploadIntent.objects.get()
        payload = response.json()
        self.assertEqual(payload["upload_intent_id"], str(intent.id))
        self.assertEqual(payload["object_key"], intent.object_key)
        self.assertEqual(intent.estado, UploadIntent.Estado.PENDING)
        self.assertEqual(intent.evento, self.event)
        self.assertEqual(intent.mesa, self.table)
        self.assertEqual(intent.tamaño_declarado, 1024)
        self.assertIn(str(intent.id), intent.object_key)
        self.assertTrue(
            intent.object_key.startswith(
                f"eventos/{self.event.slug}/mesas/{self.table.token}/upload-intents/"
            )
        )
        self.assertNotIn("foto-invitado", intent.object_key)
        self.assertAlmostEqual(
            (intent.expires_at - intent.created_at).total_seconds(),
            UPLOAD_URL_EXPIRATION_SECONDS,
            delta=2,
        )
        self.assertGreater(intent.expires_at, timezone.now())
        generate_url.assert_called_once_with(
            object_key=intent.object_key,
            content_type="image/jpeg",
        )

    @patch("eventos.views.generar_url_subida", return_value="https://upload.test/")
    def test_each_presign_uses_a_unique_intent_object_key(self, _generate_url):
        first_response = self.client.post(
            self.upload_url(),
            self.upload_data(hash_sha256="c" * 64),
        )
        second_response = self.client.post(
            self.upload_url(),
            self.upload_data(hash_sha256="d" * 64),
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertNotEqual(
            first_response.json()["object_key"],
            second_response.json()["object_key"],
        )
        self.assertEqual(UploadIntent.objects.count(), 2)

    @patch("eventos.views.generar_url_subida", return_value="https://upload.test/")
    def test_reservation_clock_is_read_after_event_lock(self, _generate_url):
        call_order = []
        locked_now = datetime(
            2026,
            9,
            2,
            18,
            0,
            tzinfo=ZoneInfo("UTC"),
        )
        pre_lock_now = locked_now - timedelta(hours=1)
        select_for_update = Evento.objects.select_for_update

        def record_lock(*args, **kwargs):
            call_order.append("lock")
            return select_for_update(*args, **kwargs)

        def record_now():
            call_order.append("now")
            if "lock" in call_order:
                return locked_now
            return pre_lock_now

        with patch.object(
            Evento.objects,
            "select_for_update",
            side_effect=record_lock,
        ), patch("eventos.views.timezone.now", side_effect=record_now):
            response = self.client.post(self.upload_url(), self.upload_data())

        self.assertEqual(response.status_code, 200)
        lock_index = call_order.index("lock")
        self.assertIn("now", call_order[lock_index + 1:])
        intent = UploadIntent.objects.get()
        self.assertEqual(
            intent.expires_at,
            locked_now + timedelta(seconds=UPLOAD_URL_EXPIRATION_SECONDS),
        )

    @patch("eventos.views.generar_url_subida")
    def test_pending_reservation_counts_toward_photo_limit(self, generate_url):
        self.create_photos(MAX_FOTOS_POR_EVENTO - 1)
        self.create_pending_intent(
            tamaño=1,
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        response = self.client.post(self.upload_url(), self.upload_data())

        self.assertEqual(response.status_code, 400)
        self.assertEqual(UploadIntent.objects.count(), 1)
        generate_url.assert_not_called()

    @patch("eventos.views.generar_url_subida")
    def test_pending_reservation_counts_toward_storage_limit(self, generate_url):
        self.create_pending_intent(
            tamaño=MAX_STORAGE_POR_EVENTO - 512,
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        response = self.client.post(
            self.upload_url(),
            self.upload_data(tamaño=1024),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(UploadIntent.objects.count(), 1)
        generate_url.assert_not_called()

    @patch("eventos.views.generar_url_subida", return_value="https://upload.test/")
    def test_expired_intent_does_not_reserve_quota(self, generate_url):
        expired_intent = self.create_pending_intent(
            tamaño=MAX_STORAGE_POR_EVENTO,
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        response = self.client.post(self.upload_url(), self.upload_data())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(expired_intent.estado, UploadIntent.Estado.PENDING)
        self.assertEqual(UploadIntent.objects.count(), 2)
        generate_url.assert_called_once()

    @patch("eventos.views.generar_url_subida", side_effect=RuntimeError("firma fallida"))
    def test_signing_failure_cancels_reservation(self, generate_url):
        response = self.client.post(self.upload_url(), self.upload_data())

        self.assertEqual(response.status_code, 500)
        intent = UploadIntent.objects.get()
        self.assertEqual(intent.estado, UploadIntent.Estado.CANCELLED)
        generate_url.assert_called_once()

    @patch("eventos.views.generar_url_subida")
    def test_closed_or_expired_event_blocks_before_reserving(self, generate_url):
        cases = [
            (Evento.Estado.CLOSED, timezone.now() + timedelta(hours=1)),
            (Evento.Estado.ACTIVE, timezone.now() - timedelta(seconds=1)),
        ]

        for estado, upload_until in cases:
            with self.subTest(estado=estado, upload_until=upload_until):
                self.event.estado = estado
                self.event.upload_until = upload_until
                self.event.save(update_fields=["estado", "upload_until"])

                response = self.client.post(self.upload_url(), self.upload_data())

                self.assertEqual(response.status_code, 403)
                self.assertFalse(UploadIntent.objects.exists())
                generate_url.assert_not_called()

    @patch("eventos.views.generar_url_subida")
    def test_table_session_and_consent_are_required_before_reserving(
        self,
        generate_url,
    ):
        no_session_client = Client()
        no_session_response = no_session_client.post(
            self.upload_url(),
            self.upload_data(),
        )

        no_consent_client = Client()
        self.authorize_upload(client=no_consent_client, consent=False)
        no_consent_response = no_consent_client.post(
            self.upload_url(),
            self.upload_data(),
        )

        inactive_table = Mesa.objects.create(
            evento=self.event,
            numero=2,
            activa=False,
        )
        inactive_client = Client()
        session = inactive_client.session
        session["mesa_id"] = inactive_table.id
        session["evento_id"] = self.event.id
        session["instrucciones_aceptadas"] = True
        session.save()
        inactive_response = inactive_client.post(
            self.upload_url(table=inactive_table),
            self.upload_data(),
        )

        self.assertEqual(no_session_response.status_code, 403)
        self.assertEqual(no_consent_response.status_code, 403)
        self.assertEqual(inactive_response.status_code, 404)
        self.assertFalse(UploadIntent.objects.exists())
        generate_url.assert_not_called()


class UploadIntentConfirmationTests(TestCase):
    def setUp(self):
        self.event = Evento.objects.create(
            nombre="Evento para confirmar",
            fecha=date(2026, 9, 2),
            estado=Evento.Estado.ACTIVE,
        )
        self.table = Mesa.objects.create(evento=self.event, numero=1)
        self.authorize_upload()

    def authorize_upload(self, client=None, table=None):
        client = client or self.client
        table = table or self.table
        session = client.session
        session["mesa_id"] = table.id
        session["evento_id"] = table.evento_id
        session["instrucciones_aceptadas"] = True
        session.save()

    def create_intent(
        self,
        *,
        event=None,
        table=None,
        estado=UploadIntent.Estado.PENDING,
        tamaño=1024,
        hash_declarado="a" * 64,
        expires_at=None,
    ):
        event = event or self.event
        table = table or self.table
        intent = UploadIntent(
            evento=event,
            mesa=table,
            nombre_original="foto-original.jpg",
            content_type_declarado="image/jpeg",
            tamaño_declarado=tamaño,
            hash_declarado=hash_declarado,
            estado=estado,
            expires_at=(
                expires_at
                or timezone.now() + timedelta(minutes=5)
            ),
        )
        intent.object_key = (
            f"eventos/{event.slug}/mesas/{table.token}/"
            f"upload-intents/{intent.id}.jpg"
        )
        intent.save()
        return intent

    def confirmation_url(self):
        return reverse(
            "confirmar_subida",
            args=[self.event.slug, self.table.token],
        )

    def confirmation_data(self, intent, **overrides):
        data = {
            "upload_intent_id": str(intent.id),
            "object_key": intent.object_key,
            "nombre": "nombre-cliente.jpg",
            "content_type": "image/png",
            "hash_sha256": "f" * 64,
            "tamaño": "1",
        }
        data.update(overrides)
        return data

    @patch("eventos.views.get_r2_client")
    def test_valid_confirmation_creates_photo_and_confirms_intent(self, get_r2):
        intent = self.create_intent()
        get_r2.return_value.head_object.return_value = {
            "ContentLength": 2048,
        }

        response = self.client.post(
            self.confirmation_url(),
            self.confirmation_data(intent),
        )

        self.assertEqual(response.status_code, 200)
        intent.refresh_from_db()
        self.assertEqual(intent.estado, UploadIntent.Estado.CONFIRMED)
        self.assertIsNotNone(intent.foto_id)
        self.assertIsNotNone(intent.confirmed_at)
        self.assertEqual(intent.tamaño_real, 2048)
        self.assertEqual(Foto.objects.count(), 1)

    @patch("eventos.views.get_r2_client")
    def test_confirmation_uses_head_size_and_intent_metadata(self, get_r2):
        intent = self.create_intent(
            tamaño=100,
            hash_declarado="b" * 64,
        )
        get_r2.return_value.head_object.return_value = {
            "ContentLength": 4096,
        }

        response = self.client.post(
            self.confirmation_url(),
            self.confirmation_data(
                intent,
                nombre="manipulado.png",
                content_type="image/png",
                hash_sha256="c" * 64,
                tamaño="999999",
            ),
        )

        self.assertEqual(response.status_code, 200)
        foto = Foto.objects.get()
        self.assertEqual(foto.object_key, intent.object_key)
        self.assertEqual(foto.nombre_original, intent.nombre_original)
        self.assertEqual(foto.content_type, intent.content_type_declarado)
        self.assertEqual(foto.hash_sha256, intent.hash_declarado)
        self.assertEqual(foto.tamaño, 4096)

    @patch("eventos.views.get_r2_client")
    def test_oversized_real_object_marks_cleanup_pending(self, get_r2):
        intent = self.create_intent()
        get_r2.return_value.head_object.return_value = {
            "ContentLength": MAX_TAMANO_FOTO + 1,
        }

        response = self.client.post(
            self.confirmation_url(),
            self.confirmation_data(intent),
        )

        self.assertEqual(response.status_code, 400)
        intent.refresh_from_db()
        self.assertEqual(intent.estado, UploadIntent.Estado.CLEANUP_PENDING)
        self.assertEqual(intent.tamaño_real, MAX_TAMANO_FOTO + 1)
        self.assertFalse(Foto.objects.exists())
        get_r2.return_value.delete_object.assert_not_called()

    @patch("eventos.views.get_r2_client")
    def test_real_storage_overage_marks_cleanup_pending(self, get_r2):
        intent = self.create_intent(tamaño=100)
        Foto.objects.create(
            evento=self.event,
            mesa=self.table,
            object_key="eventos/test/storage-existente.jpg",
            nombre_original="storage-existente.jpg",
            content_type="image/jpeg",
            tamaño=MAX_STORAGE_POR_EVENTO - 500,
            hash_sha256="d" * 64,
        )
        get_r2.return_value.head_object.return_value = {
            "ContentLength": 1000,
        }

        response = self.client.post(
            self.confirmation_url(),
            self.confirmation_data(intent),
        )

        self.assertEqual(response.status_code, 400)
        intent.refresh_from_db()
        self.assertEqual(intent.estado, UploadIntent.Estado.CLEANUP_PENDING)
        self.assertEqual(Foto.objects.count(), 1)

    @patch("eventos.views.get_r2_client")
    def test_intent_from_other_table_or_event_is_rejected_without_head(self, get_r2):
        other_table = Mesa.objects.create(evento=self.event, numero=2)
        other_table_intent = self.create_intent(table=other_table)
        other_event = Evento.objects.create(
            nombre="Otro evento",
            fecha=date(2026, 9, 2),
            estado=Evento.Estado.ACTIVE,
        )
        other_event_table = Mesa.objects.create(evento=other_event, numero=1)
        other_event_intent = self.create_intent(
            event=other_event,
            table=other_event_table,
            hash_declarado="e" * 64,
        )

        for intent in (other_table_intent, other_event_intent):
            with self.subTest(intent=intent.id):
                response = self.client.post(
                    self.confirmation_url(),
                    self.confirmation_data(intent),
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json()["error"],
                    "Intento de subida no válido.",
                )

        self.assertFalse(Foto.objects.exists())
        get_r2.return_value.head_object.assert_not_called()

    @patch("eventos.views.get_r2_client")
    def test_cancelled_intent_cannot_be_confirmed(self, get_r2):
        intent = self.create_intent(estado=UploadIntent.Estado.CANCELLED)

        response = self.client.post(
            self.confirmation_url(),
            self.confirmation_data(intent),
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(Foto.objects.exists())
        get_r2.return_value.head_object.assert_not_called()

    @patch("eventos.views.get_r2_client")
    def test_expired_pending_intent_is_marked_expired(self, get_r2):
        intent = self.create_intent(
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        response = self.client.post(
            self.confirmation_url(),
            self.confirmation_data(intent),
        )

        self.assertEqual(response.status_code, 410)
        intent.refresh_from_db()
        self.assertEqual(intent.estado, UploadIntent.Estado.EXPIRED)
        self.assertFalse(Foto.objects.exists())
        get_r2.return_value.head_object.assert_not_called()

    @patch("eventos.views.get_r2_client")
    def test_repeated_confirmation_is_idempotent(self, get_r2):
        intent = self.create_intent()
        get_r2.return_value.head_object.return_value = {
            "ContentLength": 2048,
        }

        first_response = self.client.post(
            self.confirmation_url(),
            self.confirmation_data(intent),
        )
        second_response = self.client.post(
            self.confirmation_url(),
            self.confirmation_data(intent),
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(
            first_response.json()["foto_id"],
            second_response.json()["foto_id"],
        )
        self.assertEqual(Foto.objects.count(), 1)
        self.assertEqual(get_r2.return_value.head_object.call_count, 1)

    @patch("eventos.views.get_r2_client")
    def test_confirmed_intent_remains_idempotent_after_event_closes(self, get_r2):
        intent = self.create_intent()
        get_r2.return_value.head_object.return_value = {
            "ContentLength": 2048,
        }
        first_response = self.client.post(
            self.confirmation_url(),
            self.confirmation_data(intent),
        )
        self.event.estado = Evento.Estado.CLOSED
        self.event.save(update_fields=["estado"])

        second_response = self.client.post(
            self.confirmation_url(),
            self.confirmation_data(intent),
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(
            first_response.json()["foto_id"],
            second_response.json()["foto_id"],
        )
        self.assertEqual(Foto.objects.count(), 1)
        self.assertEqual(get_r2.return_value.head_object.call_count, 1)

    @patch("eventos.views.get_r2_client")
    def test_head_failure_keeps_intent_pending_without_photo(self, get_r2):
        intent = self.create_intent()
        get_r2.return_value.head_object.side_effect = RuntimeError("R2 no disponible")

        response = self.client.post(
            self.confirmation_url(),
            self.confirmation_data(intent),
        )

        self.assertEqual(response.status_code, 400)
        intent.refresh_from_db()
        self.assertEqual(intent.estado, UploadIntent.Estado.PENDING)
        self.assertFalse(Foto.objects.exists())

    @patch("eventos.views.get_r2_client")
    def test_mismatched_client_object_key_is_rejected(self, get_r2):
        intent = self.create_intent()

        response = self.client.post(
            self.confirmation_url(),
            self.confirmation_data(intent, object_key="eventos/ajeno/foto.jpg"),
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Foto.objects.exists())
        get_r2.return_value.head_object.assert_not_called()

    @patch("eventos.views.get_r2_client")
    def test_legacy_d1_photo_is_linked_instead_of_duplicated(self, get_r2):
        intent = self.create_intent()
        legacy_photo = Foto.objects.create(
            evento=self.event,
            mesa=self.table,
            object_key=intent.object_key,
            nombre_original=intent.nombre_original,
            content_type=intent.content_type_declarado,
            tamaño=2048,
            hash_sha256=intent.hash_declarado,
        )
        get_r2.return_value.head_object.return_value = {
            "ContentLength": 2048,
        }

        response = self.client.post(
            self.confirmation_url(),
            self.confirmation_data(intent),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["foto_id"], legacy_photo.id)
        intent.refresh_from_db()
        self.assertEqual(intent.estado, UploadIntent.Estado.CONFIRMED)
        self.assertEqual(intent.foto_id, legacy_photo.id)
        self.assertEqual(Foto.objects.count(), 1)


class UploadIntentConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.event = Evento.objects.create(
            nombre="Evento concurrente",
            fecha=date(2026, 9, 2),
            estado=Evento.Estado.ACTIVE,
        )
        self.table = Mesa.objects.create(evento=self.event, numero=1)
        Foto.objects.bulk_create(
            [
                Foto(
                    evento=self.event,
                    mesa=self.table,
                    object_key=f"eventos/test/concurrente-{index}.jpg",
                    nombre_original=f"concurrente-{index}.jpg",
                    content_type="image/jpeg",
                    tamaño=1,
                    hash_sha256=f"{index:064x}",
                )
                for index in range(MAX_FOTOS_POR_EVENTO - 1)
            ]
        )

    def authorized_client(self):
        client = Client()
        session = client.session
        session["mesa_id"] = self.table.id
        session["evento_id"] = self.event.id
        session["instrucciones_aceptadas"] = True
        session.save()
        return client

    @patch("eventos.views.generar_url_subida", return_value="https://upload.test/")
    def test_concurrent_presigns_reserve_only_remaining_photo(self, generate_url):
        if connection.vendor != "postgresql":
            self.skipTest("La prueba requiere select_for_update de PostgreSQL.")

        url = reverse(
            "solicitar_url_subida",
            args=[self.event.slug, self.table.token],
        )
        barrier = Barrier(2)
        clients = [self.authorized_client(), self.authorized_client()]

        def request_presign(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return clients[index].post(
                    url,
                    {
                        "nombre": f"foto-{index}.jpg",
                        "content_type": "image/jpeg",
                        "hash_sha256": str(index + 1) * 64,
                        "tamaño": "1024",
                    },
                ).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(request_presign, range(2)))

        self.assertCountEqual(statuses, [200, 400])
        self.assertEqual(
            UploadIntent.objects.filter(
                estado=UploadIntent.Estado.PENDING,
            ).count(),
            1,
        )
        self.assertEqual(generate_url.call_count, 1)


class UploadIntentConfirmationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.event = Evento.objects.create(
            nombre="Confirmación concurrente",
            fecha=date(2026, 9, 2),
            estado=Evento.Estado.ACTIVE,
        )
        self.table = Mesa.objects.create(evento=self.event, numero=1)

    def create_intent(self, suffix, tamaño=1024):
        intent = UploadIntent(
            evento=self.event,
            mesa=self.table,
            nombre_original=f"foto-{suffix}.jpg",
            content_type_declarado="image/jpeg",
            tamaño_declarado=tamaño,
            hash_declarado=(suffix * 64)[:64],
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        intent.object_key = (
            f"eventos/{self.event.slug}/mesas/{self.table.token}/"
            f"upload-intents/{intent.id}.jpg"
        )
        intent.save()
        return intent

    def authorized_client(self):
        client = Client()
        session = client.session
        session["mesa_id"] = self.table.id
        session["evento_id"] = self.event.id
        session["instrucciones_aceptadas"] = True
        session.save()
        return client

    def confirmation_url(self):
        return reverse(
            "confirmar_subida",
            args=[self.event.slug, self.table.token],
        )

    def concurrent_confirmations(self, intents):
        barrier = Barrier(len(intents))
        clients = [self.authorized_client() for _intent in intents]
        url = self.confirmation_url()

        def confirm(index):
            close_old_connections()
            try:
                intent = intents[index]
                barrier.wait(timeout=10)
                response = clients[index].post(
                    url,
                    {
                        "upload_intent_id": str(intent.id),
                        "object_key": intent.object_key,
                    },
                )
                return response.status_code, response.json()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(intents)) as executor:
            return list(executor.map(confirm, range(len(intents))))

    @patch("eventos.views.get_r2_client")
    def test_two_confirmations_of_same_intent_create_one_photo(self, get_r2):
        if connection.vendor != "postgresql":
            self.skipTest("La prueba requiere select_for_update de PostgreSQL.")

        intent = self.create_intent("a")
        get_r2.return_value.head_object.return_value = {
            "ContentLength": 1024,
        }

        responses = self.concurrent_confirmations([intent, intent])

        self.assertEqual([status for status, _payload in responses], [200, 200])
        self.assertEqual(Foto.objects.count(), 1)
        self.assertEqual(
            responses[0][1]["foto_id"],
            responses[1][1]["foto_id"],
        )
        intent.refresh_from_db()
        self.assertEqual(intent.estado, UploadIntent.Estado.CONFIRMED)

    @patch("eventos.views.get_r2_client")
    def test_two_intents_near_photo_limit_never_exceed_limit(self, get_r2):
        if connection.vendor != "postgresql":
            self.skipTest("La prueba requiere select_for_update de PostgreSQL.")

        Foto.objects.bulk_create(
            [
                Foto(
                    evento=self.event,
                    mesa=self.table,
                    object_key=f"eventos/test/foto-limite-{index}.jpg",
                    nombre_original=f"foto-limite-{index}.jpg",
                    content_type="image/jpeg",
                    tamaño=1,
                    hash_sha256=f"{index:064x}",
                )
                for index in range(MAX_FOTOS_POR_EVENTO - 1)
            ]
        )
        intents = [self.create_intent("a"), self.create_intent("b")]
        get_r2.return_value.head_object.return_value = {
            "ContentLength": 1,
        }

        responses = self.concurrent_confirmations(intents)

        self.assertCountEqual(
            [status for status, _payload in responses],
            [200, 400],
        )
        self.assertEqual(Foto.objects.count(), MAX_FOTOS_POR_EVENTO)
        self.assertLessEqual(Foto.objects.count(), MAX_FOTOS_POR_EVENTO)
        self.assertEqual(
            UploadIntent.objects.filter(
                estado=UploadIntent.Estado.CONFIRMED,
            ).count(),
            1,
        )
        self.assertEqual(
            UploadIntent.objects.filter(
                estado=UploadIntent.Estado.CLEANUP_PENDING,
            ).count(),
            1,
        )

    @patch("eventos.views.get_r2_client")
    def test_concurrent_real_sizes_never_exceed_storage_limit(self, get_r2):
        if connection.vendor != "postgresql":
            self.skipTest("La prueba requiere select_for_update de PostgreSQL.")

        Foto.objects.create(
            evento=self.event,
            mesa=self.table,
            object_key="eventos/test/storage-concurrente.jpg",
            nombre_original="storage-concurrente.jpg",
            content_type="image/jpeg",
            tamaño=MAX_STORAGE_POR_EVENTO - 1500,
            hash_sha256="f" * 64,
        )
        intents = [
            self.create_intent("a", tamaño=500),
            self.create_intent("b", tamaño=500),
        ]
        get_r2.return_value.head_object.return_value = {
            "ContentLength": 1000,
        }

        responses = self.concurrent_confirmations(intents)
        almacenamiento = (
            Foto.objects
            .filter(eliminada_at__isnull=True)
            .aggregate(total=Sum("tamaño"))["total"]
            or 0
        )

        self.assertCountEqual(
            [status for status, _payload in responses],
            [200, 400],
        )
        self.assertLessEqual(almacenamiento, MAX_STORAGE_POR_EVENTO)
        self.assertEqual(
            UploadIntent.objects.filter(
                estado=UploadIntent.Estado.CONFIRMED,
            ).count(),
            1,
        )
        self.assertEqual(
            UploadIntent.objects.filter(
                estado=UploadIntent.Estado.CLEANUP_PENDING,
            ).count(),
            1,
        )


class EventTemporalMaterializationTests(TestCase):
    event_timezone_name = "America/Mexico_City"
    event_timezone = ZoneInfo(event_timezone_name)

    def create_event(self, nombre="Evento materializado"):
        return Evento.objects.create(
            nombre=nombre,
            fecha=date(2026, 8, 29),
            timezone=self.event_timezone_name,
        )

    def planned_end(self, year=2026, month=8, day=29):
        return datetime(
            year,
            month,
            day,
            23,
            0,
            tzinfo=self.event_timezone,
        )

    def test_materialization_sets_upload_until_to_48_hours_after_planned_end(self):
        event = self.create_event()
        planned_end = self.planned_end()

        event.materializar_ciclo_temporal(planned_end, meses_disponibilidad=6)

        self.assertEqual(event.fin_planeado, planned_end)
        self.assertEqual(event.upload_until, planned_end + timedelta(hours=48))

    def test_materialization_sets_six_calendar_months_of_availability(self):
        event = self.create_event()
        planned_end = self.planned_end()

        event.materializar_ciclo_temporal(planned_end, meses_disponibilidad=6)

        self.assertEqual(
            event.available_until,
            planned_end + relativedelta(months=6),
        )

    def test_materialization_sets_twelve_calendar_months_of_availability(self):
        event = self.create_event()
        planned_end = self.planned_end()

        event.materializar_ciclo_temporal(planned_end, meses_disponibilidad=12)

        self.assertEqual(
            event.available_until,
            planned_end + relativedelta(months=12),
        )

    def test_materialization_uses_calendar_month_end_rules(self):
        event = self.create_event()
        august_31 = self.planned_end(day=31)

        event.materializar_ciclo_temporal(august_31, meses_disponibilidad=6)

        self.assertEqual(
            event.available_until,
            datetime(2027, 2, 28, 23, 0, tzinfo=self.event_timezone),
        )

        january_31 = self.planned_end(year=2027, month=1, day=31)
        event.materializar_ciclo_temporal(january_31, meses_disponibilidad=1)

        self.assertEqual(
            event.available_until,
            datetime(2027, 2, 28, 23, 0, tzinfo=self.event_timezone),
        )

    def test_materialization_preserves_timezone_aware_local_context(self):
        event = self.create_event()
        planned_end = self.planned_end()

        event.materializar_ciclo_temporal(planned_end, meses_disponibilidad=6)
        event.save()
        event.refresh_from_db()

        self.assertTrue(timezone.is_aware(event.fin_planeado))
        self.assertEqual(
            timezone.localtime(event.fin_planeado, self.event_timezone),
            planned_end,
        )
        self.assertEqual(
            timezone.localtime(event.available_until, self.event_timezone),
            planned_end + relativedelta(months=6),
        )

    def test_direct_planned_end_change_does_not_recalculate_dates(self):
        event = self.create_event()
        original_end = self.planned_end()
        event.materializar_ciclo_temporal(original_end, meses_disponibilidad=6)
        event.save()
        original_upload_until = event.upload_until
        original_available_until = event.available_until

        event.fin_planeado = self.planned_end(month=9, day=5)
        event.save(update_fields=["fin_planeado"])
        event.refresh_from_db()

        self.assertEqual(event.upload_until, original_upload_until)
        self.assertEqual(event.available_until, original_available_until)

    def test_explicit_materialization_recalculates_dates(self):
        event = self.create_event()
        original_end = self.planned_end()
        event.materializar_ciclo_temporal(original_end, meses_disponibilidad=6)
        event.save()
        revised_end = self.planned_end(month=9, day=5)

        event.materializar_ciclo_temporal(revised_end, meses_disponibilidad=12)

        self.assertEqual(event.fin_planeado, revised_end)
        self.assertEqual(event.upload_until, revised_end + timedelta(hours=48))
        self.assertEqual(
            event.available_until,
            revised_end + relativedelta(months=12),
        )

    def test_reopening_event_does_not_recalculate_materialized_dates(self):
        event = self.create_event()
        planned_end = self.planned_end()
        event.materializar_ciclo_temporal(planned_end, meses_disponibilidad=6)
        event.estado = Evento.Estado.CLOSED
        event.save()
        original_upload_until = event.upload_until
        original_available_until = event.available_until
        host = User.objects.create_user("host@example.com", password="test-password")
        event.anfitriones.add(host)
        self.client.force_login(host)

        response = self.client.post(reverse("reabrir_evento", args=[event.slug]))
        event.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(event.upload_until, original_upload_until)
        self.assertEqual(event.available_until, original_available_until)

    def test_legacy_event_remains_valid_without_materialization(self):
        event = Evento.objects.create(
            nombre="Evento legacy",
            fecha=date(2026, 8, 29),
        )

        self.assertIsNone(event.fin_planeado)
        self.assertIsNone(event.upload_until)
        self.assertIsNone(event.available_until)

    def test_materialization_rejects_missing_or_naive_planned_end(self):
        event = self.create_event()

        with self.assertRaises(ValidationError):
            event.materializar_ciclo_temporal(None, meses_disponibilidad=6)

        with self.assertRaises(ValidationError):
            event.materializar_ciclo_temporal(
                datetime(2026, 8, 29, 23, 0),
                meses_disponibilidad=6,
            )

    def test_materialization_rejects_non_positive_duration(self):
        event = self.create_event()
        planned_end = self.planned_end()

        for months in (0, -1):
            with self.subTest(months=months):
                with self.assertRaises(ValidationError):
                    event.materializar_ciclo_temporal(
                        planned_end,
                        meses_disponibilidad=months,
                    )


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class EventTemporalDashboardTests(TestCase):
    event_timezone_name = "America/Mexico_City"
    event_timezone = ZoneInfo(event_timezone_name)

    def setUp(self):
        self.host = User.objects.create_user(
            "host@example.com",
            password="test-password",
        )
        self.other_host = User.objects.create_user(
            "other@example.com",
            password="test-password",
        )
        self.superuser = User.objects.create_superuser(
            "admin@example.com",
            "admin@example.com",
            "test-password",
        )
        self.event = Evento.objects.create(
            nombre="Evento temporal dashboard",
            fecha=date(2026, 8, 29),
            timezone=self.event_timezone_name,
        )
        self.event.anfitriones.add(self.host)
        self.planned_end = datetime(
            2026,
            8,
            29,
            23,
            0,
            tzinfo=self.event_timezone,
        )
        self.event.materializar_ciclo_temporal(
            self.planned_end,
            meses_disponibilidad=6,
        )
        self.event.save()

    def dashboard_url(self, event=None):
        return reverse("dashboard_evento", args=[(event or self.event).slug])

    def test_authorized_host_can_view_temporal_configuration(self):
        self.client.force_login(self.host)

        response = self.client.get(self.dashboard_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ciclo temporal")
        self.assertContains(response, "29/08/2026, 23:00")
        self.assertContains(response, "31/08/2026, 23:00")
        self.assertContains(response, "28/02/2027, 23:00")
        self.assertContains(response, self.event_timezone_name)

    def test_unrelated_host_cannot_modify_temporal_configuration(self):
        self.client.force_login(self.other_host)
        original_available_until = self.event.available_until

        response = self.client.post(
            self.dashboard_url(),
            {
                "fin_planeado": "2026-09-05T23:00",
                "timezone": self.event_timezone_name,
            },
        )

        self.assertEqual(response.status_code, 404)
        self.event.refresh_from_db()
        self.assertEqual(self.event.available_until, original_available_until)

    def test_superuser_can_view_temporal_configuration(self):
        self.client.force_login(self.superuser)

        response = self.client.get(self.dashboard_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ciclo temporal")

    def test_legacy_event_renders_unconfigured_temporal_values(self):
        legacy_event = Evento.objects.create(
            nombre="Evento legacy dashboard",
            fecha=date(2026, 8, 29),
        )
        legacy_event.anfitriones.add(self.host)
        self.client.force_login(self.host)

        response = self.client.get(self.dashboard_url(legacy_event))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No configurado")

    def test_temporal_configuration_updates_upload_without_changing_availability(self):
        self.client.force_login(self.host)
        original_available_until = self.event.available_until

        response = self.client.post(
            self.dashboard_url(),
            {
                "fin_planeado": "2026-09-05T23:00",
                "timezone": self.event_timezone_name,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        revised_end = datetime(
            2026,
            9,
            5,
            23,
            0,
            tzinfo=self.event_timezone,
        )
        self.assertEqual(self.event.fin_planeado, revised_end)
        self.assertEqual(
            self.event.upload_until,
            revised_end + timedelta(hours=48),
        )
        self.assertEqual(self.event.available_until, original_available_until)

    def test_temporal_form_rejects_invalid_timezone(self):
        self.client.force_login(self.host)

        response = self.client.post(
            self.dashboard_url(),
            {
                "fin_planeado": "2026-09-05T23:00",
                "timezone": "Not/AZone",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ingresa una zona horaria IANA válida.")


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class EventAuthorizationTests(TestCase):
    """Security regression tests for the current event access policy."""

    def setUp(self):
        self.host = User.objects.create_user(
            username="host@example.com",
            password="test-password",
        )
        self.other_host = User.objects.create_user(
            username="other@example.com",
            password="test-password",
        )
        self.superuser = User.objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="test-password",
        )
        self.event = self.create_event("Evento autorizado")
        self.other_event = self.create_event("Evento ajeno")
        self.event.anfitriones.add(self.host)
        self.other_event.anfitriones.add(self.other_host)
        self.table = Mesa.objects.create(evento=self.event, numero=1)
        self.other_table = Mesa.objects.create(
            evento=self.other_event,
            numero=1,
        )
        self.photo = Foto.objects.create(
            evento=self.event,
            mesa=self.table,
            object_key="eventos/evento-autorizado/mesas/mesa/foto.jpg",
            nombre_original="foto.jpg",
            content_type="image/jpeg",
            tamaño=1024,
            hash_sha256="a" * 64,
        )
        self.other_photo = Foto.objects.create(
            evento=self.other_event,
            mesa=self.other_table,
            object_key="eventos/evento-ajeno/mesas/mesa/foto.jpg",
            nombre_original="foto-ajena.jpg",
            content_type="image/jpeg",
            tamaño=1024,
            hash_sha256="b" * 64,
        )

    def create_event(self, nombre, estado=Evento.Estado.ACTIVE):
        return Evento.objects.create(
            nombre=nombre,
            fecha=date(2026, 9, 1),
            estado=estado,
        )

    def login_as(self, user):
        self.client.force_login(user)

    def authorize_public_upload(self, mesa):
        session = self.client.session
        session["mesa_id"] = mesa.id
        session["evento_id"] = mesa.evento_id
        session["instrucciones_aceptadas"] = True
        session.save()

    def upload_request_data(self):
        return {
            "nombre": "foto.jpg",
            "content_type": "image/jpeg",
            "hash_sha256": "c" * 64,
            "tamaño": "1024",
        }

    def test_host_can_access_assigned_event_dashboard(self):
        self.login_as(self.host)

        response = self.client.get(
            reverse("dashboard_evento", args=[self.event.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["evento"], self.event)

    def test_unrelated_host_cannot_access_other_event_dashboard(self):
        self.login_as(self.other_host)

        response = self.client.get(
            reverse("dashboard_evento", args=[self.event.slug])
        )

        self.assertEqual(response.status_code, 404)

    def test_superuser_can_access_any_event_dashboard(self):
        self.login_as(self.superuser)

        response = self.client.get(
            reverse("dashboard_evento", args=[self.event.slug])
        )

        self.assertEqual(response.status_code, 200)

    def test_event_dashboard_returns_404_for_missing_event(self):
        self.login_as(self.host)

        response = self.client.get(
            reverse("dashboard_evento", args=["evento-inexistente"])
        )

        self.assertEqual(response.status_code, 404)

    def test_assigned_host_can_generate_table_qr(self):
        self.login_as(self.host)

        response = self.client.get(
            reverse("qr_mesa", args=[self.event.slug, self.table.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_superuser_can_generate_any_table_qr(self):
        self.login_as(self.superuser)

        response = self.client.get(
            reverse("qr_mesa", args=[self.event.slug, self.table.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_unrelated_host_cannot_generate_other_event_table_qr(self):
        self.login_as(self.other_host)

        response = self.client.get(
            reverse("qr_mesa", args=[self.event.slug, self.table.id])
        )

        self.assertEqual(response.status_code, 404)

    def test_anonymous_user_cannot_generate_table_qr(self):
        response = self.client.get(
            reverse("qr_mesa", args=[self.event.slug, self.table.id])
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/login/?next="))

    def test_qr_generation_returns_404_for_missing_event(self):
        self.login_as(self.host)

        response = self.client.get(
            reverse("qr_mesa", args=["evento-inexistente", self.table.id])
        )

        self.assertEqual(response.status_code, 404)

    def test_qr_generation_returns_404_for_missing_table(self):
        self.login_as(self.host)

        response = self.client.get(
            reverse("qr_mesa", args=[self.event.slug, 999999])
        )

        self.assertEqual(response.status_code, 404)

    def test_assigned_host_can_administer_event_tables(self):
        self.login_as(self.host)

        response = self.client.post(
            reverse("configurar_mesas", args=[self.event.slug]),
            {"numero_mesas": 2},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_mesas"], 2)

    def test_unrelated_host_cannot_administer_other_event_tables(self):
        self.login_as(self.host)

        response = self.client.post(
            reverse(
                "actualizar_mesa",
                args=[self.other_event.slug, self.other_table.id],
            ),
            {"nombre": "No autorizado"},
        )

        self.assertEqual(response.status_code, 404)
        self.other_table.refresh_from_db()
        self.assertEqual(self.other_table.nombre, "")

    @patch("eventos.views.generar_url_lectura", return_value="https://read.test/")
    def test_assigned_host_can_access_event_photos_dashboard(self, _generate_url):
        self.login_as(self.host)

        response = self.client.get(
            reverse("fotos_dashboard", args=[self.event.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.photo.nombre_original)

    def test_unrelated_host_cannot_delete_other_event_photo(self):
        self.login_as(self.host)

        response = self.client.post(
            reverse(
                "eliminar_foto_dashboard",
                args=[self.other_event.slug, self.other_photo.id],
            )
        )

        self.assertEqual(response.status_code, 404)
        self.other_photo.refresh_from_db()
        self.assertIsNone(self.other_photo.eliminada_at)

    def test_assigned_host_can_delete_event_photo(self):
        self.login_as(self.host)

        with patch("eventos.views.eliminar_objeto") as delete_object:
            response = self.client.post(
                reverse(
                    "eliminar_foto_dashboard",
                    args=[self.event.slug, self.photo.id],
                )
            )

        self.assertEqual(response.status_code, 200)
        delete_object.assert_called_once_with(self.photo.object_key)
        self.photo.refresh_from_db()
        self.assertIsNotNone(self.photo.eliminada_at)

    def test_unrelated_host_cannot_download_other_event_photos(self):
        self.login_as(self.host)

        response = self.client.get(
            reverse("descargar_fotos_evento", args=[self.other_event.slug])
        )

        self.assertEqual(response.status_code, 404)

    def test_unrelated_host_cannot_change_other_event_state(self):
        self.login_as(self.host)

        response = self.client.post(
            reverse("cerrar_evento", args=[self.other_event.slug])
        )

        self.assertEqual(response.status_code, 404)
        self.other_event.refresh_from_db()
        self.assertEqual(self.other_event.estado, Evento.Estado.ACTIVE)

    @patch("eventos.views.generar_url_subida", return_value="https://upload.test/")
    def test_active_event_allows_authorized_upload_request(self, _generate_url):
        self.authorize_public_upload(self.table)

        response = self.client.post(
            reverse(
                "solicitar_url_subida",
                args=[self.event.slug, self.table.token],
            ),
            self.upload_request_data(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["url"], "https://upload.test/")

    def test_closed_event_blocks_new_upload_requests(self):
        self.event.estado = Evento.Estado.CLOSED
        self.event.save(update_fields=["estado"])
        self.authorize_public_upload(self.table)

        response = self.client.post(
            reverse(
                "solicitar_url_subida",
                args=[self.event.slug, self.table.token],
            ),
            self.upload_request_data(),
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("cerrado", response.json()["error"])

    @patch("eventos.views.generar_url_subida", return_value="https://upload.test/")
    def test_reopening_event_restores_authorized_upload_requests(self, _generate_url):
        self.event.estado = Evento.Estado.CLOSED
        self.event.save(update_fields=["estado"])
        self.login_as(self.host)

        reopen_response = self.client.post(
            reverse("reabrir_evento", args=[self.event.slug])
        )

        self.assertEqual(reopen_response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.estado, Evento.Estado.ACTIVE)

        self.authorize_public_upload(self.table)
        response = self.client.post(
            reverse(
                "solicitar_url_subida",
                args=[self.event.slug, self.table.token],
            ),
            self.upload_request_data(),
        )

        self.assertEqual(response.status_code, 200)
