from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Evento


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class DashboardNavigationPolishTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            "admin-navigation@example.com",
            "admin-navigation@example.com",
            "test-password",
        )
        self.host = User.objects.create_user(
            "host-navigation@example.com",
            "host-navigation@example.com",
            "test-password",
        )
        self.other_host = User.objects.create_user(
            "other-navigation@example.com",
            "other-navigation@example.com",
            "test-password",
        )

    def create_event(self, name, state):
        event = Evento.objects.create(
            nombre=name,
            fecha=date(2026, 12, 12),
            estado=state,
        )
        event.anfitriones.add(self.host)
        return event

    def event_dashboard(self, event, user=None):
        self.client.force_login(user or self.host)
        return self.client.get(
            reverse("dashboard_evento", args=[event.slug])
        )

    def test_event_breadcrumb_uses_the_correct_list_for_each_role(self):
        event = self.create_event("Evento navegable", Evento.Estado.ACTIVE)

        host_response = self.event_dashboard(event)
        self.assertContains(
            host_response,
            f'<a href="{reverse("dashboard_anfitrion")}">Mis eventos</a>',
            html=True,
        )
        self.assertContains(host_response, 'aria-current="page"')

        admin_response = self.event_dashboard(event, self.admin)
        self.assertContains(
            admin_response,
            f'<a href="{reverse("dashboard")}">Dashboard</a>',
            html=True,
        )

    def test_quick_actions_match_draft_state(self):
        response = self.event_dashboard(
            self.create_event("Evento borrador", Evento.Estado.DRAFT)
        )

        for action in ("activar", "mesas", "editar"):
            self.assertContains(response, f'data-quick-action="{action}"')
        for action in ("album", "slideshow", "reabrir"):
            self.assertNotContains(response, f'data-quick-action="{action}"')
        self.assertContains(response, "Borrador")

    def test_quick_actions_match_active_and_closed_states(self):
        active_response = self.event_dashboard(
            self.create_event("Evento activo", Evento.Estado.ACTIVE)
        )
        for action in ("album", "slideshow", "mesas"):
            self.assertContains(
                active_response,
                f'data-quick-action="{action}"',
            )
        self.assertNotContains(
            active_response,
            'data-quick-action="activar"',
        )
        self.assertContains(active_response, "Activo")

        closed_response = self.event_dashboard(
            self.create_event("Evento cerrado", Evento.Estado.CLOSED)
        )
        for action in ("album", "slideshow", "reabrir"):
            self.assertContains(
                closed_response,
                f'data-quick-action="{action}"',
            )
        self.assertNotContains(
            closed_response,
            'data-quick-action="mesas"',
        )
        self.assertContains(closed_response, "Cerrado")

    def test_archived_event_has_no_operational_quick_actions(self):
        response = self.event_dashboard(
            self.create_event("Evento archivado", Evento.Estado.ARCHIVED)
        )

        self.assertNotContains(response, "data-quick-action=")
        self.assertContains(
            response,
            "Evento archivado disponible sólo para consulta.",
        )
        self.assertContains(response, "Archivado")

    def test_cards_have_one_primary_cta_and_state_appropriate_shortcuts(self):
        events = {
            state: self.create_event(f"Evento {state}", state)
            for state in (
                Evento.Estado.DRAFT,
                Evento.Estado.ACTIVE,
                Evento.Estado.CLOSED,
                Evento.Estado.ARCHIVED,
            )
        }
        self.client.force_login(self.host)

        response = self.client.get(reverse("dashboard_anfitrion"))
        rendered = response.content.decode()

        self.assertEqual(rendered.count('data-card-action="dashboard"'), 4)
        self.assertEqual(rendered.count('data-card-action="mesas"'), 2)
        self.assertEqual(rendered.count('data-card-action="album"'), 2)
        archived_start = rendered.index(
            f'data-event-state="{Evento.Estado.ARCHIVED}"'
        )
        self.assertNotIn(
            'data-card-action="mesas"',
            rendered[archived_start:],
        )
        self.assertNotIn(
            'data-card-action="album"',
            rendered[archived_start:],
        )

    def test_existing_event_authorization_is_unchanged(self):
        event = self.create_event("Evento privado", Evento.Estado.ACTIVE)

        response = self.event_dashboard(event, self.other_host)

        self.assertEqual(response.status_code, 404)
