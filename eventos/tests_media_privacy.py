from datetime import date, timedelta

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Evento
from .views import MEDIA_UNLOCK_SESSION_KEY


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class MediaPrivacyTests(TestCase):
    password = "contraseña-compartida"

    def setUp(self):
        self.evento = self.create_evento()

    def create_evento(self, **overrides):
        fields = {
            "nombre": f"Evento privado {Evento.objects.count() + 1}",
            "fecha": date(2026, 9, 19),
            "estado": Evento.Estado.ACTIVE,
            "media_access": Evento.MediaAccess.PASSWORD,
            "media_password_hash": make_password(self.password),
        }
        fields.update(overrides)
        return Evento.objects.create(**fields)

    def album_url(self, evento=None):
        return reverse("album_publico", args=[(evento or self.evento).slug])

    def album_photos_url(self, evento=None):
        return reverse(
            "album_publico_fotos",
            args=[(evento or self.evento).slug],
        )

    def slideshow_url(self, evento=None):
        return reverse("slideshow", args=[(evento or self.evento).slug])

    def slideshow_photos_url(self, evento=None):
        return reverse(
            "slideshow_photos",
            args=[(evento or self.evento).slug],
        )

    def slideshow_promos_url(self, evento=None):
        return reverse(
            "slideshow_promos",
            args=[(evento or self.evento).slug],
        )

    def unlock_url(self, evento=None):
        return reverse("desbloquear_media", args=[(evento or self.evento).slug])

    def unlock(self, *, evento=None, password=None, destino="album", client=None):
        return (client or self.client).post(
            self.unlock_url(evento),
            {
                "password": password or self.password,
                "destino": destino,
            },
        )

    def assert_requires_unlock(self, response, destino):
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"{self.unlock_url()}?destino={destino}",
        )
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_event_default_is_public_and_temporal_alias_is_preserved(self):
        evento = Evento.objects.create(
            nombre="Evento compatible",
            fecha=date(2026, 9, 19),
            estado=Evento.Estado.ACTIVE,
        )

        self.assertEqual(evento.media_access, Evento.MediaAccess.PUBLIC)
        self.assertEqual(evento.media_password_hash, "")
        self.assertTrue(evento.media_disponible())
        self.assertEqual(
            evento.media_disponible(),
            evento.permite_album_publico(),
        )

    def test_public_album_is_accessible_anonymously(self):
        self.evento.media_access = Evento.MediaAccess.PUBLIC
        self.evento.save(update_fields=["media_access"])

        response = self.client.get(self.album_url())

        self.assertEqual(response.status_code, 200)

    def test_public_slideshow_is_accessible_anonymously(self):
        self.evento.media_access = Evento.MediaAccess.PUBLIC
        self.evento.save(update_fields=["media_access"])

        response = self.client.get(self.slideshow_url())

        self.assertEqual(response.status_code, 200)

    def test_password_album_redirects_to_unlock_form(self):
        response = self.client.get(self.album_url())

        self.assert_requires_unlock(response, "album")
        form = self.client.get(response.url)
        self.assertContains(form, "Este contenido está protegido")
        self.assertContains(
            form,
            "Ingresa la contraseña del evento para ver las fotos.",
        )

    def test_password_slideshow_redirects_to_shared_unlock_form(self):
        response = self.client.get(self.slideshow_url())

        self.assert_requires_unlock(response, "slideshow")
        self.assertContains(
            self.client.get(response.url),
            'name="destino" value="slideshow"',
            html=False,
        )

    def test_correct_password_unlocks_album_and_slideshow(self):
        response = self.unlock()

        self.assertRedirects(
            response,
            self.album_url(),
            fetch_redirect_response=False,
        )
        self.assertEqual(self.client.get(self.album_url()).status_code, 200)
        self.assertEqual(self.client.get(self.slideshow_url()).status_code, 200)

    def test_incorrect_password_does_not_unlock(self):
        response = self.unlock(password="incorrecta")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La contraseña no es correcta.")
        self.assertNotIn(MEDIA_UNLOCK_SESSION_KEY, self.client.session)
        self.assert_requires_unlock(self.client.get(self.album_url()), "album")

    def test_password_mode_without_hash_rejects_empty_password(self):
        self.evento.media_password_hash = ""
        self.evento.save(update_fields=["media_password_hash"])

        response = self.client.post(
            self.unlock_url(),
            {
                "password": "",
                "destino": "album",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La contraseña no es correcta.")
        self.assertNotIn(MEDIA_UNLOCK_SESSION_KEY, self.client.session)
        self.assert_requires_unlock(self.client.get(self.album_url()), "album")
        self.assert_requires_unlock(
            self.client.get(self.slideshow_url()),
            "slideshow",
        )

    def test_session_contains_only_version_fingerprint(self):
        self.unlock()

        desbloqueos = self.client.session[MEDIA_UNLOCK_SESSION_KEY]
        serialized = repr(dict(self.client.session))
        self.assertEqual(set(desbloqueos), {str(self.evento.pk)})
        self.assertNotEqual(
            desbloqueos[str(self.evento.pk)],
            self.evento.media_password_hash,
        )
        self.assertNotIn(self.password, serialized)
        self.assertNotIn(self.evento.media_password_hash, serialized)

    def test_changing_password_hash_invalidates_previous_unlock(self):
        self.unlock()
        self.assertEqual(self.client.get(self.album_url()).status_code, 200)

        self.evento.media_password_hash = make_password("contraseña-nueva")
        self.evento.save(update_fields=["media_password_hash"])

        self.assert_requires_unlock(self.client.get(self.album_url()), "album")
        self.assertEqual(
            self.client.get(self.slideshow_photos_url()).status_code,
            403,
        )

    def test_event_host_enters_album_and_slideshow_without_password(self):
        host = User.objects.create_user(username="host", password="test")
        self.evento.anfitriones.add(host)
        self.client.force_login(host)

        self.assertEqual(self.client.get(self.album_url()).status_code, 200)
        self.assertEqual(self.client.get(self.slideshow_url()).status_code, 200)
        self.assertNotIn(MEDIA_UNLOCK_SESSION_KEY, self.client.session)

    def test_superuser_enters_without_password(self):
        admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="test",
        )
        self.client.force_login(admin)

        self.assertEqual(self.client.get(self.album_url()).status_code, 200)
        self.assertEqual(self.client.get(self.slideshow_url()).status_code, 200)

    def test_unrelated_authenticated_user_still_requires_password(self):
        other = User.objects.create_user(username="other", password="test")
        self.client.force_login(other)

        self.assert_requires_unlock(self.client.get(self.album_url()), "album")
        self.assert_requires_unlock(
            self.client.get(self.slideshow_url()),
            "slideshow",
        )

    def test_album_json_cannot_bypass_password(self):
        response = self.client.get(self.album_photos_url(), {"cursor": "x"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "media_access_required")
        self.assertNotIn("photos", response.json())

    def test_slideshow_photos_cannot_bypass_password(self):
        response = self.client.get(self.slideshow_photos_url())

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "media_access_required")
        self.assertNotIn("photos", response.json())

    def test_slideshow_promos_cannot_bypass_password(self):
        response = self.client.get(self.slideshow_promos_url())

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "media_access_required")
        self.assertNotIn("promos", response.json())

    def test_expired_event_cannot_be_unlocked_with_correct_password(self):
        self.evento.available_until = timezone.now() - timedelta(seconds=1)
        self.evento.save(update_fields=["available_until"])

        response = self.unlock()

        self.assertEqual(response.status_code, 404)
        self.assertNotIn(MEDIA_UNLOCK_SESSION_KEY, self.client.session)

    def test_draft_and_archived_events_do_not_expose_media(self):
        for estado in (Evento.Estado.DRAFT, Evento.Estado.ARCHIVED):
            evento = self.create_evento(estado=estado)
            urls = (
                self.album_url(evento),
                self.album_photos_url(evento),
                self.slideshow_url(evento),
                self.slideshow_photos_url(evento),
                self.slideshow_promos_url(evento),
                self.unlock_url(evento),
            )
            for url in urls:
                with self.subTest(estado=estado, url=url):
                    self.assertEqual(self.client.get(url).status_code, 404)

    def test_password_hash_is_not_exposed_in_responses(self):
        responses = (
            self.client.get(self.album_url()),
            self.client.get(self.slideshow_url()),
            self.client.get(self.unlock_url()),
            self.client.get(self.album_photos_url()),
            self.client.get(self.slideshow_photos_url()),
            self.client.get(self.slideshow_promos_url()),
        )

        for response in responses:
            with self.subTest(status=response.status_code):
                self.assertNotIn(
                    self.evento.media_password_hash,
                    response.content.decode(),
                )
                self.assertNotIn(self.password, response.content.decode())

    def test_admin_change_form_does_not_expose_password_hash(self):
        admin = User.objects.create_superuser(
            username="admin-hash",
            email="admin-hash@example.com",
            password="test",
        )
        self.client.force_login(admin)

        response = self.client.get(
            reverse("admin:eventos_evento_change", args=[self.evento.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.evento.media_password_hash)
        self.assertNotContains(response, "media_access")
        self.assertNotContains(response, "media_password_hash")

    def test_unlock_is_scoped_to_one_event(self):
        otro_evento = self.create_evento(nombre="Otro evento privado")
        self.unlock()

        self.assertEqual(self.client.get(self.album_url()).status_code, 200)
        response = self.client.get(self.album_url(otro_evento))
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.unlock_url(otro_evento), response.url)

    def test_unlock_destination_is_restricted_to_known_internal_routes(self):
        response = self.unlock(destino="https://evil.example/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.album_url())

    def test_unlock_post_keeps_csrf_protection(self):
        csrf_client = Client(enforce_csrf_checks=True)

        response = self.unlock(client=csrf_client)

        self.assertEqual(response.status_code, 403)
        self.assertNotIn(MEDIA_UNLOCK_SESSION_KEY, csrf_client.session)
