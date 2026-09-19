from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Evento, Mesa


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class TableConfigurationSelfServiceTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user("tables-host@example.com")
        self.other_host = User.objects.create_user("tables-other@example.com")
        self.superuser = User.objects.create_superuser(
            "tables-admin@example.com",
            "tables-admin@example.com",
            "password",
        )

    def create_event(self, *, name="Evento mesas", source=None, host=None):
        event = Evento.objects.create(
            nombre=name,
            fecha=date(2026, 12, 12),
            estado=Evento.Estado.DRAFT,
            creation_source=source,
            self_service_created_by=(
                host
                if source == Evento.CreationSource.SELF_SERVICE
                else None
            ),
        )
        event.anfitriones.add(host or self.host)
        return event

    def configuration_url(self, event):
        return reverse("configurar_mesas", args=[event.slug])

    def test_first_visit_creates_exactly_one_initial_table(self):
        event = self.create_event(
            source=Evento.CreationSource.SELF_SERVICE,
            host=self.host,
        )
        self.client.force_login(self.host)

        response = self.client.get(self.configuration_url(event))

        self.assertEqual(response.status_code, 200)
        table = event.mesas.get()
        self.assertEqual(table.numero, 1)
        self.assertEqual(table.nombre, "")
        self.assertEqual(table.etiqueta, "Mesa 1")

    def test_repeated_visits_do_not_create_duplicate_initial_table(self):
        event = self.create_event(
            source=Evento.CreationSource.SELF_SERVICE,
            host=self.host,
        )
        self.client.force_login(self.host)

        self.client.get(self.configuration_url(event))
        first_table_id = event.mesas.get().id
        self.client.get(self.configuration_url(event))

        self.assertEqual(event.mesas.count(), 1)
        self.assertEqual(event.mesas.get().id, first_table_id)

    def test_self_service_event_with_existing_tables_gets_no_extra_table(self):
        event = self.create_event(
            source=Evento.CreationSource.SELF_SERVICE,
            host=self.host,
        )
        existing = Mesa.objects.create(evento=event, numero=1, nombre="VIP")
        self.client.force_login(self.host)

        response = self.client.get(self.configuration_url(event))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(event.mesas.values_list("id", flat=True)),
            [existing.id],
        )
        existing.refresh_from_db()
        self.assertEqual(existing.numero, 1)
        self.assertEqual(existing.nombre, "VIP")

    def test_legacy_and_admin_events_do_not_get_automatic_table(self):
        self.client.force_login(self.host)
        events = (
            self.create_event(name="Legacy", source=None, host=self.host),
            self.create_event(
                name="Administrativo",
                source=Evento.CreationSource.ADMIN,
                host=self.host,
            ),
        )

        for event in events:
            with self.subTest(source=event.creation_source):
                response = self.client.get(self.configuration_url(event))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(event.mesas.exists())

    def test_authorized_host_can_view_and_add_tables(self):
        event = self.create_event(
            source=Evento.CreationSource.SELF_SERVICE,
            host=self.host,
        )
        self.client.force_login(self.host)

        page = self.client.get(self.configuration_url(event))
        update = self.client.post(
            self.configuration_url(event),
            {"numero_mesas": 3},
        )

        self.assertEqual(page.status_code, 200)
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.json()["total_mesas"], 3)
        self.assertEqual(
            list(event.mesas.values_list("numero", flat=True)),
            [1, 2, 3],
        )

    def test_other_event_host_cannot_view_or_modify_tables(self):
        event = self.create_event(
            source=Evento.CreationSource.SELF_SERVICE,
            host=self.host,
        )
        self.client.force_login(self.other_host)

        page = self.client.get(self.configuration_url(event))
        update = self.client.post(
            self.configuration_url(event),
            {"numero_mesas": 3},
        )

        self.assertEqual(page.status_code, 404)
        self.assertEqual(update.status_code, 404)
        self.assertFalse(event.mesas.exists())

    def test_superuser_can_access_table_configuration(self):
        event = self.create_event(
            source=Evento.CreationSource.SELF_SERVICE,
            host=self.host,
        )
        self.client.force_login(self.superuser)

        response = self.client.get(self.configuration_url(event))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(event.mesas.count(), 1)

    def test_dashboard_button_points_to_correct_event(self):
        event = self.create_event(
            source=Evento.CreationSource.SELF_SERVICE,
            host=self.host,
        )
        self.client.force_login(self.host)

        response = self.client.get(self.configuration_url(event))

        dashboard_url = reverse("dashboard_evento", args=[event.slug])
        self.assertContains(response, "← Volver al dashboard del evento")
        self.assertContains(response, f'href="{dashboard_url}"')

    def test_explanatory_copy_is_visible(self):
        event = self.create_event(
            source=Evento.CreationSource.SELF_SERVICE,
            host=self.host,
        )
        self.client.force_login(self.host)

        response = self.client.get(self.configuration_url(event))

        self.assertContains(response, "Configura los accesos de tu evento")
        self.assertContains(response, "Tu evento necesita al menos un acceso")
        self.assertContains(response, "puedes dejar una sola mesa")
        self.assertContains(response, "agrega tantas como necesites")

    def test_existing_add_and_rename_flow_remains_available(self):
        event = self.create_event(
            source=Evento.CreationSource.SELF_SERVICE,
            host=self.host,
        )
        self.client.force_login(self.host)
        self.client.get(self.configuration_url(event))
        self.client.post(
            self.configuration_url(event),
            {"numero_mesas": 2},
        )
        second = event.mesas.get(numero=2)

        rename = self.client.post(
            reverse(
                "actualizar_mesa",
                args=[event.slug, second.id],
            ),
            {"nombre": "Familia López"},
        )

        self.assertEqual(rename.status_code, 200)
        self.assertEqual(event.mesas.count(), 2)
        second.refresh_from_db()
        self.assertEqual(second.nombre, "Familia López")
