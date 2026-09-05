"""Execute the slideshow JavaScript with a fake DOM, fetch and timers.

Uses the installed Node runtime (built-ins only); no browser, database or network.
"""

from datetime import date
import json
from pathlib import Path
import re
import shutil
import subprocess

from django.conf import settings
from django.contrib.sessions.models import Session
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .models import Evento


HARNESS = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { script, scenario } = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));

class ClassList {
    constructor() { this.values = new Set(); }
    add(value) { this.values.add(value); }
    remove(value) { this.values.delete(value); }
    contains(value) { return this.values.has(value); }
}

function makeElement(tagName = 'DIV') {
    return {
        tagName, dataset: {}, hidden: false, disabled: false, title: '',
        textContent: '', listeners: {}, attributes: {}, classList: new ClassList(),
        addEventListener(name, callback) { this.listeners[name] = callback; },
        setAttribute(name, value) { this.attributes[name] = String(value); },
        removeAttribute(name) { delete this.attributes[name]; },
        contains(node) { return node === this; },
        click() { return this.listeners.click?.({ target: this }); },
    };
}

function photo(id) {
    return { id, url: `https://images.invalid/${id}`, created_at: `2026-09-04T00:00:0${id}Z` };
}

function makeApp(options = {}) {
    const elements = {
        slideshow: makeElement('MAIN'),
        'slideshow-image': makeElement('IMG'),
        'slideshow-status': makeElement(),
        'slideshow-controls': makeElement('NAV'),
        'slideshow-previous': makeElement('BUTTON'),
        'slideshow-toggle-playback': makeElement('BUTTON'),
        'slideshow-next': makeElement('BUTTON'),
        'slideshow-fullscreen': makeElement('BUTTON'),
    };
    const root = elements.slideshow;
    root.dataset.photosUrl = '/fotos/evento/slideshow/photos/';
    root.dataset.state = 'LOADING';

    const calls = {
        fetchUrls: [], domImages: [], preloadUrls: [],
        maxActivePreloads: 0, requestFullscreen: 0, exitFullscreen: 0,
    };
    const documentListeners = {};
    const timers = new Map();
    let timerId = 0;
    let activePreloads = 0;
    const currentFailures = new Set(options.currentFailures || []);
    const preloadFailures = new Set(options.preloadFailures || []);
    const pages = [...(options.pages || [{ status: 200, body: {
        photos: options.photos || [photo(1), photo(2), photo(3)],
        next_after_id: options.photos?.at(-1)?.id || 3,
        has_more: false,
    } }])];

    Object.defineProperty(elements['slideshow-image'], 'src', {
        get() { return this._src || ''; },
        set(value) {
            this._src = value;
            calls.domImages.push(value);
            queueMicrotask(() => {
                const callback = currentFailures.has(value) ? this.onerror : this.onload;
                callback?.();
            });
        },
    });

    class FakeImage {
        constructor() { this.onload = null; this.onerror = null; this._src = ''; }
        set src(value) {
            this._src = value;
            calls.preloadUrls.push(value);
            activePreloads += 1;
            calls.maxActivePreloads = Math.max(calls.maxActivePreloads, activePreloads);
            queueMicrotask(() => {
                activePreloads -= 1;
                const callback = preloadFailures.has(value) ? this.onerror : this.onload;
                callback?.();
            });
        }
        get src() { return this._src; }
    }

    const document = {
        activeElement: null,
        fullscreenElement: null,
        getElementById(id) { return elements[id]; },
        addEventListener(name, callback) { documentListeners[name] = callback; },
        async exitFullscreen() {
            calls.exitFullscreen += 1;
            document.fullscreenElement = null;
            documentListeners.fullscreenchange?.();
        },
    };

    if (options.fullscreenSupported !== false) {
        root.requestFullscreen = async () => {
            calls.requestFullscreen += 1;
            if (options.fullscreenFailure) throw new Error('PRIVATE FULLSCREEN ERROR');
            document.fullscreenElement = root;
            documentListeners.fullscreenchange?.();
        };
    } else {
        document.exitFullscreen = undefined;
    }

    const context = vm.createContext({
        document,
        window: { location: { href: 'https://site.invalid/fotos/evento/slideshow/' } },
        URL,
        Image: FakeImage,
        console: { log() {}, error() {} },
        queueMicrotask,
        fetch: async (url, init) => {
            calls.fetchUrls.push(url);
            assert.equal(init.method, 'GET');
            assert.equal(init.cache, 'no-store');
            assert.equal(init.credentials, 'same-origin');
            const page = pages.shift();
            assert.ok(page, 'Unexpected catalog request');
            return {
                ok: page.status >= 200 && page.status < 300,
                status: page.status,
                json: async () => page.body,
            };
        },
        setTimeout(callback, delay) {
            const id = ++timerId;
            timers.set(id, { callback, delay });
            return id;
        },
        clearTimeout(id) { timers.delete(id); },
    });
    vm.runInContext(script, context);

    async function settle(rounds = 12) {
        for (let index = 0; index < rounds; index += 1) await Promise.resolve();
    }
    async function runTimer(delay) {
        const match = [...timers.entries()].find(([, timer]) => timer.delay === delay);
        assert.ok(match, `No timer found for ${delay}ms`);
        timers.delete(match[0]);
        match[1].callback();
        await settle();
    }
    function hasTimer(delay) {
        return [...timers.values()].some(timer => timer.delay === delay);
    }
    async function click(id) {
        await elements[id].click();
        await settle();
    }
    async function key(key, repeat = false) {
        let prevented = false;
        const event = {
            key, repeat, target: { tagName: 'BODY' },
            preventDefault() { prevented = true; },
        };
        await documentListeners.keydown(event);
        await settle();
        return prevented;
    }
    return { elements, root, calls, document, timers, settle, runTimer, hasTimer, click, key };
}

async function main() {
    if (scenario === 'catalog') {
        const app = makeApp({ pages: [
            { status: 200, body: { photos: [photo(3), photo(1)], next_after_id: 3, has_more: true } },
            { status: 200, body: { photos: [photo(2), photo(3)], next_after_id: 3, has_more: false } },
        ] });
        assert.equal(app.root.dataset.state, 'LOADING');
        assert.equal(app.elements['slideshow-status'].textContent, 'Preparando presentación...');
        await app.settle();
        assert.equal(app.calls.fetchUrls.length, 2);
        assert.ok(!app.calls.fetchUrls[0].includes('after_id'));
        assert.ok(app.calls.fetchUrls[1].includes('after_id=3'));
        assert.equal(app.root.dataset.currentPhotoId, '1');
        await app.click('slideshow-next');
        assert.equal(app.root.dataset.currentPhotoId, '2');
        await app.click('slideshow-next');
        assert.equal(app.root.dataset.currentPhotoId, '3');
    } else if (scenario === 'empty_unavailable') {
        const empty = makeApp({ photos: [] });
        await empty.settle();
        assert.equal(empty.root.dataset.state, 'EMPTY');
        assert.equal(empty.elements['slideshow-status'].textContent, 'Esperando nuevas fotos');
        const unavailable = makeApp({ pages: [{ status: 404, body: {} }] });
        await unavailable.settle();
        assert.equal(unavailable.root.dataset.state, 'UNAVAILABLE');
        assert.equal(unavailable.elements['slideshow-status'].textContent, 'Presentación no disponible');
    } else if (scenario === 'playback') {
        const app = makeApp();
        await app.settle();
        assert.equal(app.root.dataset.state, 'PLAYING');
        assert.equal(app.root.dataset.currentPhotoId, '1');
        assert.equal(app.hasTimer(7000), true);
        await app.runTimer(7000);
        assert.equal(app.root.dataset.currentPhotoId, '2');
        await app.click('slideshow-toggle-playback');
        assert.equal(app.root.dataset.state, 'PAUSED');
        assert.equal(app.hasTimer(7000), false);
        assert.equal(app.elements['slideshow-toggle-playback'].textContent, 'Reanudar');
        await app.click('slideshow-next');
        assert.equal(app.root.dataset.currentPhotoId, '3');
        await app.click('slideshow-previous');
        assert.equal(app.root.dataset.currentPhotoId, '2');
        await app.click('slideshow-toggle-playback');
        assert.equal(app.root.dataset.state, 'PLAYING');
        assert.equal(app.hasTimer(7000), true);
        await app.click('slideshow-next');
        await app.click('slideshow-next');
        assert.equal(app.root.dataset.currentPhotoId, '1');
    } else if (scenario === 'single_photo') {
        const app = makeApp({ photos: [photo(8)] });
        await app.settle();
        assert.equal(app.root.dataset.currentPhotoId, '8');
        assert.equal(app.hasTimer(7000), false);
        assert.equal(app.calls.preloadUrls.length, 0);
        await app.click('slideshow-next');
        assert.deepEqual(app.calls.domImages, ['https://images.invalid/8']);
    } else if (scenario === 'preload_and_errors') {
        const app = makeApp({ preloadFailures: ['https://images.invalid/2'] });
        await app.settle();
        assert.deepEqual(app.calls.domImages, ['https://images.invalid/1']);
        assert.deepEqual(app.calls.preloadUrls, ['https://images.invalid/2', 'https://images.invalid/3']);
        assert.equal(app.calls.maxActivePreloads, 1);
        await app.runTimer(7000);
        assert.equal(app.root.dataset.currentPhotoId, '3');
        const skip = makeApp({ currentFailures: ['https://images.invalid/1'] });
        await skip.settle();
        assert.equal(skip.root.dataset.currentPhotoId, '2');
        assert.equal(skip.root.dataset.state, 'PLAYING');
        const all = makeApp({ currentFailures: [
            'https://images.invalid/1', 'https://images.invalid/2', 'https://images.invalid/3',
        ] });
        await all.settle();
        assert.equal(all.root.dataset.state, 'ERROR_RECOVERABLE');
        assert.equal(all.elements['slideshow-status'].textContent, 'No pudimos cargar esta foto');
    } else if (scenario === 'fullscreen') {
        const app = makeApp();
        await app.settle();
        await app.click('slideshow-fullscreen');
        assert.equal(app.calls.requestFullscreen, 1);
        assert.equal(app.elements['slideshow-fullscreen'].textContent, 'Salir de pantalla completa');
        await app.click('slideshow-fullscreen');
        assert.equal(app.calls.exitFullscreen, 1);
        const failed = makeApp({ fullscreenFailure: true });
        await failed.settle();
        await failed.click('slideshow-fullscreen');
        assert.equal(failed.root.dataset.state, 'PLAYING');
        const unsupported = makeApp({ fullscreenSupported: false });
        await unsupported.settle();
        assert.equal(unsupported.elements['slideshow-fullscreen'].disabled, true);
    } else if (scenario === 'keyboard') {
        const app = makeApp();
        await app.settle();
        assert.equal(await app.key('ArrowRight'), true);
        assert.equal(app.root.dataset.currentPhotoId, '2');
        await app.key('ArrowRight', true);
        assert.equal(app.root.dataset.currentPhotoId, '2');
        await app.key('ArrowLeft');
        assert.equal(app.root.dataset.currentPhotoId, '1');
        await app.key(' ');
        assert.equal(app.root.dataset.state, 'PAUSED');
        await app.key(' ');
        assert.equal(app.root.dataset.state, 'PLAYING');
        await app.key('f');
        assert.equal(app.calls.requestFullscreen, 1);
    } else if (scenario === 'controls') {
        const app = makeApp();
        await app.settle();
        assert.equal(app.hasTimer(3000), true);
        await app.runTimer(3000);
        assert.equal(app.root.classList.contains('controls-hidden'), true);
        app.root.listeners.mousemove();
        assert.equal(app.root.classList.contains('controls-hidden'), false);
        await app.click('slideshow-toggle-playback');
        assert.equal(app.root.dataset.state, 'PAUSED');
        assert.equal(app.root.classList.contains('controls-hidden'), false);
        assert.equal(app.hasTimer(3000), false);
    } else {
        throw new Error(`Unknown scenario: ${scenario}`);
    }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
"""


class SlideshowTemplateRenderTests(TestCase):
    def test_player_renders_controls_without_creating_session(self):
        evento = Evento.objects.create(
            nombre="Evento pantalla",
            fecha=date(2026, 9, 4),
            estado=Evento.Estado.ACTIVE,
        )

        response = self.client.get(reverse("slideshow", args=[evento.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="slideshow-previous"')
        self.assertContains(response, 'id="slideshow-toggle-playback"')
        self.assertContains(response, 'id="slideshow-next"')
        self.assertContains(response, 'id="slideshow-fullscreen"')
        self.assertEqual(response.content.count(b'id="slideshow-image"'), 1)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)
        self.assertEqual(Session.objects.count(), 0)


class SlideshowPlayerFrontendTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.node = shutil.which("node")
        cls.template = (
            Path(__file__).parent / "templates" / "eventos" / "slideshow.html"
        ).read_text(encoding="utf-8")
        cls.script = re.search(
            r"<script>(.*?)</script>", cls.template, re.DOTALL
        ).group(1)

    def run_scenario(self, scenario):
        self.assertIsNotNone(
            self.node,
            "These frontend tests require the local Node runtime; no npm packages are needed.",
        )
        result = subprocess.run(
            [self.node, "-e", HARNESS],
            input=json.dumps({"script": self.script, "scenario": scenario}),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_catalog_is_paginated_deduplicated_and_sorted(self):
        self.run_scenario("catalog")

    def test_empty_and_unavailable_states(self):
        self.run_scenario("empty_unavailable")

    def test_autoplay_pause_resume_manual_navigation_and_wrap(self):
        self.run_scenario("playback")

    def test_single_photo_does_not_cycle_or_preload(self):
        self.run_scenario("single_photo")

    def test_single_preload_and_image_error_recovery(self):
        self.run_scenario("preload_and_errors")

    def test_fullscreen_success_failure_and_unsupported_browser(self):
        self.run_scenario("fullscreen")

    def test_keyboard_navigation_and_repeat_guard(self):
        self.run_scenario("keyboard")

    def test_controls_auto_hide_but_remain_visible_while_paused(self):
        self.run_scenario("controls")

    def test_css_has_contain_transition_focus_and_reduced_motion(self):
        self.assertIn("object-fit: contain", self.template)
        self.assertIn("transition: opacity 400ms ease", self.template)
        self.assertIn(":focus-visible", self.template)
        self.assertIn("prefers-reduced-motion: reduce", self.template)
        reduced_motion = self.template.split(
            "@media (prefers-reduced-motion: reduce)", 1
        )[1]
        self.assertIn("transition: none", reduced_motion)

    def test_player_has_no_polling_or_sensitive_template_data(self):
        self.assertNotIn("setInterval", self.script)
        self.assertNotIn("object_key", self.template)
        self.assertNotIn("mesa.token", self.template)
        self.assertNotIn("codigo_acceso", self.template)
