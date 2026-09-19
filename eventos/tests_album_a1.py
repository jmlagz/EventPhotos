import hashlib
from datetime import date, timedelta
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Evento, Foto, Mesa


def _signed_url(object_key):
    return f"https://signed.test/{object_key.rsplit('/', 1)[-1]}"


class AlbumPublicoA1Tests(TestCase):
    def setUp(self):
        self.evento = self.create_evento()
        self.mesa = Mesa.objects.create(
            evento=self.evento,
            numero=1,
            nombre="Familia Privada",
        )

    def create_evento(self, **overrides):
        fields = {
            "nombre": "Álbum A1",
            "fecha": date(2026, 9, 17),
            "estado": Evento.Estado.ACTIVE,
        }
        fields.update(overrides)
        return Evento.objects.create(**fields)

    def create_photo(self, *, evento=None, mesa=None, index=0, **overrides):
        evento = evento or self.evento
        mesa = mesa or self.mesa
        fields = {
            "evento": evento,
            "mesa": mesa,
            "object_key": f"eventos/{evento.slug}/fotos/{index}.jpg",
            "nombre_original": f"privado-{index}.jpg",
            "content_type": "image/jpeg",
            "tamaño": 1024,
            "hash_sha256": f"{index:064x}",
            "uploader_hash": "otro-navegador",
            "estado": Foto.Estado.APROBADA,
        }
        fields.update(overrides)
        return Foto.objects.create(**fields)

    def create_photos(self, amount, *, evento=None, mesa=None):
        evento = evento or self.evento
        mesa = mesa or self.mesa
        start = Foto.objects.filter(evento=evento).count() + 1
        return [
            self.create_photo(
                evento=evento,
                mesa=mesa,
                index=start + index,
            )
            for index in range(amount)
        ]

    def album_url(self, evento=None):
        return reverse("album_publico", args=[(evento or self.evento).slug])

    def pages_url(self, evento=None):
        return reverse(
            "album_publico_fotos",
            args=[(evento or self.evento).slug],
        )

    @patch("eventos.views.generar_url_lectura", side_effect=_signed_url)
    def test_active_closed_and_legacy_albums_are_visible(self, _sign):
        legacy = self.create_evento(nombre="Legacy", available_until=None)
        closed = self.create_evento(
            nombre="Cerrado",
            estado=Evento.Estado.CLOSED,
            available_until=timezone.now() + timedelta(hours=1),
        )

        for evento in (self.evento, legacy, closed):
            with self.subTest(evento=evento.nombre):
                self.assertEqual(self.client.get(self.album_url(evento)).status_code, 200)

    def test_draft_archived_and_expired_albums_are_404_for_both_endpoints(self):
        eventos = (
            self.create_evento(nombre="Borrador", estado=Evento.Estado.DRAFT),
            self.create_evento(nombre="Archivado", estado=Evento.Estado.ARCHIVED),
            self.create_evento(
                nombre="Vencido",
                available_until=timezone.now() - timedelta(seconds=1),
            ),
        )

        for evento in eventos:
            with self.subTest(evento=evento.nombre):
                self.assertEqual(self.client.get(self.album_url(evento)).status_code, 404)
                self.assertEqual(
                    self.client.get(self.pages_url(evento), {"cursor": "x"}).status_code,
                    404,
                )

    @patch("eventos.views.generar_url_lectura", side_effect=_signed_url)
    def test_deleted_photos_are_excluded_and_photo_state_policy_is_preserved(
        self,
        _sign,
    ):
        visible = [
            self.create_photo(index=index, estado=estado)
            for index, estado in enumerate(
                (
                    Foto.Estado.PENDIENTE,
                    Foto.Estado.APROBADA,
                    Foto.Estado.RECHAZADA,
                ),
                start=1,
            )
        ]
        self.create_photo(index=10, eliminada_at=timezone.now())

        response = self.client.get(self.album_url())

        self.assertEqual(response.context["total_fotos"], 3)
        self.assertEqual(
            {item["id"] for item in response.context["fotos"]},
            {foto.id for foto in visible},
        )

    @patch("eventos.views.generar_url_lectura", side_effect=_signed_url)
    def test_album_orders_by_created_at_then_id_descending(self, _sign):
        fotos = self.create_photos(3)
        tied_at = timezone.now() - timedelta(days=1)
        Foto.objects.filter(pk__in=[foto.pk for foto in fotos]).update(
            creada_en=tied_at
        )

        response = self.client.get(self.album_url())

        self.assertEqual(
            [item["id"] for item in response.context["fotos"]],
            sorted((foto.id for foto in fotos), reverse=True),
        )

    @patch("eventos.views.generar_url_lectura", side_effect=_signed_url)
    def test_initial_batch_sizes_zero_one_thirty_thirty_one_and_sixty_one(
        self,
        sign,
    ):
        for amount in (0, 1, 30, 31, 61):
            evento = self.create_evento(nombre=f"Evento {amount}")
            mesa = Mesa.objects.create(evento=evento, numero=1)
            self.create_photos(amount, evento=evento, mesa=mesa)
            sign.reset_mock()

            with self.subTest(amount=amount):
                response = self.client.get(self.album_url(evento))
                expected = min(amount, 30)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.context["fotos"]), expected)
                self.assertEqual(response.context["total_fotos"], amount)
                self.assertEqual(response.context["album_has_more"], amount > 30)
                self.assertEqual(sign.call_count, expected)

    @patch("eventos.views.generar_url_lectura", side_effect=_signed_url)
    def test_cursor_pages_have_no_duplicates_or_omissions(self, _sign):
        fotos = self.create_photos(61)
        expected_ids = list(
            Foto.objects.filter(pk__in=[foto.pk for foto in fotos])
            .order_by("-creada_en", "-id")
            .values_list("id", flat=True)
        )

        initial = self.client.get(self.album_url())
        collected = [item["id"] for item in initial.context["fotos"]]
        cursor = initial.context["album_next_cursor"]
        page_sizes = []

        while cursor:
            response = self.client.get(self.pages_url(), {"cursor": cursor})
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            page_sizes.append(len(payload["photos"]))
            collected.extend(photo["id"] for photo in payload["photos"])
            cursor = payload["next_cursor"]
            self.assertEqual(payload["has_more"], cursor is not None)

        self.assertEqual(page_sizes, [30, 1])
        self.assertEqual(collected, expected_ids)
        self.assertEqual(len(collected), len(set(collected)))

    @patch("eventos.views.generar_url_lectura", side_effect=_signed_url)
    def test_valid_cursor_returns_only_the_next_batch(self, _sign):
        self.create_photos(31)
        initial = self.client.get(self.album_url())

        response = self.client.get(
            self.pages_url(),
            {"cursor": initial.context["album_next_cursor"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["photos"]), 1)
        self.assertFalse(response.json()["has_more"])
        self.assertIsNone(response.json()["next_cursor"])

    def test_missing_or_invalid_cursor_has_stable_400_contract(self):
        for query in (
            {},
            {"cursor": "datos-no-firmados"},
            {"cursor": "x" * 513},
        ):
            with self.subTest(query=query):
                response = self.client.get(self.pages_url(), query)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json(),
                    {
                        "error": "El cursor no es válido.",
                        "code": "invalid_cursor",
                    },
                )
                self.assertEqual(response["Cache-Control"], "private, no-store")

    @patch("eventos.views.generar_url_lectura", side_effect=_signed_url)
    def test_cursor_is_bound_to_its_album(self, _sign):
        self.create_photos(31)
        cursor = self.client.get(self.album_url()).context["album_next_cursor"]
        otro_evento = self.create_evento(nombre="Otro álbum")

        response = self.client.get(
            self.pages_url(otro_evento),
            {"cursor": cursor},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_cursor")

    @patch("eventos.views.generar_url_lectura", side_effect=_signed_url)
    def test_endpoint_is_get_only_and_contract_is_minimal(self, _sign):
        foto = self.create_photo(
            index=1,
            nombre_original="nombre-secreto.jpg",
            uploader_hash="hash-secreto",
        )
        self.create_photos(30)
        initial = self.client.get(self.album_url())

        response = self.client.get(
            self.pages_url(),
            {"cursor": initial.context["album_next_cursor"]},
        )
        photo = response.json()["photos"][0]

        self.assertEqual(set(photo), {"id", "url", "can_delete"})
        self.assertEqual(photo["id"], foto.id)
        self.assertEqual(self.client.post(self.pages_url()).status_code, 405)

    @patch("eventos.views.generar_url_lectura", return_value="https://signed.test/photo")
    def test_public_html_and_json_omit_sensitive_metadata(self, _sign):
        foto = self.create_photo(
            index=1,
            nombre_original="nombre-muy-secreto.jpg",
            uploader_hash="uploader-muy-secreto",
        )
        self.create_photos(30)

        html_response = self.client.get(self.album_url())
        json_response = self.client.get(
            self.pages_url(),
            {"cursor": html_response.context["album_next_cursor"]},
        )
        combined = html_response.content.decode() + json_response.content.decode()

        for secret in (
            self.mesa.nombre,
            self.mesa.token,
            self.mesa.codigo_acceso,
            foto.nombre_original,
            foto.uploader_hash,
            foto.hash_sha256,
            foto.creada_en.isoformat(),
        ):
            self.assertNotIn(secret, combined)

    def test_single_signing_failure_is_logged_safely_and_does_not_break_album(self):
        fotos = self.create_photos(2)
        failing = fotos[0]

        def sign(object_key):
            if object_key == failing.object_key:
                raise RuntimeError("secret object key must not be logged")
            return "https://signed.test/photo"

        with self.assertLogs("eventos.views", level="WARNING") as logs:
            with patch("eventos.views.generar_url_lectura", side_effect=sign):
                response = self.client.get(self.album_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["fotos"]), 1)
        self.assertNotIn(failing.object_key, " ".join(logs.output))
        self.assertNotIn("secret object key", " ".join(logs.output))

    @patch("eventos.views.generar_url_lectura", side_effect=_signed_url)
    def test_album_cache_and_robots_headers(self, _sign):
        self.create_photos(31)
        response = self.client.get(self.album_url())

        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertContains(
            response,
            '<meta name="robots" content="noindex, nofollow">',
            html=True,
        )

        page = self.client.get(
            self.pages_url(),
            {"cursor": response.context["album_next_cursor"]},
        )
        self.assertEqual(page["Cache-Control"], "private, no-store")

    def test_expired_public_event_does_not_show_album_cta(self):
        self.evento.available_until = timezone.now() - timedelta(seconds=1)
        self.evento.save(update_fields=["available_until"])

        response = self.client.get(reverse("evento_publico", args=[self.evento.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.album_url())
        self.assertNotContains(response, "Ver álbum")

    @patch("eventos.views.get_r2_client")
    def test_delete_still_requires_matching_uploader_hash(self, get_r2):
        foto = self.create_photo(index=1, uploader_hash="hash-de-otro-navegador")

        response = self.client.post(
            reverse("eliminar_foto", args=[self.evento.slug, foto.id])
        )

        self.assertEqual(response.status_code, 403)
        get_r2.assert_not_called()
        foto.refresh_from_db()
        self.assertIsNone(foto.eliminada_at)

    @patch("eventos.views.get_r2_client")
    def test_delete_remains_csrf_protected(self, get_r2):
        token = "token-del-uploader"
        foto = self.create_photo(
            index=1,
            uploader_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        )
        csrf_client = Client(enforce_csrf_checks=True)
        session = csrf_client.session
        session["uploader_token"] = token
        session.save()

        response = csrf_client.post(
            reverse("eliminar_foto", args=[self.evento.slug, foto.id])
        )

        self.assertEqual(response.status_code, 403)
        get_r2.assert_not_called()
        foto.refresh_from_db()
        self.assertIsNone(foto.eliminada_at)
