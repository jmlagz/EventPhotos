from datetime import date

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Evento, InvitacionUsuario, Mesa
from .services.event_configuration import (
    asignar_anfitrion,
    crear_evento_configurable,
    desasignar_anfitrion,
    evaluar_checklist,
    validar_activacion,
)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class CompleteHostManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            "host-admin@example.com",
            "host-admin@example.com",
            "test-password",
        )
        self.host = User.objects.create_user(
            "host@example.com",
            "host@example.com",
            "test-password",
        )
        self.other_host = User.objects.create_user(
            "other-host@example.com",
            "other-host@example.com",
            "test-password",
        )
        self.unassigned = User.objects.create_user(
            "unassigned@example.com",
            "unassigned@example.com",
            "test-password",
        )
        self.event = self.create_event("Gestión de anfitriones")
        self.other_event = self.create_event("Otro evento")

    def create_event(self, name):
        return crear_evento_configurable(
            nombre=name,
            tipo=Evento.Tipo.OTRO,
            fecha=date(2026, 11, 14),
            descripcion="",
            mensaje_bienvenida="",
            timezone_name="America/Mexico_City",
            duracion_efectiva_meses=6,
        )

    def assignment_url(self):
        return reverse(
            "asignar_anfitrion_existente",
            args=[self.event.slug],
        )

    def removal_url(self, user):
        return reverse(
            "desasignar_anfitrion_existente",
            args=[self.event.slug, user.pk],
        )

    def test_assigning_existing_user_creates_relation_and_sends_one_email(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            self.assignment_url(),
            {"usuario": self.host.pk},
        )

        self.assertRedirects(
            response,
            reverse("dashboard_evento", args=[self.event.slug]),
        )
        self.assertTrue(self.event.anfitriones.filter(pk=self.host.pk).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.event.nombre, mail.outbox[0].subject)
        self.assertIn(self.event.nombre, mail.outbox[0].body)
        self.assertIn(reverse("login_anfitrion"), mail.outbox[0].body)

    def test_reassigning_existing_relation_is_noop_without_email(self):
        self.event.anfitriones.add(self.host)

        assigned = asignar_anfitrion(self.event, self.host)

        self.assertFalse(assigned)
        self.assertEqual(
            self.event.anfitriones.filter(pk=self.host.pk).count(),
            1,
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_creating_host_assigns_event_and_sends_only_assignment_email(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("crear_usuario") + f"?evento={self.event.slug}",
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
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.event.nombre, mail.outbox[0].body)
        invitation = InvitacionUsuario.objects.get(usuario=new_host)
        self.assertIn(invitation.token, mail.outbox[0].body)

    def test_removing_host_preserves_user_and_other_event_relations(self):
        self.event.anfitriones.add(self.host, self.other_host)
        self.other_event.anfitriones.add(self.host)
        self.client.force_login(self.admin)

        response = self.client.post(self.removal_url(self.host))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.event.anfitriones.filter(pk=self.host.pk).exists())
        self.assertTrue(self.event.anfitriones.filter(pk=self.other_host.pk).exists())
        self.assertTrue(
            self.other_event.anfitriones.filter(pk=self.host.pk).exists()
        )
        self.assertTrue(User.objects.filter(pk=self.host.pk).exists())

    def test_removing_last_draft_host_is_allowed_and_blocks_activation(self):
        self.event.anfitriones.add(self.host)
        Mesa.objects.create(evento=self.event, numero=1)
        self.client.force_login(self.admin)

        self.client.post(self.removal_url(self.host))

        self.event.refresh_from_db()
        checklist = evaluar_checklist(self.event)
        states = {item["clave"]: item["estado"] for item in checklist["items"]}
        self.assertEqual(self.event.anfitriones.count(), 0)
        self.assertEqual(states["anfitriones"], "Pendiente")
        self.assertEqual(states["activacion"], "Revisar")
        self.assertFalse(checklist["listo"])
        with self.assertRaises(ValidationError):
            validar_activacion(self.event)

    def test_removing_last_active_host_preserves_state_and_temporality(self):
        self.event.anfitriones.add(self.host)
        self.event.estado = Evento.Estado.ACTIVE
        self.event.save(update_fields=["estado"])
        original_temporality = (
            self.event.fin_planeado,
            self.event.upload_until,
            self.event.available_until,
        )
        self.client.force_login(self.admin)

        self.client.post(self.removal_url(self.host))

        self.event.refresh_from_db()
        self.assertEqual(self.event.anfitriones.count(), 0)
        self.assertEqual(self.event.estado, Evento.Estado.ACTIVE)
        self.assertEqual(
            (
                self.event.fin_planeado,
                self.event.upload_until,
                self.event.available_until,
            ),
            original_temporality,
        )

    def test_normal_and_unassigned_users_cannot_manage_hosts(self):
        self.event.anfitriones.add(self.host)

        for actor in (self.host, self.unassigned):
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                assign_response = self.client.post(
                    self.assignment_url(),
                    {"usuario": self.other_host.pk},
                )
                remove_response = self.client.post(self.removal_url(self.host))
                self.assertEqual(assign_response.status_code, 403)
                self.assertEqual(remove_response.status_code, 403)

        self.assertFalse(
            self.event.anfitriones.filter(pk=self.other_host.pk).exists()
        )
        self.assertTrue(self.event.anfitriones.filter(pk=self.host.pk).exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_manipulated_user_id_cannot_remove_host_from_another_event(self):
        self.other_event.anfitriones.add(self.other_host)
        self.client.force_login(self.admin)

        response = self.client.post(self.removal_url(self.other_host))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(
            self.other_event.anfitriones.filter(pk=self.other_host.pk).exists()
        )

    def test_unassigning_missing_relation_is_safe_and_idempotent(self):
        removed = desasignar_anfitrion(self.event, self.host)

        self.assertFalse(removed)
        self.assertTrue(User.objects.filter(pk=self.host.pk).exists())

    def test_orphan_dashboard_shows_alert_and_last_host_confirmation(self):
        self.client.force_login(self.admin)
        orphan_response = self.client.get(
            reverse("dashboard_evento", args=[self.event.slug])
        )
        self.assertContains(
            orphan_response,
            "Este evento no tiene anfitriones asignados.",
        )

        self.event.anfitriones.add(self.host)
        assigned_response = self.client.get(
            reverse("dashboard_evento", args=[self.event.slug])
        )
        self.assertContains(assigned_response, "Este es el último anfitrión")
        self.assertContains(assigned_response, "csrfmiddlewaretoken")
        self.assertContains(assigned_response, ">\n                                    Quitar\n")

    def test_management_mutations_reject_get(self):
        self.event.anfitriones.add(self.host)
        self.client.force_login(self.admin)

        self.assertEqual(self.client.get(self.assignment_url()).status_code, 405)
        self.assertEqual(self.client.get(self.removal_url(self.host)).status_code, 405)

    def test_management_mutations_require_csrf(self):
        self.event.anfitriones.add(self.host)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)

        assign_response = csrf_client.post(
            self.assignment_url(),
            {"usuario": self.other_host.pk},
        )
        remove_response = csrf_client.post(self.removal_url(self.host))

        self.assertEqual(assign_response.status_code, 403)
        self.assertEqual(remove_response.status_code, 403)
        self.assertTrue(self.event.anfitriones.filter(pk=self.host.pk).exists())
        self.assertFalse(
            self.event.anfitriones.filter(pk=self.other_host.pk).exists()
        )

    def test_legacy_event_can_be_left_without_hosts_without_other_changes(self):
        legacy = Evento.objects.create(
            nombre="Legacy sin anfitrión",
            fecha=date(2026, 11, 14),
            estado=Evento.Estado.CLOSED,
        )
        legacy.anfitriones.add(self.host)

        self.assertTrue(desasignar_anfitrion(legacy, self.host))

        legacy.refresh_from_db()
        self.assertIsNone(legacy.configuracion_version)
        self.assertEqual(legacy.estado, Evento.Estado.CLOSED)
        self.assertIsNone(legacy.fin_planeado)
        self.assertIsNone(legacy.upload_until)
        self.assertIsNone(legacy.available_until)
        self.assertEqual(legacy.anfitriones.count(), 0)
