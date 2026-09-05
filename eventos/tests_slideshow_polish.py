from datetime import date, timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Evento


class SlideshowViewportCssTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = (
            Path(__file__).parent / "templates" / "eventos" / "slideshow.html"
        ).read_text(encoding="utf-8")
        cls.slideshow_rule = cls.template.split(".slideshow {", 1)[1].split("}", 1)[0]
        cls.photo_rule = cls.template.split(".slideshow__photo {", 1)[1].split("}", 1)[0]

    def test_slideshow_container_is_strictly_confined_to_viewport(self):
        self.assertIn("width: 100vw", self.slideshow_rule)
        self.assertIn("height: 100vh", self.slideshow_rule)
        self.assertIn("height: 100dvh", self.slideshow_rule)
        self.assertIn("overflow: hidden", self.slideshow_rule)

    def test_photo_cannot_participate_in_grid_sizing_or_overflow(self):
        for declaration in (
            "position: absolute",
            "inset: 0",
            "width: 100%",
            "height: 100%",
            "max-width: 100vw",
            "max-height: 100dvh",
            "min-width: 0",
            "min-height: 0",
            "object-fit: contain",
        ):
            self.assertIn(declaration, self.photo_rule)
        self.assertNotIn("object-fit: cover", self.template)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class DashboardSlideshowActionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.host = user_model.objects.create_user(
            username="slideshow-host@example.com",
            password="test-password",
        )
        self.other_host = user_model.objects.create_user(
            username="other-slideshow-host@example.com",
            password="test-password",
        )

    def create_event(self, estado, **kwargs):
        event = Evento.objects.create(
            nombre=f"Evento {estado}",
            fecha=date(2026, 9, 4),
            estado=estado,
            **kwargs,
        )
        event.anfitriones.add(self.host)
        return event

    def dashboard_response(self, event):
        self.client.force_login(self.host)
        return self.client.get(reverse("dashboard_evento", args=[event.slug]))

    def assert_slideshow_action(self, response, event):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Abrir slideshow")
        self.assertContains(response, reverse("slideshow", args=[event.slug]))
        self.assertContains(response, 'target="_blank"')
        self.assertContains(response, 'rel="noopener"')

    def test_active_event_dashboard_links_to_named_slideshow_route(self):
        event = self.create_event(Evento.Estado.ACTIVE)

        self.assert_slideshow_action(self.dashboard_response(event), event)

    def test_closed_available_event_shows_slideshow_action(self):
        event = self.create_event(
            Evento.Estado.CLOSED,
            available_until=timezone.now() + timedelta(days=1),
        )

        self.assert_slideshow_action(self.dashboard_response(event), event)

    def test_closed_expired_event_does_not_show_slideshow_action(self):
        event = self.create_event(
            Evento.Estado.CLOSED,
            available_until=timezone.now() - timedelta(seconds=1),
        )

        response = self.dashboard_response(event)

        self.assertNotContains(response, "Abrir slideshow")
        self.assertNotContains(response, reverse("slideshow", args=[event.slug]))

    def test_draft_and_archived_events_do_not_show_slideshow_action(self):
        for state in (Evento.Estado.DRAFT, Evento.Estado.ARCHIVED):
            with self.subTest(state=state):
                event = self.create_event(state)

                response = self.dashboard_response(event)

                self.assertNotContains(response, "Abrir slideshow")
                self.assertNotContains(response, reverse("slideshow", args=[event.slug]))

    def test_existing_dashboard_authorization_still_rejects_unrelated_host(self):
        event = self.create_event(Evento.Estado.ACTIVE)
        self.client.force_login(self.other_host)

        response = self.client.get(reverse("dashboard_evento", args=[event.slug]))

        self.assertEqual(response.status_code, 404)
