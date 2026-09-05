"""Exercise slideshow live polling and resync with Node built-ins only."""

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
    return {
        id,
        url: `https://images.invalid/${id}${suffix}`,
        created_at: `2026-09-04T00:00:${String(id).padStart(2, '0')}Z`,
    };
}

function page(photos, options = {}) {
    return {
        status: options.status || 200,
        body: {
            photos,
            next_after_id: options.nextAfterId ?? (photos.at(-1)?.id ?? null),
            has_more: options.hasMore || false,
        },
        ...options,
    };
}

function makeApp(specs) {
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
    root.dataset.state = 'LOADING';
    const timers = new Map();
    const documentListeners = {};
    const pendingFetches = [];
    const queue = [...specs];
    const calls = { fetchUrls: [], domImages: [], preloads: [], activeFetches: 0, maxActiveFetches: 0 };
    let timerId = 0;

    Object.defineProperty(elements['slideshow-image'], 'src', {
        get() { return this._src || ''; },
        set(value) {
            this._src = value;
            calls.domImages.push(value);
            queueMicrotask(() => this.onload?.());
        },
    });

    class FakeImage {
        set src(value) {
            this._src = value;
            calls.preloads.push(value);
            queueMicrotask(() => this.onload?.());
        }
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
    root.requestFullscreen = async () => { document.fullscreenElement = root; };

    function response(spec) {
        return {
            ok: spec.status >= 200 && spec.status < 300,
            status: spec.status,
            json: async () => {
                if (spec.invalidJson) throw new SyntaxError('PRIVATE JSON ERROR');
                return spec.body;
            },
        };
    }

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
            const spec = queue.shift();
            assert.ok(spec, 'Unexpected fetch');
            calls.activeFetches += 1;
            calls.maxActiveFetches = Math.max(calls.maxActiveFetches, calls.activeFetches);
            if (spec.deferred) {
                return new Promise((resolve, reject) => {
                    pendingFetches.push(() => {
                        calls.activeFetches -= 1;
                        if (spec.networkError) reject(new TypeError('PRIVATE NETWORK ERROR'));
                        else resolve(response(spec));
                    });
                });
            }
            calls.activeFetches -= 1;
            if (spec.networkError) throw new TypeError('PRIVATE NETWORK ERROR');
            return response(spec);
        },
        setTimeout(callback, delay) {
            const id = ++timerId;
            timers.set(id, { callback, delay });
            return id;
        },
        clearTimeout(id) { timers.delete(id); },
    });
    vm.runInContext(script, context);

    async function settle(rounds = 16) {
        for (let index = 0; index < rounds; index += 1) await Promise.resolve();
    }
    function timersAt(delay) {
        return [...timers.values()].filter(timer => timer.delay === delay).length;
    }
    async function runTimer(delay) {
        const match = [...timers.entries()].find(([, timer]) => timer.delay === delay);
        assert.ok(match, `No timer at ${delay}ms`);
        timers.delete(match[0]);
        match[1].callback();
        await settle();
    }
    async function click(id) {
        await elements[id].click();
        await settle();
    }
    async function visibility(hidden) {
        document.hidden = hidden;
        documentListeners.visibilitychange();
        await settle();
    }
    async function releaseFetch() {
        const release = pendingFetches.shift();
        assert.ok(release, 'No deferred fetch');
        release();
        await settle();
    }
    return { root, elements, calls, document, timersAt, runTimer, click, visibility, releaseFetch, settle };
}

async function main() {
    if (scenario === 'poll_cursor_empty') {
        const app = makeApp([page([photo(1), photo(3)]), page([])]);
        await app.settle();
        const beforeImages = [...app.calls.domImages];
        await app.runTimer(5000);
        assert.equal(app.calls.fetchUrls.length, 2);
        assert.ok(app.calls.fetchUrls[1].includes('after_id=3'));
        assert.deepEqual(app.calls.domImages, beforeImages);
        assert.equal(app.root.dataset.currentPhotoId, '1');
        assert.equal(app.root.dataset.state, 'PLAYING');
        assert.equal(app.timersAt(5000), 1);
    } else if (scenario === 'new_fifo') {
        const app = makeApp([page([photo(1), photo(2)]), page([photo(3), photo(3), photo(4)])]);
        await app.settle();
        await app.runTimer(5000);
        assert.equal(app.root.dataset.currentPhotoId, '1');
        await app.runTimer(7000);
        assert.equal(app.root.dataset.currentPhotoId, '3');
        await app.runTimer(7000);
        assert.equal(app.root.dataset.currentPhotoId, '4');
        await app.runTimer(7000);
        assert.equal(app.root.dataset.currentPhotoId, '1');
        await app.runTimer(7000);
        assert.equal(app.root.dataset.currentPhotoId, '2');
        await app.runTimer(7000);
        assert.equal(app.root.dataset.currentPhotoId, '3');
    } else if (scenario === 'empty_to_live') {
        const app = makeApp([page([]), page([photo(5)]), page([photo(6)])]);
        await app.settle();
        assert.equal(app.root.dataset.state, 'EMPTY');
        await app.runTimer(5000);
        assert.equal(app.root.dataset.currentPhotoId, '5');
        assert.equal(app.root.dataset.state, 'PLAYING');
        assert.equal(app.timersAt(7000), 0);
        await app.runTimer(5000);
        assert.equal(app.root.dataset.currentPhotoId, '5');
        assert.equal(app.timersAt(7000), 1);
        await app.runTimer(7000);
        assert.equal(app.root.dataset.currentPhotoId, '6');
    } else if (scenario === 'paused_live') {
        const app = makeApp([page([photo(1), photo(2)]), page([photo(3)])]);
        await app.settle();
        await app.click('slideshow-toggle-playback');
        assert.equal(app.root.dataset.state, 'PAUSED');
        await app.runTimer(5000);
        assert.equal(app.root.dataset.state, 'PAUSED');
        assert.equal(app.root.dataset.currentPhotoId, '1');
        assert.equal(app.timersAt(5000), 1);
        await app.click('slideshow-toggle-playback');
        await app.runTimer(7000);
        assert.equal(app.root.dataset.currentPhotoId, '3');
    } else if (scenario === 'backoff') {
        const app = makeApp([
            page([photo(1), photo(2)]),
            { status: 0, networkError: true },
            { status: 500, body: {} },
            { status: 200, body: {}, invalidJson: true },
            page([]),
        ]);
        await app.settle();
        await app.runTimer(5000);
        assert.equal(app.timersAt(10000), 1);
        assert.equal(app.root.dataset.state, 'PLAYING');
        await app.runTimer(10000);
        assert.equal(app.timersAt(20000), 1);
        await app.runTimer(20000);
        assert.equal(app.timersAt(30000), 1);
        await app.runTimer(30000);
        assert.equal(app.timersAt(5000), 1);
        assert.equal(app.root.dataset.currentPhotoId, '1');
    } else if (scenario === 'unavailable') {
        const app = makeApp([page([photo(1), photo(2)]), { status: 404, body: {} }]);
        await app.settle();
        await app.runTimer(5000);
        assert.equal(app.root.dataset.state, 'UNAVAILABLE');
        assert.equal(app.elements['slideshow-status'].textContent, 'Presentación no disponible');
        assert.equal(app.elements['slideshow-image'].src, '');
        assert.equal(app.timersAt(5000), 0);
        assert.equal(app.timersAt(7000), 0);
        assert.equal(app.timersAt(2700000), 0);
        const requests = app.calls.fetchUrls.length;
        await app.visibility(false);
        assert.equal(app.calls.fetchUrls.length, requests);
    } else if (scenario === 'visibility') {
        const app = makeApp([page([photo(1), photo(2)]), page([]), page([])]);
        await app.settle();
        await app.visibility(true);
        assert.equal(app.timersAt(5000), 0);
        assert.equal(app.timersAt(30000), 1);
        assert.equal(app.timersAt(7000), 1);
        await app.runTimer(30000);
        assert.equal(app.timersAt(30000), 1);
        await app.visibility(false);
        assert.equal(app.timersAt(0), 1);
        await app.runTimer(0);
        assert.equal(app.timersAt(5000), 1);
    } else if (scenario === 'pagination_progress') {
        const app = makeApp([
            page([photo(1), photo(2)]),
            page([photo(3)], { hasMore: true, nextAfterId: 3 }),
            page([photo(4)]),
        ]);
        await app.settle();
        await app.runTimer(5000);
        assert.equal(app.calls.fetchUrls.length, 3);
        assert.ok(app.calls.fetchUrls[1].includes('after_id=2'));
        assert.ok(app.calls.fetchUrls[2].includes('after_id=3'));
        await app.runTimer(7000);
        assert.equal(app.root.dataset.currentPhotoId, '3');
        await app.runTimer(7000);
        assert.equal(app.root.dataset.currentPhotoId, '4');

        const stalled = makeApp([
            page([photo(1), photo(2)]),
            page([], { hasMore: true, nextAfterId: 2 }),
        ]);
        await stalled.settle();
        await stalled.runTimer(5000);
        assert.equal(stalled.calls.fetchUrls.length, 2);
        assert.equal(stalled.timersAt(10000), 1);
        assert.equal(stalled.root.dataset.state, 'PLAYING');
    } else if (scenario === 'overlap') {
        const app = makeApp([
            page([photo(1), photo(2)]),
            page([], { deferred: true }),
            page([]),
        ]);
        await app.settle();
        await app.runTimer(5000);
        assert.equal(app.calls.fetchUrls.length, 2);
        assert.equal(app.calls.activeFetches, 1);
        await app.visibility(false);
        assert.equal(app.calls.fetchUrls.length, 2);
        await app.releaseFetch();
        assert.equal(app.calls.maxActiveFetches, 1);
        assert.equal(app.timersAt(0), 1);
        await app.runTimer(0);
        assert.equal(app.calls.fetchUrls.length, 3);
        assert.equal(app.calls.maxActiveFetches, 1);
        assert.equal(app.timersAt(5000), 1);
    } else if (scenario === 'resync') {
        const preserved = makeApp([
            page([photo(1, '-old'), photo(2, '-old')]),
            page([photo(1, '-new'), photo(2, '-new'), photo(3, '-new')]),
        ]);
        await preserved.settle();
        const visibleBefore = [...preserved.calls.domImages];
        await preserved.runTimer(2700000);
        assert.equal(preserved.root.dataset.currentPhotoId, '1');
        assert.deepEqual(preserved.calls.domImages, visibleBefore);
        assert.ok(preserved.calls.preloads.includes('https://images.invalid/2-new'));
        await preserved.click('slideshow-next');
        assert.equal(preserved.root.dataset.currentPhotoId, '2');

        const removed = makeApp([
            page([photo(1, '-old'), photo(2, '-old'), photo(3, '-old')]),
            page([photo(1, '-new'), photo(3, '-new')]),
        ]);
        await removed.settle();
        await removed.click('slideshow-next');
        assert.equal(removed.root.dataset.currentPhotoId, '2');
        await removed.runTimer(2700000);
        assert.equal(removed.root.dataset.currentPhotoId, '3');
        assert.equal(removed.elements['slideshow-image'].src, 'https://images.invalid/3-new');
        await removed.click('slideshow-next');
        assert.equal(removed.root.dataset.currentPhotoId, '1');
    } else {
        throw new Error(`Unknown scenario: ${scenario}`);
    }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
"""


class SlideshowLiveFrontendTests(SimpleTestCase):
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

    def test_poll_uses_highest_cursor_and_empty_response_keeps_playback(self):
        self.run_scenario("poll_cursor_empty")

    def test_new_photos_are_deduplicated_and_consumed_fifo_once(self):
        self.run_scenario("new_fifo")

    def test_empty_player_and_single_photo_become_live(self):
        self.run_scenario("empty_to_live")

    def test_pause_keeps_polling_and_resume_prioritizes_news(self):
        self.run_scenario("paused_live")

    def test_network_5xx_and_invalid_payload_backoff_then_reset(self):
        self.run_scenario("backoff")

    def test_404_stops_player_and_all_live_timers(self):
        self.run_scenario("unavailable")

    def test_visibility_reduces_polling_and_visible_poll_is_prompt(self):
        self.run_scenario("visibility")

    def test_incremental_pagination_and_nonprogressing_cursor_guard(self):
        self.run_scenario("pagination_progress")

    def test_requests_do_not_overlap_and_use_one_poll_scheduler(self):
        self.run_scenario("overlap")

    def test_resync_renews_urls_reconciles_deletions_and_preserves_current(self):
        self.run_scenario("resync")

    def test_live_player_has_no_r2_calls_or_sensitive_fields(self):
        for forbidden in (
            "head_object",
            "get_object",
            "object_key",
            "mesa.token",
            "codigo_acceso",
            "setInterval",
        ):
            self.assertNotIn(forbidden, self.template)
