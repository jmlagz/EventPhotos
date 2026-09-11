from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import Evento, Mesa


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class DashboardListPolishTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            "admin-dashboard@example.com",
            "admin-dashboard@example.com",
            "test-password",
        )
        self.host = User.objects.create_user(
            "host-dashboard@example.com",
            "host-dashboard@example.com",
            "test-password",
        )

    def create_event(self, name, state, event_date, *, cover_key=""):
        return Evento.objects.create(
            nombre=name,
            fecha=event_date,
            estado=state,
            imagen_portada_key=cover_key,
        )

    def create_state_set(self):
        return {
            "archived": self.create_event(
                "Evento archivado",
                Evento.Estado.ARCHIVED,
                date(2026, 12, 4),
            ),
            "draft": self.create_event(
                "Evento borrador",
                Evento.Estado.DRAFT,
                date(2026, 12, 2),
            ),
            "closed": self.create_event(
                "Evento cerrado",
                Evento.Estado.CLOSED,
                date(2026, 12, 3),
            ),
            "active": self.create_event(
                "Evento activo",
                Evento.Estado.ACTIVE,
                date(2026, 12, 1),
            ),
        }

    def test_global_dashboard_orders_states_and_uses_friendly_labels(self):
        self.create_state_set()
        self.client.force_login(self.admin)

        response = self.client.get(reverse("dashboard"))
        rendered = response.content.decode()

        state_markers = [
            'data-event-state="active"',
            'data-event-state="draft"',
            'data-event-state="closed"',
            'data-event-state="archived"',
        ]
        positions = [rendered.index(marker) for marker in state_markers]
        self.assertEqual(positions, sorted(positions))
        for label in ("Activo", "Borrador", "Cerrado", "Archivado"):
            self.assertContains(response, label)
        for summary_label in (
            "Activos",
            "Borradores",
            "Cerrados",
            "Archivados",
        ):
            self.assertContains(response, summary_label)

    def test_host_dashboard_only_lists_assigned_events_in_state_order(self):
        events = self.create_state_set()
        events["active"].anfitriones.add(self.host)
        events["closed"].anfitriones.add(self.host)
        self.client.force_login(self.host)

        response = self.client.get(reverse("dashboard_anfitrion"))
        rendered = response.content.decode()

        self.assertContains(response, "Evento activo")
        self.assertContains(response, "Evento cerrado")
        self.assertNotContains(response, "Evento borrador")
        self.assertLess(
            rendered.index('data-event-state="active"'),
            rendered.index('data-event-state="closed"'),
        )
        self.assertContains(response, "Entrar al evento")

    @patch(
        "eventos.views.generar_url_lectura",
        return_value="https://signed.test/cover.webp",
    )
    def test_cards_show_signed_cover_or_local_fallback_without_exposing_key(
        self,
        signer,
    ):
        covered = self.create_event(
            "Con portada",
            Evento.Estado.ACTIVE,
            date(2026, 12, 1),
            cover_key="eventos/private/cover.webp",
        )
        covered.anfitriones.add(self.host)
        fallback = self.create_event(
            "Sin portada",
            Evento.Estado.DRAFT,
            date(2026, 12, 2),
        )
        fallback.anfitriones.add(self.host)
        self.client.force_login(self.host)

        response = self.client.get(reverse("dashboard_anfitrion"))
        rendered = response.content.decode()

        self.assertContains(response, "https://signed.test/cover.webp")
        self.assertContains(response, "event-cover-fallback")
        self.assertNotContains(response, "eventos/private/cover.webp")
        self.assertIn(
            'onerror="this.hidden=true; this.nextElementSibling.hidden=false;"',
            rendered,
        )
        self.assertIn(
            'class="event-cover-fallback" aria-hidden="true" hidden',
            rendered,
        )
        signer.assert_called_once_with("eventos/private/cover.webp")

    @patch("eventos.views.generar_url_lectura")
    def test_dashboard_event_queries_do_not_scale_per_card(self, signer):
        events = self.create_state_set()
        for index, event in enumerate(events.values(), start=1):
            Mesa.objects.create(evento=event, numero=index)
        self.client.force_login(self.admin)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        event_relation_queries = [
            query["sql"]
            for query in queries.captured_queries
            if "eventos_foto" in query["sql"].lower()
            or "eventos_mesa" in query["sql"].lower()
        ]
        self.assertLessEqual(len(event_relation_queries), 2)
        signer.assert_not_called()

    def test_existing_dashboard_authorization_redirects_are_preserved(self):
        self.client.force_login(self.host)
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("dashboard_anfitrion"))

        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard_anfitrion"))
        self.assertRedirects(response, reverse("dashboard"))

    def test_card_secondary_actions_have_touch_sized_targets(self):
        styles = Path(
            settings.BASE_DIR,
            "eventos",
            "static",
            "eventos",
            "css",
            "dashboard-lists.css",
        ).read_text(encoding="utf-8")

        self.assertIn(".event-secondary-link", styles)
        self.assertIn("min-height: 44px;", styles)
