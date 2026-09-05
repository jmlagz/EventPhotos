from datetime import date, timedelta
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import Evento, Foto, Mesa


class SlideshowBackendTests(TestCase):
    def setUp(self):
        self.counter = 0

    def create_event(self, *, estado=Evento.Estado.ACTIVE, **kwargs):
        self.counter += 1
        return Evento.objects.create(
            nombre=f"Evento slideshow {self.counter}",
            fecha=date(2026, 9, 4),
            estado=estado,
            **kwargs,
        )

    def create_photo(
        self,
        evento,
        *,
        eliminada=False,
        estado=Foto.Estado.APROBADA,
    ):
        self.counter += 1
        mesa, _ = Mesa.objects.get_or_create(
            evento=evento,
            numero=1,
        )
        return Foto.objects.create(
            evento=evento,
            mesa=mesa,
            object_key=f"eventos/{evento.slug}/fotos/{self.counter}.jpg",
            nombre_original=f"privada-{self.counter}.jpg",
            content_type="image/jpeg",
            tamaño=1024,
            hash_sha256=f"{self.counter:064x}",
            uploader_hash=f"uploader-{self.counter}",
            estado=estado,
            eliminada_at=timezone.now() if eliminada else None,
        )

    def player_url(self, evento):
        return reverse("slideshow", args=[evento.slug])

    def photos_url(self, evento):
        return reverse("slideshow_photos", args=[evento.slug])

    def test_player_active_available_returns_200(self):
        evento = self.create_event()

        response = self.client.get(self.player_url(evento))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Slideshow — {evento.nombre}")

    def test_player_closed_available_returns_200(self):
        evento = self.create_event(estado=Evento.Estado.CLOSED)

        response = self.client.get(self.player_url(evento))

        self.assertEqual(response.status_code, 200)

    def test_player_draft_returns_404(self):
        evento = self.create_event(estado=Evento.Estado.DRAFT)

        response = self.client.get(self.player_url(evento))

        self.assertEqual(response.status_code, 404)

    def test_player_archived_returns_404(self):
        evento = self.create_event(estado=Evento.Estado.ARCHIVED)

        response = self.client.get(self.player_url(evento))

        self.assertEqual(response.status_code, 404)

    def test_player_expired_availability_returns_404(self):
        evento = self.create_event(
            available_until=timezone.now() - timedelta(seconds=1),
        )

        response = self.client.get(self.player_url(evento))

        self.assertEqual(response.status_code, 404)

    def test_player_does_not_create_or_modify_session(self):
        evento = self.create_event()

        response = self.client.get(self.player_url(evento))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)
        self.assertEqual(Session.objects.count(), 0)

    @patch("eventos.views.generar_url_lectura")
    def test_player_does_not_query_photos_or_generate_urls(self, generate_url):
        evento = self.create_event()
        self.create_photo(evento)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.player_url(evento))

        self.assertEqual(response.status_code, 200)
        generate_url.assert_not_called()
        self.assertFalse(
            any("eventos_foto" in query["sql"].lower() for query in queries)
        )

    @patch(
        "eventos.views.generar_url_lectura",
        side_effect=lambda key: f"https://signed.test/{key.rsplit('/', 1)[-1]}",
    )
    def test_endpoint_initial_get_returns_photos_in_ascending_id_order(self, _sign):
        evento = self.create_event()
        first = self.create_photo(evento)
        second = self.create_photo(evento)
        third = self.create_photo(evento)

        response = self.client.get(self.photos_url(evento))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [photo["id"] for photo in response.json()["photos"]],
            [first.id, second.id, third.id],
        )

    @patch("eventos.views.generar_url_lectura", return_value="https://signed.test/photo")
    def test_endpoint_excludes_logically_deleted_photos(self, _sign):
        evento = self.create_event()
        visible = self.create_photo(evento)
        self.create_photo(evento, eliminada=True)

        response = self.client.get(self.photos_url(evento))

        self.assertEqual(
            [photo["id"] for photo in response.json()["photos"]],
            [visible.id],
        )

    @patch("eventos.views.generar_url_lectura", return_value="https://signed.test/photo")
    def test_endpoint_preserves_current_album_photo_state_policy(self, _sign):
        evento = self.create_event()
        photos = [
            self.create_photo(evento, estado=estado)
            for estado in (
                Foto.Estado.PENDIENTE,
                Foto.Estado.APROBADA,
                Foto.Estado.RECHAZADA,
            )
        ]

        response = self.client.get(self.photos_url(evento))

        self.assertEqual(
            [photo["id"] for photo in response.json()["photos"]],
            [photo.id for photo in photos],
        )

    @patch("eventos.views.generar_url_lectura", return_value="https://signed.test/photo")
    def test_endpoint_after_id_returns_only_greater_ids(self, _sign):
        evento = self.create_event()
        first = self.create_photo(evento)
        second = self.create_photo(evento)
        third = self.create_photo(evento)

        response = self.client.get(
            self.photos_url(evento),
            {"after_id": second.id},
        )

        self.assertEqual(
            [photo["id"] for photo in response.json()["photos"]],
            [third.id],
        )
        self.assertNotEqual(first.id, third.id)

    def test_endpoint_rejects_invalid_cursors_with_controlled_json(self):
        evento = self.create_event()

        for value in (
            "invalid",
            "",
            "0",
            "-1",
            "1.5",
            "9223372036854775808",
        ):
            with self.subTest(value=value):
                response = self.client.get(
                    self.photos_url(evento),
                    {"after_id": value},
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json(),
                    {"error": "El cursor after_id no es válido."},
                )

    def create_many_photos(self, evento, total):
        mesa = Mesa.objects.create(evento=evento, numero=1)
        now = timezone.now()
        photos = []
        for index in range(total):
            value = index + 1
            photos.append(
                Foto(
                    evento=evento,
                    mesa=mesa,
                    object_key=f"eventos/{evento.slug}/fotos/{value}.jpg",
                    nombre_original=f"foto-{value}.jpg",
                    content_type="image/jpeg",
                    tamaño=1024,
                    hash_sha256=f"{value:064x}",
                    uploader_hash=f"uploader-{value}",
                    creada_en=now,
                )
            )
        return Foto.objects.bulk_create(photos)

    @patch("eventos.views.generar_url_lectura", return_value="https://signed.test/photo")
    def test_endpoint_returns_at_most_100_photos(self, _sign):
        evento = self.create_event()
        self.create_many_photos(evento, 101)

        response = self.client.get(self.photos_url(evento))

        self.assertEqual(len(response.json()["photos"]), 100)

    @patch("eventos.views.generar_url_lectura", return_value="https://signed.test/photo")
    def test_endpoint_ignores_client_supplied_limit(self, _sign):
        evento = self.create_event()
        self.create_many_photos(evento, 3)

        response = self.client.get(
            self.photos_url(evento),
            {"limit": 1},
        )

        self.assertEqual(len(response.json()["photos"]), 3)

    @patch("eventos.views.generar_url_lectura", return_value="https://signed.test/photo")
    def test_endpoint_has_more_is_true_only_when_more_than_100_exist(self, _sign):
        evento = self.create_event()
        photos = self.create_many_photos(evento, 101)

        first_page = self.client.get(self.photos_url(evento))
        second_page = self.client.get(
            self.photos_url(evento),
            {"after_id": photos[99].id},
        )

        self.assertIs(first_page.json()["has_more"], True)
        self.assertIs(second_page.json()["has_more"], False)

    @patch("eventos.views.generar_url_lectura", return_value="https://signed.test/photo")
    def test_endpoint_next_after_id_is_last_returned_photo(self, _sign):
        evento = self.create_event()
        first = self.create_photo(evento)
        second = self.create_photo(evento)

        response = self.client.get(self.photos_url(evento))

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(response.json()["next_after_id"], second.id)

    def test_endpoint_initial_empty_result_has_null_cursor(self):
        evento = self.create_event()

        response = self.client.get(self.photos_url(evento))

        self.assertEqual(response.json()["photos"], [])
        self.assertIsNone(response.json()["next_after_id"])

    def test_endpoint_empty_result_preserves_received_cursor(self):
        evento = self.create_event()

        response = self.client.get(
            self.photos_url(evento),
            {"after_id": 987},
        )

        self.assertEqual(response.json()["photos"], [])
        self.assertEqual(response.json()["next_after_id"], 987)

    def test_endpoint_disables_response_caching(self):
        evento = self.create_event()

        response = self.client.get(self.photos_url(evento))

        self.assertEqual(response["Cache-Control"], "no-store")

    def test_endpoint_rejects_unavailable_event_states_and_expiration(self):
        cases = (
            (Evento.Estado.DRAFT, None),
            (Evento.Estado.ARCHIVED, None),
            (
                Evento.Estado.ACTIVE,
                timezone.now() - timedelta(seconds=1),
            ),
        )

        for estado, available_until in cases:
            with self.subTest(estado=estado, available_until=available_until):
                evento = self.create_event(
                    estado=estado,
                    available_until=available_until,
                )

                response = self.client.get(self.photos_url(evento))

                self.assertEqual(response.status_code, 404)

    @patch("eventos.views.generar_url_lectura", return_value="https://signed.test/opaque")
    def test_endpoint_does_not_expose_sensitive_photo_fields(self, _sign):
        evento = self.create_event()
        photo = self.create_photo(evento)

        response = self.client.get(self.photos_url(evento))

        payload = response.json()
        self.assertEqual(
            set(payload["photos"][0]),
            {"id", "url", "created_at"},
        )
        serialized = response.content.decode("utf-8")
        for value in (
            photo.object_key,
            photo.nombre_original,
            photo.uploader_hash,
            photo.mesa.token,
            photo.mesa.codigo_acceso,
        ):
            self.assertNotIn(value, serialized)

    @patch("eventos.views.generar_url_lectura")
    def test_endpoint_signs_each_returned_photo(self, generate_url):
        evento = self.create_event()
        photos = [self.create_photo(evento) for _ in range(3)]
        generate_url.side_effect = [
            f"https://signed.test/{photo.id}" for photo in photos
        ]

        response = self.client.get(self.photos_url(evento))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [call.args[0] for call in generate_url.call_args_list],
            [photo.object_key for photo in photos],
        )

    @patch("eventos.r2.get_r2_client")
    def test_endpoint_does_not_perform_r2_head_or_get(self, get_r2_client):
        evento = self.create_event()
        photo = self.create_photo(evento)
        r2 = Mock()
        r2.generate_presigned_url.return_value = "https://signed.test/photo"
        get_r2_client.return_value = r2

        response = self.client.get(self.photos_url(evento))

        self.assertEqual(response.status_code, 200)
        r2.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={
                "Bucket": settings.R2_BUCKET_NAME,
                "Key": photo.object_key,
            },
            ExpiresIn=3600,
        )
        r2.head_object.assert_not_called()
        r2.get_object.assert_not_called()

    @patch(
        "eventos.views.generar_url_lectura",
        side_effect=RuntimeError("SECRET_SIGNING_FAILURE"),
    )
    def test_endpoint_signing_failure_is_controlled_and_not_partial(self, _sign):
        evento = self.create_event()
        self.create_photo(evento)
        self.create_photo(evento)

        response = self.client.get(self.photos_url(evento))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"error": "No fue posible preparar las fotos."},
        )
        self.assertNotIn("SECRET_SIGNING_FAILURE", response.content.decode("utf-8"))
        self.assertNotIn("photos", response.json())
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_player_rejects_post_with_405(self):
        evento = self.create_event()

        response = self.client.post(self.player_url(evento))

        self.assertEqual(response.status_code, 405)

    def test_endpoint_rejects_post_with_405(self):
        evento = self.create_event()

        response = self.client.post(self.photos_url(evento))

        self.assertEqual(response.status_code, 405)
