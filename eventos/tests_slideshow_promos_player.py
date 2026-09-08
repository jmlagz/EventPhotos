"""Pruebas del catálogo y playback de promos con Node, sin navegador ni red."""

import json
from pathlib import Path
import re
import shutil
import subprocess

from django.test import SimpleTestCase


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

function element(tagName = 'DIV') {
    return {
        tagName, dataset: {}, hidden: false, disabled: false, title: '',
        textContent: '', listeners: {}, attributes: {}, classList: new ClassList(),
        addEventListener(name, callback) { this.listeners[name] = callback; },
        setAttribute(name, value) { this.attributes[name] = String(value); },
        removeAttribute(name) {
            delete this.attributes[name];
            if (name === 'src') this._src = '';
        },
        contains(node) { return node === this; },
        click() { return this.listeners.click?.({ target: this }); },
    };
}

function photo(id, suffix = '') {
    return { id, url: `https://photos.invalid/${id}${suffix}`, created_at: '2026-09-08T00:00:00Z' };
}

function promo(id, order, suffix = '') {
    return { id, type: `type-${id}`, url: `https://promos.invalid/${id}${suffix}`, order };
}

function photoPage(items) {
    return { status: 200, body: { photos: items, next_after_id: items.at(-1)?.id ?? null, has_more: false } };
}

function promoPage(items, status = 200) {
    return { status, body: { promos: items } };
}

function makeApp(options = {}) {
    const elements = {
        slideshow: element('MAIN'),
        'slideshow-image': element('IMG'),
        'slideshow-status': element(),
        'slideshow-controls': element('NAV'),
        'slideshow-previous': element('BUTTON'),
        'slideshow-toggle-playback': element('BUTTON'),
        'slideshow-next': element('BUTTON'),
        'slideshow-fullscreen': element('BUTTON'),
    };
    const root = elements.slideshow;
    root.dataset.photosUrl = '/fotos/evento/slideshow/photos/';
    root.dataset.promosUrl = '/fotos/evento/slideshow/promos/';
    root.dataset.state = 'LOADING';

    const photoResponses = [...(options.photoResponses || [photoPage(options.photos || [photo(1), photo(2), photo(3), photo(4)])])];
    const promoResponses = [...(options.promoResponses || [promoPage(options.promos || [])])];
    const failedImages = new Set(options.failedImages || []);
    const calls = { fetchUrls: [], images: [], requestFullscreen: 0 };
    const timers = new Map();
    const documentListeners = {};
    let timerId = 0;

    Object.defineProperty(elements['slideshow-image'], 'src', {
        get() { return this._src || ''; },
        set(value) {
            this._src = value;
            calls.images.push(value);
            queueMicrotask(() => (failedImages.has(value) ? this.onerror : this.onload)?.());
        },
    });

    class FakeImage {
        set src(value) { this._src = value; queueMicrotask(() => this.onload?.()); }
        get src() { return this._src; }
    }

    const document = {
        activeElement: null,
        fullscreenElement: null,
        hidden: false,
        getElementById(id) { return elements[id]; },
        addEventListener(name, callback) { documentListeners[name] = callback; },
        async exitFullscreen() { document.fullscreenElement = null; },
    };
    root.requestFullscreen = async () => {
        calls.requestFullscreen += 1;
        document.fullscreenElement = root;
    };

    const context = vm.createContext({
        document,
        window: { location: { href: 'https://site.invalid/fotos/evento/slideshow/' } },
        URL,
        Image: FakeImage,
        queueMicrotask,
        console: { log() {}, error() {} },
        fetch: async (url, init) => {
            calls.fetchUrls.push(url);
            assert.equal(init.method, 'GET');
            assert.equal(init.cache, 'no-store');
            const queue = url.includes('/promos/') ? promoResponses : photoResponses;
            const response = queue.shift();
            assert.ok(response, `Unexpected fetch: ${url}`);
            return {
                ok: response.status >= 200 && response.status < 300,
                status: response.status,
                json: async () => response.body,
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

    async function settle(rounds = 20) {
        for (let index = 0; index < rounds; index += 1) await Promise.resolve();
    }
    async function runTimer(delay, fromEnd = false) {
        const matches = [...timers.entries()].filter(([, timer]) => timer.delay === delay);
        assert.ok(matches.length, `No timer at ${delay}ms`);
        const [id, timer] = fromEnd ? matches.at(-1) : matches[0];
        timers.delete(id);
        timer.callback();
        await settle();
    }
    function hasTimer(delay) { return [...timers.values()].some(timer => timer.delay === delay); }
    async function click(id) { await elements[id].click(); await settle(); }
    async function key(key) {
        await documentListeners.keydown({
            key, repeat: false, target: { tagName: 'BODY' }, preventDefault() {},
        });
        await settle();
    }
    return { root, elements, calls, settle, runTimer, hasTimer, click, key };
}

async function reachFirstPromo(app) {
    for (let index = 0; index < 8; index += 1) await app.runTimer(7000);
}

async function main() {
    if (scenario === 'photo_only') {
        const app = makeApp();
        await app.settle();
        assert.equal(app.root.dataset.currentPhotoId, '1');
        for (let index = 0; index < 9; index += 1) await app.runTimer(7000);
        assert.ok(app.calls.images.every(url => url.startsWith('https://photos.invalid/')));
    } else if (scenario === 'minimum') {
        const app = makeApp({ photos: [photo(1), photo(2), photo(3)], promos: [promo(1, 1)] });
        await app.settle();
        for (let index = 0; index < 10; index += 1) await app.runTimer(7000);
        assert.ok(!app.calls.images.some(url => url.startsWith('https://promos.invalid/')));
    } else if (scenario === 'rotation') {
        const app = makeApp({ promos: [promo(2, 2), promo(1, 1)] });
        await app.settle();
        assert.equal(app.calls.images[0], 'https://photos.invalid/1');
        await reachFirstPromo(app);
        assert.equal(app.calls.images.at(-1), 'https://promos.invalid/1');
        assert.equal(app.elements['slideshow-image'].alt, 'Promoción de EventPhotos');
        assert.equal(app.hasTimer(5000), true);
        await app.runTimer(5000, true);
        assert.ok(app.calls.images.at(-1).startsWith('https://photos.invalid/'));
        assert.equal(app.elements['slideshow-image'].alt, 'Foto del evento');
        for (let index = 0; index < 8; index += 1) await app.runTimer(7000);
        assert.equal(app.calls.images.at(-1), 'https://promos.invalid/2');
    } else if (scenario === 'novelty_priority') {
        const app = makeApp({
            promos: [promo(1, 1)],
            photoResponses: [
                photoPage([photo(1), photo(2), photo(3), photo(4)]),
                photoPage([photo(5)]),
            ],
        });
        await app.settle();
        for (let index = 0; index < 7; index += 1) await app.runTimer(7000);
        await app.runTimer(5000);
        await app.runTimer(7000);
        assert.equal(app.root.dataset.currentPhotoId, '5');
        await app.runTimer(7000);
        assert.equal(app.calls.images.at(-1), 'https://promos.invalid/1');
    } else if (scenario === 'pause') {
        const app = makeApp({ promos: [promo(1, 1)] });
        await app.settle();
        for (let index = 0; index < 7; index += 1) await app.runTimer(7000);
        await app.click('slideshow-toggle-playback');
        assert.equal(app.root.dataset.state, 'PAUSED');
        assert.equal(app.hasTimer(7000), false);
        assert.equal(app.hasTimer(5000), true);
        await app.click('slideshow-toggle-playback');
        await app.runTimer(7000);
        assert.equal(app.calls.images.at(-1), 'https://promos.invalid/1');
    } else if (scenario === 'failures') {
        const endpoint = makeApp({ promoResponses: [promoPage([], 500)] });
        await endpoint.settle();
        await endpoint.runTimer(7000);
        assert.equal(endpoint.root.dataset.state, 'PLAYING');

        const imageFailure = makeApp({
            promos: [promo(1, 1), promo(2, 2)],
            failedImages: ['https://promos.invalid/1'],
        });
        await imageFailure.settle();
        await reachFirstPromo(imageFailure);
        assert.equal(imageFailure.calls.images.at(-1), 'https://promos.invalid/2');
        assert.equal(imageFailure.root.dataset.state, 'PLAYING');

        const allFail = makeApp({
            promos: [promo(1, 1), promo(2, 2)],
            failedImages: ['https://promos.invalid/1', 'https://promos.invalid/2'],
        });
        await allFail.settle();
        await reachFirstPromo(allFail);
        assert.ok(allFail.calls.images.at(-1).startsWith('https://photos.invalid/'));
        assert.equal(allFail.root.dataset.state, 'PLAYING');
    } else if (scenario === 'resync') {
        const app = makeApp({
            promoResponses: [promoPage([promo(1, 1, '-old')]), promoPage([promo(2, 1, '-new')])],
            photoResponses: [
                photoPage([photo(1), photo(2), photo(3), photo(4)]),
                photoPage([photo(1), photo(2), photo(3), photo(4)]),
            ],
        });
        await app.settle();
        await app.runTimer(2700000);
        for (let index = 0; index < 8; index += 1) await app.runTimer(7000);
        assert.equal(app.calls.images.at(-1), 'https://promos.invalid/2-new');
    } else if (scenario === 'manual') {
        const app = makeApp({ promos: [promo(1, 1)] });
        await app.settle();
        for (let index = 0; index < 7; index += 1) await app.runTimer(7000);
        await app.click('slideshow-next');
        assert.ok(app.calls.images.at(-1).startsWith('https://photos.invalid/'));
        await app.click('slideshow-previous');
        assert.ok(app.calls.images.at(-1).startsWith('https://photos.invalid/'));
        await app.key('ArrowRight');
        assert.ok(app.calls.images.at(-1).startsWith('https://photos.invalid/'));
        await app.key('f');
        assert.equal(app.calls.requestFullscreen, 1);
    } else {
        throw new Error(`Unknown scenario: ${scenario}`);
    }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
"""


class SlideshowPromoPlayerTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.node = shutil.which("node")
        template = (
            Path(__file__).parent / "templates" / "eventos" / "slideshow.html"
        ).read_text(encoding="utf-8")
        cls.script = re.search(r"<script>(.*?)</script>", template, re.DOTALL).group(1)

    def run_scenario(self, scenario):
        self.assertIsNotNone(self.node, "Estas pruebas requieren Node.")
        result = subprocess.run(
            [self.node, "-e", HARNESS],
            input=json.dumps({"script": self.script, "scenario": scenario}),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_zero_promos_preserves_photo_only_playback(self):
        self.run_scenario("photo_only")

    def test_promos_require_four_valid_photos(self):
        self.run_scenario("minimum")

    def test_promo_is_not_first_uses_five_seconds_and_rotates(self):
        self.run_scenario("rotation")

    def test_live_novelty_has_priority_over_due_promo(self):
        self.run_scenario("novelty_priority")

    def test_pause_stops_autoplay_and_resume_keeps_due_promo(self):
        self.run_scenario("pause")

    def test_endpoint_and_image_failures_keep_photos_playing(self):
        self.run_scenario("failures")

    def test_resync_replaces_promo_catalog(self):
        self.run_scenario("resync")

    def test_manual_navigation_keyboard_and_fullscreen_remain_photo_based(self):
        self.run_scenario("manual")
