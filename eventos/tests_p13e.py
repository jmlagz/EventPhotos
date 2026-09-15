from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.http import urlsafe_base64_encode
from django.utils.text import slugify

from .models import AceptacionLegal, Evento
from .services.account_activation import account_activation_token_generator


P13E_SETTINGS = {
    "SELF_SERVICE_ENABLED": True,
    "LEGAL_TERMS_VERSION": "terms-p13e-v1",
    "LEGAL_PRIVACY_VERSION": "privacy-p13e-v1",
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "PASSWORD_HASHERS": ["django.contrib.auth.hashers.MD5PasswordHasher"],
}


@override_settings(**P13E_SETTINGS)
class OnboardingNavigationTests(TestCase):
    password = "clave-segura"

    def setUp(self):
        self.usuario = User.objects.create_user(
            username="host-p13e@example.com",
            email="host-p13e@example.com",
            password=self.password,
        )

    def crear_evento(self, nombre, usuario=None, **extra):
        evento = Evento.objects.create(
            nombre=nombre,
            slug=slugify(nombre),
            fecha=date(2026, 12, 12),
            **extra,
        )
        evento.anfitriones.add(usuario or self.usuario)
        return evento

    def login_post(self, usuario=None):
        usuario = usuario or self.usuario
        return self.client.post(
            reverse("login_anfitrion"),
            {
                "username": usuario.username,
                "password": self.password,
            },
        )

    def test_usuario_sin_eventos_inicia_en_mis_eventos(self):
        response = self.login_post()

        self.assertRedirects(response, reverse("dashboard_anfitrion"))

    def test_usuario_con_un_evento_inicia_en_mis_eventos(self):
        evento = self.crear_evento("Evento único")

        response = self.login_post()

        self.assertRedirects(response, reverse("dashboard_anfitrion"))
        self.assertNotEqual(
            response.url,
            reverse("dashboard_evento", args=[evento.slug]),
        )

    def test_usuario_con_varios_eventos_inicia_en_mis_eventos(self):
        self.crear_evento("Evento uno")
        self.crear_evento("Evento dos")

        response = self.login_post()

        self.assertRedirects(response, reverse("dashboard_anfitrion"))

    def test_usuario_ya_autenticado_con_un_evento_vuelve_a_mis_eventos(self):
        self.crear_evento("Evento autenticado")
        self.client.force_login(self.usuario)

        response = self.client.get(reverse("login_anfitrion"))

        self.assertRedirects(response, reverse("dashboard_anfitrion"))

    def test_superusuario_conserva_dashboard_global(self):
        superusuario = User.objects.create_superuser(
            username="admin-p13e@example.com",
            email="admin-p13e@example.com",
            password=self.password,
        )

        response = self.login_post(superusuario)

        self.assertRedirects(response, reverse("dashboard"))

    def test_activacion_publica_exitosa_llega_a_mis_eventos(self):
        usuario = User.objects.create_user(
            username="pending-p13e@example.com",
            email="pending-p13e@example.com",
            password=self.password,
            is_active=False,
        )
        AceptacionLegal.objects.create(
            usuario=usuario,
            version_terminos="terms-p13e-v1",
            version_privacidad="privacy-p13e-v1",
        )
        uidb64 = urlsafe_base64_encode(str(usuario.pk).encode())
        token = account_activation_token_generator.make_token(usuario)

        response = self.client.get(
            reverse(
                "activar_cuenta_publica",
                kwargs={"uidb64": uidb64, "token": token},
            )
        )

        self.assertRedirects(response, reverse("dashboard_anfitrion"))
        self.assertEqual(Evento.objects.count(), 0)

    def test_estado_vacio_muestra_cta_de_creacion(self):
        self.client.force_login(self.usuario)

        response = self.client.get(reverse("dashboard_anfitrion"))

        self.assertContains(response, "Aún no tienes eventos")
        self.assertContains(response, "Crear evento")
        self.assertContains(response, reverse("crear_evento_autoservicio"))

    @override_settings(SELF_SERVICE_ENABLED=False)
    def test_flag_deshabilitado_oculta_cta(self):
        self.client.force_login(self.usuario)

        response = self.client.get(reverse("dashboard_anfitrion"))

        self.assertNotContains(response, "Crear evento")
        self.assertNotContains(
            response,
            reverse("crear_evento_autoservicio"),
        )

    def test_limite_autoservicio_oculta_cta_y_bloquea_ruta(self):
        self.crear_evento(
            "Evento autoservicio",
            creation_source=Evento.CreationSource.SELF_SERVICE,
            self_service_created_by=self.usuario,
        )
        self.client.force_login(self.usuario)

        dashboard = self.client.get(reverse("dashboard_anfitrion"))
        creation = self.client.get(reverse("crear_evento_autoservicio"))

        self.assertNotContains(dashboard, "Crear evento")
        self.assertRedirects(creation, reverse("dashboard_anfitrion"))

    def test_lista_ofrece_acceso_al_dashboard_del_evento_propio(self):
        evento = self.crear_evento("Evento propio")
        self.client.force_login(self.usuario)

        response = self.client.get(reverse("dashboard_anfitrion"))

        self.assertContains(response, evento.nombre)
        self.assertContains(
            response,
            reverse("dashboard_evento", args=[evento.slug]),
        )
        self.assertContains(response, "Entrar al evento")

    def test_usuario_no_puede_acceder_a_evento_ajeno(self):
        otro = User.objects.create_user(username="otro-p13e@example.com")
        evento_ajeno = self.crear_evento("Evento ajeno", usuario=otro)
        self.client.force_login(self.usuario)

        response = self.client.get(
            reverse("dashboard_evento", args=[evento_ajeno.slug])
        )

        self.assertEqual(response.status_code, 404)

    def test_next_externo_no_introduce_redireccion_abierta(self):
        response = self.client.post(
            f'{reverse("login_anfitrion")}?next=https://evil.example/path',
            {
                "username": self.usuario.username,
                "password": self.password,
            },
        )

        self.assertRedirects(response, reverse("dashboard_anfitrion"))

    def test_dashboard_evento_muestra_vuelta_a_mis_eventos(self):
        evento = self.crear_evento("Evento navegable")
        self.client.force_login(self.usuario)

        response = self.client.get(
            reverse("dashboard_evento", args=[evento.slug])
        )

        self.assertContains(
            response,
            f'<a href="{reverse("dashboard_anfitrion")}">Mis eventos</a>',
            html=True,
        )
