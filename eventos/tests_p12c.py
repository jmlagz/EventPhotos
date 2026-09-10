from datetime import date, datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import EventoForm
from .models import Evento, Mesa
from .services.event_configuration import (
    asignar_anfitrion,
    construir_fin_planeado,
    crear_evento_configurable,
    evaluar_checklist,
    validar_activacion,
)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class GuidedEventCreationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="guided-admin@example.com",
            email="guided-admin@example.com",
            password="test-password",
        )
        self.client.force_login(self.admin)

    def payload(self, **overrides):
        data = {
            "nombre": "Boda guiada",
            "tipo": Evento.Tipo.BODA,
            "fecha": "2026-10-17",
            "descripcion": "Descripción",
            "mensaje_bienvenida": "Bienvenidos",
            "timezone": "America/Mexico_City",
            "vigencia_meses": "6",
        }
        data.update(overrides)
        return data

    def test_legacy_model_default_remains_null(self):
        legacy = Evento.objects.create(
            nombre="Evento legacy",
            fecha=date(2026, 10, 17),
        )
        self.assertIsNone(legacy.configuracion_version)

    def test_creation_form_has_friendly_defaults(self):
        form = EventoForm()
        self.assertEqual(form.fields["timezone"].initial, "America/Mexico_City")
        self.assertEqual(form.fields["vigencia_meses"].initial, 6)
        self.assertNotIn("fin_planeado", form.fields)
        self.assertNotIn("upload_until", form.fields)
        self.assertNotIn("available_until", form.fields)

    def test_guided_creation_materializes_temporal_cycle(self):
        response = self.client.post(reverse("crear_evento"), self.payload())
        self.assertEqual(response.status_code, 302)

        event = Evento.objects.get(nombre="Boda guiada")
        local_timezone = ZoneInfo("America/Mexico_City")
        expected_end = datetime(2026, 10, 18, 4, 0, tzinfo=local_timezone)
        self.assertEqual(event.configuracion_version, 1)
        self.assertEqual(event.estado, Evento.Estado.DRAFT)
        self.assertEqual(event.fin_planeado, expected_end)
        self.assertEqual(event.upload_until, expected_end + timedelta(hours=48))
        self.assertEqual(
            event.available_until,
            datetime(2027, 4, 18, 4, 0, tzinfo=local_timezone),
        )
        self.assertFalse(event.anfitriones.filter(pk=self.admin.pk).exists())

    def test_invalid_timezone_is_rejected(self):
        response = self.client.post(
            reverse("crear_evento"),
            self.payload(timezone="Invalid/Zone"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")
        self.assertFalse(Evento.objects.filter(nombre="Boda guiada").exists())

    def test_new_events_receive_controlled_unique_slugs(self):
        first = crear_evento_configurable(
            nombre="Evento repetido",
            tipo=Evento.Tipo.OTRO,
            fecha=date(2026, 10, 17),
            descripcion="",
            mensaje_bienvenida="",
            timezone_name="America/Mexico_City",
            duracion_efectiva_meses=6,
        )
        second = crear_evento_configurable(
            nombre="Evento repetido",
            tipo=Evento.Tipo.OTRO,
            fecha=date(2026, 10, 18),
            descripcion="",
            mensaje_bienvenida="",
            timezone_name="America/Mexico_City",
            duracion_efectiva_meses=12,
        )
        self.assertEqual(first.slug, "evento-repetido")
        self.assertEqual(second.slug, "evento-repetido-2")
        self.assertEqual(
            second.available_until,
            second.fin_planeado.replace(year=second.fin_planeado.year + 1),
        )

    def test_dst_zone_produces_aware_end_and_exact_48_hours(self):
        event = crear_evento_configurable(
            nombre="Evento DST",
            tipo=Evento.Tipo.OTRO,
            fecha=date(2026, 3, 6),
            descripcion="",
            mensaje_bienvenida="",
            timezone_name="America/Tijuana",
            duracion_efectiva_meses=6,
        )
        expected = construir_fin_planeado(
            date(2026, 3, 6),
            "America/Tijuana",
        )
        self.assertIsNotNone(expected.utcoffset())
        self.assertEqual(expected.hour, 4)
        event.refresh_from_db()
        self.assertEqual(
            event.upload_until.astimezone(datetime_timezone.utc)
            - event.fin_planeado.astimezone(datetime_timezone.utc),
            timedelta(hours=48),
        )


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class GuidedHostAssignmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            "assignment-admin@example.com",
            "assignment-admin@example.com",
            "test-password",
        )
        self.host = User.objects.create_user(
            "existing-host@example.com",
            "existing-host@example.com",
            "test-password",
        )
        self.event = self.create_event("Asignación uno")
        self.other_event = self.create_event("Asignación dos")

    def create_event(self, name):
        return crear_evento_configurable(
            nombre=name,
            tipo=Evento.Tipo.OTRO,
            fecha=date(2026, 11, 1),
            descripcion="",
            mensaje_bienvenida="",
            timezone_name="America/Mexico_City",
            duracion_efectiva_meses=6,
        )

    def test_explicit_relation_defines_operational_host(self):
        asignar_anfitrion(self.event, self.host)
        asignar_anfitrion(self.event, self.admin)
        asignar_anfitrion(self.other_event, self.host)

        self.assertCountEqual(
            self.event.anfitriones.all(),
            [self.host, self.admin],
        )
        self.assertTrue(self.other_event.anfitriones.filter(pk=self.host.pk).exists())

    def test_admin_can_assign_existing_user_and_returns_to_event(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("asignar_anfitrion_existente", args=[self.event.slug]),
            {"usuario": self.host.pk},
        )
        self.assertRedirects(
            response,
            reverse("dashboard_evento", args=[self.event.slug]),
        )
        self.assertTrue(self.event.anfitriones.filter(pk=self.host.pk).exists())

    def test_normal_host_cannot_assign_users(self):
        asignar_anfitrion(self.event, self.host)
        self.client.force_login(self.host)
        response = self.client.post(
            reverse("asignar_anfitrion_existente", args=[self.event.slug]),
            {"usuario": self.admin.pk},
        )
        self.assertEqual(response.status_code, 403)

    @patch("eventos.views.EmailMultiAlternatives.send", return_value=1)
    def test_new_host_creation_returns_to_preselected_event(self, _send):
        self.client.force_login(self.admin)
        url = reverse("crear_usuario") + f"?evento={self.event.slug}"
        response = self.client.post(
            url,
            {
                "first_name": "Nueva",
                "last_name": "Anfitriona",
                "email": "new-host@example.com",
                "rol": "anfitrion",
                "eventos": [self.event.pk],
            },
        )
        self.assertRedirects(
            response,
            reverse("dashboard_evento", args=[self.event.slug]),
        )
        new_host = User.objects.get(username="new-host@example.com")
        self.assertTrue(self.event.anfitriones.filter(pk=new_host.pk).exists())


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class GuidedEventEditingTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(
            "edit-host@example.com",
            password="test-password",
        )
        self.other_host = User.objects.create_user(
            "other-edit-host@example.com",
            password="test-password",
        )
        self.event = crear_evento_configurable(
            nombre="Evento editable",
            tipo=Evento.Tipo.BODA,
            fecha=date(2026, 10, 17),
            descripcion="Inicial",
            mensaje_bienvenida="Hola",
            timezone_name="America/Mexico_City",
            duracion_efectiva_meses=6,
        )
        asignar_anfitrion(self.event, self.host)
        self.client.force_login(self.host)

    def payload(self, **overrides):
        data = {
            "nombre": "Evento actualizado",
            "tipo": Evento.Tipo.XV_ANOS,
            "fecha": "2026-11-08",
            "descripcion": "Nueva descripción",
            "mensaje_bienvenida": "Nuevo mensaje",
            "timezone": "America/Cancun",
            "vigencia_meses": "12",
        }
        data.update(overrides)
        return data

    def edit_url(self):
        return reverse("editar_evento", args=[self.event.slug])

    def test_draft_allows_edit_and_explicit_rematerialization(self):
        original_slug = self.event.slug
        response = self.client.post(self.edit_url(), self.payload())
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.nombre, "Evento actualizado")
        self.assertEqual(self.event.fecha, date(2026, 11, 8))
        self.assertEqual(self.event.timezone, "America/Cancun")
        local_end = self.event.fin_planeado.astimezone(ZoneInfo("America/Cancun"))
        self.assertEqual(local_end.day, 9)
        self.assertEqual(local_end.hour, 4)
        self.assertEqual(self.event.slug, original_slug)

    def test_active_and_closed_allow_text_but_lock_date_and_timezone(self):
        for state in (Evento.Estado.ACTIVE, Evento.Estado.CLOSED):
            with self.subTest(state=state):
                self.event.estado = state
                self.event.nombre = "Evento editable"
                self.event.save(update_fields=["estado", "nombre"])
                original_date = self.event.fecha
                original_timezone = self.event.timezone

                response = self.client.post(self.edit_url(), self.payload())
                self.assertEqual(response.status_code, 302)
                self.event.refresh_from_db()
                self.assertEqual(self.event.nombre, "Evento actualizado")
                self.assertEqual(self.event.fecha, original_date)
                self.assertEqual(self.event.timezone, original_timezone)

    def test_archived_event_is_read_only(self):
        self.event.estado = Evento.Estado.ARCHIVED
        self.event.save(update_fields=["estado"])
        original_name = self.event.nombre
        response = self.client.post(self.edit_url(), self.payload())
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.nombre, original_name)
        self.assertContains(response, "sólo lectura")

    def test_unrelated_host_cannot_edit_event(self):
        self.client.force_login(self.other_host)
        response = self.client.get(self.edit_url())
        self.assertEqual(response.status_code, 404)

    def test_legacy_active_event_can_edit_text_without_temporal_backfill(self):
        legacy = Evento.objects.create(
            nombre="Legacy editable",
            fecha=date(2026, 10, 17),
            estado=Evento.Estado.ACTIVE,
        )
        legacy.anfitriones.add(self.host)
        response = self.client.post(
            reverse("editar_evento", args=[legacy.slug]),
            {
                "nombre": "Legacy actualizado",
                "tipo": Evento.Tipo.OTRO,
                "fecha": "2027-01-01",
                "descripcion": "Texto actualizado",
                "mensaje_bienvenida": "Hola",
                "timezone": "America/Cancun",
                "vigencia_meses": "12",
            },
        )
        self.assertEqual(response.status_code, 302)
        legacy.refresh_from_db()
        self.assertEqual(legacy.nombre, "Legacy actualizado")
        self.assertEqual(legacy.fecha, date(2026, 10, 17))
        self.assertIsNone(legacy.timezone)
        self.assertIsNone(legacy.fin_planeado)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class GuidedChecklistAndActivationTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(
            "checklist-host@example.com",
            password="test-password",
        )
        self.event = crear_evento_configurable(
            nombre="Evento checklist",
            tipo=Evento.Tipo.BODA,
            fecha=date(2026, 12, 5),
            descripcion="",
            mensaje_bienvenida="",
            timezone_name="America/Mexico_City",
            duracion_efectiva_meses=6,
        )

    def test_checklist_is_derived_and_identity_is_optional(self):
        checklist = evaluar_checklist(self.event)
        states = {item["clave"]: item["estado"] for item in checklist["items"]}
        self.assertEqual(states["anfitriones"], "Pendiente")
        self.assertEqual(states["mesas"], "Pendiente")
        self.assertEqual(states["identidad"], "Opcional")
        self.assertEqual(states["activacion"], "Revisar")
        self.assertFalse(checklist["listo"])

    def test_dashboard_renders_checklist_and_disabled_activation(self):
        asignar_anfitrion(self.event, self.host)
        self.client.force_login(self.host)
        response = self.client.get(
            reverse("dashboard_evento", args=[self.event.slug])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Checklist de configuración")
        self.assertContains(response, 'data-checklist="mesas"')
        self.assertContains(response, "Opcional")
        self.assertContains(response, "disabled")

    def test_activation_is_blocked_until_host_and_table_exist(self):
        self.client.force_login(self.host)
        asignar_anfitrion(self.event, self.host)
        activation_url = reverse("activar_evento", args=[self.event.slug])

        response = self.client.post(activation_url)
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.estado, Evento.Estado.DRAFT)

        Mesa.objects.create(evento=self.event, numero=1)
        self.client.post(activation_url)
        self.event.refresh_from_db()
        self.assertEqual(self.event.estado, Evento.Estado.ACTIVE)

    def test_explicitly_assigned_superuser_can_satisfy_host_requirement(self):
        admin = User.objects.create_superuser(
            "explicit-admin@example.com",
            "explicit-admin@example.com",
            "test-password",
        )
        asignar_anfitrion(self.event, admin)
        Mesa.objects.create(evento=self.event, numero=1)
        validar_activacion(self.event)

    def test_legacy_null_version_keeps_previous_activation_rule(self):
        legacy = Evento.objects.create(
            nombre="Legacy activable",
            fecha=date(2026, 12, 5),
            estado=Evento.Estado.DRAFT,
        )
        legacy.anfitriones.add(self.host)
        self.client.force_login(self.host)
        response = self.client.post(reverse("activar_evento", args=[legacy.slug]))
        self.assertEqual(response.status_code, 302)
        legacy.refresh_from_db()
        self.assertEqual(legacy.estado, Evento.Estado.ACTIVE)
        self.assertTrue(legacy.permite_carga())

    def test_incoherent_temporal_data_fails_server_validation(self):
        asignar_anfitrion(self.event, self.host)
        Mesa.objects.create(evento=self.event, numero=1)
        self.event.upload_until = self.event.fin_planeado + timedelta(hours=24)
        self.event.save(update_fields=["upload_until"])
        with self.assertRaises(ValidationError):
            validar_activacion(self.event)
