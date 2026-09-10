from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Evento, Mesa
from .services.event_configuration import crear_evento_configurable


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class DashboardVisualAlignmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            "visual-admin@example.com",
            "visual-admin@example.com",
            "test-password",
        )
        self.event = crear_evento_configurable(
            nombre="Evento visual",
            tipo=Evento.Tipo.BODA,
            fecha=date(2026, 12, 12),
            descripcion="Una celebración especial",
            mensaje_bienvenida="Bienvenidos",
            timezone_name="America/Mexico_City",
            duracion_efectiva_meses=6,
        )
        self.client.force_login(self.admin)

    def dashboard(self):
        return self.client.get(
            reverse("dashboard_evento", args=[self.event.slug])
        )

    def test_dashboard_sections_follow_guided_visual_order(self):
        response = self.dashboard()
        rendered = response.content.decode()
        section_ids = (
            'id="config-informacion"',
            'id="config-fecha"',
            'id="config-anfitriones"',
            'id="config-mesas"',
            'id="config-identidad"',
            'id="config-revision"',
            'id="config-activacion"',
        )

        for section_id in section_ids:
            self.assertContains(response, section_id)
        self.assertEqual(rendered.count('id="config-anfitriones"'), 1)
        self.assertContains(response, 'class="dashboard-flow"')
        positions = [rendered.index(section_id) for section_id in section_ids]
        self.assertEqual(positions, sorted(positions))
        self.assertNotContains(response, "flow-information")
        self.assertNotContains(response, "flow-activation")

    def test_checklist_links_to_every_configuration_section(self):
        response = self.dashboard()

        for target in (
            "config-informacion",
            "config-fecha",
            "config-anfitriones",
            "config-mesas",
            "config-identidad",
            "config-activacion",
        ):
            self.assertContains(response, f'href="#{target}"')
        self.assertContains(response, "checklist-state-pendiente")
        self.assertContains(response, "checklist-state-opcional")
        self.assertContains(response, "checklist-state-revisar")

    def test_complete_draft_uses_complete_chips_and_enabled_activation(self):
        self.event.anfitriones.add(self.admin)
        Mesa.objects.create(evento=self.event, numero=1)

        response = self.dashboard()

        self.assertContains(response, "checklist-state-completo")
        self.assertContains(response, "✓")
        self.assertContains(response, "Completo")
        self.assertNotContains(
            response,
            'class="button button-success"\n                    disabled',
        )

    def test_orphan_alert_uses_nonfatal_warning_variant(self):
        response = self.dashboard()

        self.assertContains(response, "alert alert-warning hostless-alert")
        self.assertContains(response, 'role="status"')

    def test_commercial_palette_is_defined_in_shared_tokens(self):
        tokens = Path(
            settings.BASE_DIR,
            "eventos",
            "static",
            "eventos",
            "css",
            "tokens.css",
        ).read_text(encoding="utf-8")

        for color in (
            "#073b4c",
            "#16c7b0",
            "#20bfe7",
            "#ef476f",
            "#ffc43d",
            "#c8b6e8",
            "#f7fbef",
            "#ffffff",
        ):
            self.assertIn(color, tokens.lower())
        self.assertIn("--color-focus:", tokens)
        self.assertIn("--color-surface-muted:", tokens)
        self.assertIn("--ep-color-primary: var(--color-primary);", tokens)
