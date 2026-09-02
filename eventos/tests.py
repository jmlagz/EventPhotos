from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from dateutil.relativedelta import relativedelta

from .limites import (
    MAX_FOTOS_POR_EVENTO,
    MAX_STORAGE_POR_EVENTO,
    MAX_TAMANO_FOTO,
)
from .models import Evento, Foto, Mesa


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
