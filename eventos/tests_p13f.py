from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import AceptacionLegal, Evento


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class GestionCuentaTests(TestCase):
    password = "Clave-Segura-2026"

    def setUp(self):
        self.usuario = User.objects.create_user(
            username="cuenta@example.com",
            email="cuenta@example.com",
            password=self.password,
            first_name="Ana",
            last_name="López",
        )
        self.account_url = reverse("mi_cuenta")

    def test_mi_cuenta_requiere_login(self):
        response = self.client.get(self.account_url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login_anfitrion"), response.url)

    def test_usuario_autenticado_ve_su_informacion_y_navegacion(self):
        self.client.force_login(self.usuario)

        response = self.client.get(self.account_url)

        self.assertContains(response, "Mi cuenta")
        self.assertContains(response, "Ana")
        self.assertContains(response, "López")
        self.assertContains(response, "cuenta@example.com")
        self.assertContains(response, reverse("cambiar_password"))
        self.assertContains(response, reverse("logout_anfitrion"))
        self.assertContains(response, "Desactivar cuenta")
        self.assertNotContains(response, "Restablecer contraseña")
        self.assertNotContains(response, reverse("password_reset"))

        dashboard = self.client.get(reverse("dashboard_anfitrion"))
        self.assertContains(dashboard, self.account_url)

    def test_login_conserva_enlace_de_recuperacion(self):
        response = self.client.get(reverse("login_anfitrion"))

        self.assertContains(response, "¿Olvidaste tu contraseña?")
        self.assertContains(response, reverse("password_reset"))

    def test_formulario_cambio_password_muestra_textos_en_espanol(self):
        self.client.force_login(self.usuario)

        response = self.client.get(reverse("cambiar_password"))

        self.assertContains(response, "Contraseña actual")
        self.assertContains(response, "Nueva contraseña")
        self.assertContains(response, "Confirmar nueva contraseña")
        self.assertContains(response, "La contraseña no puede")
        self.assertNotContains(response, "Old password")
        self.assertNotContains(response, "New password confirmation")

    def test_error_password_actual_incorrecta_se_muestra_en_espanol(self):
        self.client.force_login(self.usuario)

        response = self.client.post(
            reverse("cambiar_password"),
            {
                "old_password": "incorrecta",
                "new_password1": "Nueva-Clave-Segura-2026",
                "new_password2": "Nueva-Clave-Segura-2026",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La contraseña actual es incorrecta")

    def test_usuario_actualiza_nombre_y_apellido(self):
        self.client.force_login(self.usuario)

        response = self.client.post(
            self.account_url,
            {"first_name": "María", "last_name": "García"},
        )

        self.assertRedirects(response, self.account_url)
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.first_name, "María")
        self.assertEqual(self.usuario.last_name, "García")

    def test_campos_forjados_no_modifican_identidad_ni_permisos(self):
        self.client.force_login(self.usuario)

        self.client.post(
            self.account_url,
            {
                "first_name": "Ana actualizada",
                "last_name": "López",
                "username": "atacante@example.com",
                "email": "atacante@example.com",
                "is_active": "false",
                "is_staff": "true",
                "is_superuser": "true",
            },
        )

        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.first_name, "Ana actualizada")
        self.assertEqual(self.usuario.username, "cuenta@example.com")
        self.assertEqual(self.usuario.email, "cuenta@example.com")
        self.assertTrue(self.usuario.is_active)
        self.assertFalse(self.usuario.is_staff)
        self.assertFalse(self.usuario.is_superuser)

    def test_usuario_no_puede_modificar_a_otro_usuario(self):
        otro = User.objects.create_user(
            username="otro@example.com",
            first_name="Otro",
            last_name="Usuario",
        )
        self.client.force_login(self.usuario)

        self.client.post(
            self.account_url,
            {
                "first_name": "Nombre propio",
                "last_name": "Actualizado",
                "user_id": otro.pk,
            },
        )

        otro.refresh_from_db()
        self.assertEqual(otro.first_name, "Otro")
        self.assertEqual(otro.last_name, "Usuario")

    def test_cambio_password_funciona_y_conserva_sesion(self):
        self.client.force_login(self.usuario)
        nueva_password = "Nueva-Clave-Segura-2026"

        response = self.client.post(
            reverse("cambiar_password"),
            {
                "old_password": self.password,
                "new_password1": nueva_password,
                "new_password2": nueva_password,
            },
        )

        self.assertRedirects(response, self.account_url)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(nueva_password))
        self.assertEqual(
            int(self.client.session["_auth_user_id"]),
            self.usuario.pk,
        )
        self.assertEqual(self.client.get(self.account_url).status_code, 200)

    def test_desactivacion_get_solo_muestra_confirmacion(self):
        self.client.force_login(self.usuario)

        response = self.client.get(reverse("desactivar_cuenta"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Esta acción no es una eliminación definitiva")
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.is_active)

    def test_desactivacion_exige_confirmacion_explicita(self):
        self.client.force_login(self.usuario)

        response = self.client.post(reverse("desactivar_cuenta"), {})

        self.assertEqual(response.status_code, 400)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.is_active)
        self.assertIn("_auth_user_id", self.client.session)

    def test_desactivacion_requiere_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.usuario)

        response = csrf_client.post(
            reverse("desactivar_cuenta"),
            {"confirmar": "si"},
        )

        self.assertEqual(response.status_code, 403)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.is_active)

    def test_desactivacion_preserva_evento_anfitrion_legal_y_procedencia(self):
        evento = Evento.objects.create(
            nombre="Evento preservado",
            slug="evento-preservado",
            fecha=date(2026, 12, 12),
            creation_source=Evento.CreationSource.SELF_SERVICE,
            self_service_created_by=self.usuario,
        )
        evento.anfitriones.add(self.usuario)
        aceptacion = AceptacionLegal.objects.create(
            usuario=self.usuario,
            version_terminos="terms-v1",
            version_privacidad="privacy-v1",
        )
        self.client.force_login(self.usuario)

        response = self.client.post(
            reverse("desactivar_cuenta"),
            {"confirmar": "si"},
        )

        self.assertRedirects(response, reverse("cuenta_desactivada"))
        self.usuario.refresh_from_db()
        evento.refresh_from_db()
        self.assertFalse(self.usuario.is_active)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(Evento.objects.filter(pk=evento.pk).exists())
        self.assertTrue(evento.anfitriones.filter(pk=self.usuario.pk).exists())
        self.assertTrue(AceptacionLegal.objects.filter(pk=aceptacion.pk).exists())
        self.assertEqual(evento.self_service_created_by_id, self.usuario.pk)
        confirmation = self.client.get(reverse("cuenta_desactivada"))
        self.assertContains(confirmation, "Tus datos y eventos no se han eliminado")

    def test_superusuario_no_puede_desactivarse_por_este_flujo(self):
        superusuario = User.objects.create_superuser(
            username="admin-p13f@example.com",
            email="admin-p13f@example.com",
            password=self.password,
        )
        self.client.force_login(superusuario)

        response = self.client.post(
            reverse("desactivar_cuenta"),
            {"confirmar": "si"},
        )

        self.assertEqual(response.status_code, 403)
        superusuario.refresh_from_db()
        self.assertTrue(superusuario.is_active)
        self.assertTrue(superusuario.is_superuser)

    def test_superusuario_conserva_navegacion_sin_mi_cuenta(self):
        superusuario = User.objects.create_superuser(
            username="admin-nav-p13f@example.com",
            email="admin-nav-p13f@example.com",
            password=self.password,
        )
        self.client.force_login(superusuario)

        account = self.client.get(self.account_url)
        dashboard = self.client.get(reverse("dashboard"))

        self.assertRedirects(account, reverse("dashboard"))
        self.assertNotContains(dashboard, self.account_url)
