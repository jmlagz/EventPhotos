from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Evento
from .services.event_self_service import (
    LimiteEventosAutoservicioAlcanzado,
    crear_evento_autoservicio,
)


@override_settings(
    SELF_SERVICE_ENABLED=True,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class CreacionEventoAutoservicioTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            username="ana@example.com",
            email="ana@example.com",
            password="clave-segura",
        )
        self.url = reverse("crear_evento_autoservicio")
        self.data = {
            "nombre": "Boda de Ana",
            "tipo": Evento.Tipo.BODA,
            "fecha": "2026-12-12",
        }

    def login(self):
        self.client.force_login(self.usuario)

    def test_usuario_activo_crea_primer_evento(self):
        self.login()

        response = self.client.post(self.url, self.data)

        evento = Evento.objects.get()
        self.assertRedirects(
            response,
            reverse("dashboard_evento", kwargs={"slug": evento.slug}),
        )
        self.assertEqual(evento.estado, Evento.Estado.DRAFT)
        self.assertEqual(evento.configuracion_version, 1)
        self.assertEqual(
            evento.creation_source,
            Evento.CreationSource.SELF_SERVICE,
        )
        self.assertEqual(evento.self_service_created_by, self.usuario)
        self.assertTrue(evento.anfitriones.filter(pk=self.usuario.pk).exists())
        self.assertIsNone(evento.timezone)
        self.assertIsNone(evento.fin_planeado)
        self.assertIsNone(evento.upload_until)
        self.assertIsNone(evento.available_until)
        self.assertEqual(evento.mesas.count(), 0)

    def test_formulario_solo_expone_campos_minimos(self):
        self.login()

        response = self.client.get(self.url)

        self.assertEqual(
            list(response.context["form"].fields),
            ["nombre", "tipo", "fecha"],
        )

    def test_usuario_anonimo_es_redirigido_a_login(self):
        response = self.client.post(self.url, self.data)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login_anfitrion"), response.url)
        self.assertEqual(Evento.objects.count(), 0)

    def test_usuario_inactivo_es_rechazado_por_servicio(self):
        self.usuario.is_active = False
        self.usuario.save(update_fields=["is_active"])

        with self.assertRaises(PermissionDenied):
            crear_evento_autoservicio(
                usuario=self.usuario,
                nombre="Evento bloqueado",
                tipo=Evento.Tipo.OTRO,
                fecha=date(2026, 12, 12),
            )

        self.assertEqual(Evento.objects.count(), 0)

    def test_segundo_post_autoservicio_es_bloqueado(self):
        self.login()
        self.client.post(self.url, self.data)

        response = self.client.post(
            self.url,
            {**self.data, "nombre": "Segundo evento"},
        )

        self.assertRedirects(response, reverse("dashboard_anfitrion"))
        self.assertEqual(Evento.objects.count(), 1)

    def test_acceso_directo_al_formulario_se_bloquea_al_alcanzar_limite(self):
        crear_evento_autoservicio(
            usuario=self.usuario,
            nombre="Evento existente",
            tipo=Evento.Tipo.OTRO,
            fecha=date(2026, 12, 12),
        )
        self.login()

        response = self.client.get(self.url)

        self.assertRedirects(response, reverse("dashboard_anfitrion"))

    def test_evento_legacy_o_admin_no_bloquea_primer_autoservicio(self):
        legacy = Evento.objects.create(
            nombre="Evento previo",
            slug="evento-previo",
            fecha=date(2026, 10, 1),
        )
        legacy.anfitriones.add(self.usuario)
        admin = Evento.objects.create(
            nombre="Evento admin",
            slug="evento-admin",
            fecha=date(2026, 11, 1),
            creation_source=Evento.CreationSource.ADMIN,
        )
        admin.anfitriones.add(self.usuario)
        self.login()

        self.client.post(self.url, self.data)

        self.assertEqual(
            Evento.objects.filter(
                self_service_created_by=self.usuario
            ).count(),
            1,
        )

    @patch(
        "eventos.services.event_self_service."
        "_asignar_creador_como_anfitrion",
        side_effect=RuntimeError("fallo simulado"),
    )
    def test_fallo_al_asignar_anfitrion_revierte_evento(self, _asignar):
        with self.assertRaises(RuntimeError):
            crear_evento_autoservicio(
                usuario=self.usuario,
                nombre="Evento atómico",
                tipo=Evento.Tipo.OTRO,
                fecha=date(2026, 12, 12),
            )

        self.assertEqual(Evento.objects.count(), 0)

    def test_post_manipulado_no_puede_elegir_procedencia_ni_creador(self):
        otro = User.objects.create_user(username="otro@example.com")
        self.login()
        data = {
            **self.data,
            "creation_source": Evento.CreationSource.ADMIN,
            "self_service_created_by": otro.pk,
            "anfitriones": [otro.pk],
            "estado": Evento.Estado.ACTIVE,
            "configuracion_version": 99,
        }

        self.client.post(self.url, data)

        evento = Evento.objects.get()
        self.assertEqual(
            evento.creation_source,
            Evento.CreationSource.SELF_SERVICE,
        )
        self.assertEqual(evento.self_service_created_by, self.usuario)
        self.assertEqual(list(evento.anfitriones.all()), [self.usuario])
        self.assertEqual(evento.estado, Evento.Estado.DRAFT)
        self.assertEqual(evento.configuracion_version, 1)

    def test_estado_vacio_muestra_cta_y_ruta(self):
        self.login()

        response = self.client.get(reverse("dashboard_anfitrion"))

        self.assertContains(response, "Crear evento")
        self.assertContains(response, self.url)
        self.assertContains(response, "Empieza creando tu primer evento")

    def test_dashboard_oculta_cta_al_alcanzar_limite(self):
        crear_evento_autoservicio(
            usuario=self.usuario,
            nombre="Evento existente",
            tipo=Evento.Tipo.OTRO,
            fecha=date(2026, 12, 12),
        )
        self.login()

        response = self.client.get(reverse("dashboard_anfitrion"))

        self.assertNotContains(response, "Crear evento")

    @override_settings(SELF_SERVICE_ENABLED=False)
    def test_flag_desactivado_oculta_cta_y_ruta(self):
        self.login()

        dashboard = self.client.get(reverse("dashboard_anfitrion"))
        creation = self.client.get(self.url)

        self.assertNotContains(dashboard, "Crear evento")
        self.assertEqual(creation.status_code, 404)

    def test_superusuario_conserva_dashboard_global_y_no_usa_autoservicio(self):
        superusuario = User.objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="clave-segura",
        )
        self.client.force_login(superusuario)

        dashboard = self.client.get(reverse("dashboard_anfitrion"))
        creation = self.client.post(self.url, self.data)

        self.assertRedirects(dashboard, reverse("dashboard"))
        self.assertEqual(creation.status_code, 403)
        self.assertEqual(Evento.objects.count(), 0)

    def test_creacion_administrativa_nueva_marca_origen_admin(self):
        superusuario = User.objects.create_superuser(
            username="admin-source@example.com",
            email="admin-source@example.com",
            password="clave-segura",
        )
        self.client.force_login(superusuario)

        response = self.client.post(
            reverse("crear_evento"),
            {
                "nombre": "Evento administrativo",
                "tipo": Evento.Tipo.CORPORATIVO,
                "fecha": "2026-12-12",
                "descripcion": "",
                "mensaje_bienvenida": "",
                "timezone": "America/Mexico_City",
                "vigencia_meses": "6",
            },
        )

        self.assertEqual(response.status_code, 302)
        evento = Evento.objects.get()
        self.assertEqual(evento.creation_source, Evento.CreationSource.ADMIN)
        self.assertIsNone(evento.self_service_created_by)

    def test_evento_historico_puede_conservar_procedencia_nula(self):
        evento = Evento.objects.create(
            nombre="Evento histórico",
            slug="evento-historico",
            fecha=date(2026, 12, 12),
        )

        self.assertIsNone(evento.creation_source)
        self.assertIsNone(evento.self_service_created_by)

    def test_limite_se_basa_en_creador_aunque_deje_de_ser_anfitrion(self):
        evento = crear_evento_autoservicio(
            usuario=self.usuario,
            nombre="Evento existente",
            tipo=Evento.Tipo.OTRO,
            fecha=date(2026, 12, 12),
        )
        evento.anfitriones.remove(self.usuario)

        with self.assertRaises(LimiteEventosAutoservicioAlcanzado):
            crear_evento_autoservicio(
                usuario=self.usuario,
                nombre="Segundo evento",
                tipo=Evento.Tipo.OTRO,
                fecha=date(2027, 1, 1),
            )

        self.assertEqual(Evento.objects.count(), 1)
