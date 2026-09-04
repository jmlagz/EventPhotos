"""Execute the actual upload-page JavaScript with a fake DOM and fetch.

Uses the installed Node runtime (built-ins only); no browser, database or network.
"""

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
const ID = '11111111-1111-4111-8111-111111111111';
const ID2 = '22222222-2222-4222-8222-222222222222';
const ID3 = '33333333-3333-4333-8333-333333333333';
const success = { status: 200, body: { ok: true, foto_id: 1 } };

function response(spec) {
    return {
        ok: spec.status >= 200 && spec.status < 300,
        status: spec.status,
        json: async () => {
            if (spec.nonJson) throw new SyntaxError('PRIVATE PARSE ERROR <html>');
            return spec.body;
        },
    };
}

function makeApp(confirmResponses, options = {}) {
    function element() {
        return {
            disabled: false, textContent: '', style: {}, files: [], listeners: {},
            addEventListener(name, callback) { this.listeners[name] = callback; },
            appendChild() {},
        };
    }
    const elements = Object.fromEntries(
        ['selectorFotos', 'galeria', 'contador', 'mensaje', 'botonSubir'].map(id => [id, element()])
    );
    const calls = { presign: 0, put: 0, confirmations: [] };
    const context = vm.createContext({
        document: { getElementById: id => elements[id], createElement: element },
        URL: { createObjectURL: () => 'blob:fake' },
        URLSearchParams, console: { log() {}, error() {} },
        fetch: async (url, init) => {
            if (init.method === 'PUT') {
                calls.put++;
                assert.equal(init.headers['If-None-Match'], '*');
                assert.equal(init.headers['Content-Type'], 'image/jpeg');
                assert.equal(init.body.type, 'image/jpeg');
                return response({ status: options.putStatus || 200 });
            }
            assert.equal(init.method, 'POST');
            assert.ok(init.headers['X-CSRFToken']);
            if (url.includes('solicitar_url_subida')) {
                calls.presign++;
                assert.equal(init.body.get('tamaño'), '3');
                if (options.presignDuplicate) return response({ status: 200, body: { duplicada: true } });
                return response({ status: 200, body: {
                    upload_intent_id: [ID, ID2, ID3][calls.presign - 1],
                    url: 'https://upload.invalid/signed-secret',
                    object_key: 'DO-NOT-RETAIN-THIS-KEY',
                    headers: { 'Content-Type': 'image/jpeg', 'If-None-Match': '*' },
                } });
            }
            assert.ok(url.includes('confirmar_subida'), 'Only expected requests allowed');
            assert.deepEqual(Array.from(init.body.keys()), ['upload_intent_id']);
            calls.confirmations.push(init.body.get('upload_intent_id'));
            let spec = confirmResponses.shift();
            assert.ok(spec, 'Unexpected additional confirmation');
            if (typeof spec === 'function') spec = await spec();
            if (spec.networkError) throw new TypeError('PRIVATE NETWORK ERROR');
            return response(spec);
        },
    });
    vm.runInContext(script, context);
    const file = name => ({ name, type: 'image/jpeg', size: 3, arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer });
    const select = (count = 1) => {
        elements.selectorFotos.files = Array.from({ length: count }, (_, i) => file(`foto-${i}.jpg`));
        elements.selectorFotos.listeners.change();
    };
    select(options.files || 1);
    return {
        elements, calls, select, context,
        click: () => elements.botonSubir.listeners.click(),
        message: () => elements.mensaje.textContent,
        retry: () => !elements.botonSubir.disabled && elements.botonSubir.textContent === 'Reintentar confirmación',
    };
}

async function retryWorks(first) {
    const app = makeApp([first, success]);
    await app.click();
    assert.equal(app.retry(), true);
    assert.equal(app.elements.selectorFotos.disabled, true);
    const recovery = JSON.parse(vm.runInContext('JSON.stringify(Array.from(progresoFotos.values()))', app.context));
    assert.deepEqual(recovery, [{ uploadIntentId: ID, reintentos: 0, resultado: null }]);
    assert.ok(!app.message().includes('PRIVATE'));
    await app.click();
    assert.deepEqual(app.calls, { presign: 1, put: 1, confirmations: [ID, ID] });
    assert.equal(app.retry(), false);
    assert.equal(app.elements.botonSubir.disabled, true);
    assert.equal(app.message(), '✅ 1 foto(s) subida(s) correctamente.');
    assert.equal(vm.runInContext('progresoFotos.values().next().value.uploadIntentId', app.context), undefined);
    return app;
}

async function main() {
    if (scenario === 'success') {
        const app = makeApp([success]);
        await app.click();
        assert.deepEqual(app.calls, { presign: 1, put: 1, confirmations: [ID] });
        assert.equal(app.retry(), false);
        assert.equal(app.message(), '✅ 1 foto(s) subida(s) correctamente.');
        await app.click();
        assert.equal(app.calls.confirmations.length, 1);
    } else if (scenario === 'transient') {
        await retryWorks({ status: 503, body: { error: 'No fue posible materializar la foto.' } });
    } else if (scenario === 'network') {
        await retryWorks({ networkError: true });
    } else if (scenario === 'non_json') {
        await retryWorks({ status: 500, nonJson: true });
    } else if (scenario === 'ambiguous_success') {
        for (const spec of [{ status: 200, nonJson: true }, { status: 200, body: {} }]) await retryWorks(spec);
    } else if (scenario === 'transient_statuses') {
        for (const status of [500, 502, 503, 504]) await retryWorks({ status, body: { error: 'Error temporal del servicio.' } });
    } else if (scenario === 'definitive') {
        for (const status of [400, 401, 403, 404, 409, 410, 412, 429, 501]) {
            for (const nonJson of [false, true]) {
                const app = makeApp([{ status, nonJson, body: { error: 'Rechazo definitivo del servidor.' } }]);
                await app.click();
                assert.equal(app.retry(), false);
                assert.equal(app.elements.botonSubir.disabled, true);
                assert.ok(!app.message().includes('PRIVATE'));
                if (!nonJson) assert.ok(app.message().includes('Rechazo definitivo del servidor.'));
                await app.click();
                assert.deepEqual(app.calls, { presign: 1, put: 1, confirmations: [ID] });
            }
        }
    } else if (scenario === 'duplicate') {
        const app = makeApp([{ status: 503, body: { error: 'Error temporal.' } }, { status: 200, body: { duplicada: true } }]);
        await app.click();
        await app.click();
        assert.deepEqual(app.calls, { presign: 1, put: 1, confirmations: [ID, ID] });
        assert.equal(app.message(), '♻️ Las 1 foto(s) ya habían sido compartidas.');
        const existing = makeApp([], { presignDuplicate: true });
        await existing.click();
        assert.deepEqual(existing.calls, { presign: 1, put: 0, confirmations: [] });
    } else if (scenario === 'repeated_failure') {
        const failure = { status: 503, body: { error: 'Mensaje JSON conservado.' } };
        const app = makeApp([failure, failure, failure, failure]);
        await app.click();
        assert.ok(app.message().includes('Mensaje JSON conservado.'));
        for (let i = 0; i < 3; i++) {
            assert.equal(app.retry(), true);
            await app.click();
        }
        assert.equal(app.retry(), false);
        assert.equal(app.elements.botonSubir.disabled, true);
        assert.ok(app.message().includes('tras varios intentos'));
        await app.click();
        assert.deepEqual(app.calls, { presign: 1, put: 1, confirmations: [ID, ID, ID, ID] });
    } else if (scenario === 'double_click') {
        let release;
        const wait = new Promise(resolve => { release = resolve; });
        const app = makeApp([{ status: 503, body: {} }, () => wait]);
        await app.click();
        const first = app.click();
        assert.equal(app.elements.botonSubir.disabled, true);
        await app.click(); // Call handler directly even though button is disabled.
        app.select(); // Also try to replace selection while confirming.
        assert.deepEqual(app.calls.confirmations, [ID, ID]);
        release(success);
        await first;
        assert.deepEqual(app.calls, { presign: 1, put: 1, confirmations: [ID, ID] });
    } else if (scenario === 'batch') {
        const app = makeApp([success, { status: 503, body: {} }, success, { status: 200, body: { duplicada: true } }], { files: 3 });
        await app.click();
        assert.deepEqual(app.calls, { presign: 2, put: 2, confirmations: [ID, ID2] });
        app.select(1); // Pending confirmation must keep the original batch.
        await app.click();
        assert.deepEqual(app.calls, { presign: 3, put: 3, confirmations: [ID, ID2, ID2, ID3] });
        assert.equal(app.message(), '✅ 2 foto(s) subida(s). ♻️ 1 ya existían.');
    } else if (scenario === 'put_failure') {
        const app = makeApp([], { putStatus: 403 });
        await app.click();
        assert.equal(app.retry(), false);
        assert.equal(app.calls.confirmations.length, 0);
        assert.equal(vm.runInContext('progresoFotos.size', app.context), 0);
        assert.ok(!app.message().includes('R2'));
    } else if (scenario === 'page_lifetime') {
        const app = makeApp([{ status: 503, body: {} }]);
        await app.click();
        assert.equal(app.retry(), true);
        const fresh = makeApp([success]);
        assert.equal(vm.runInContext('progresoFotos.size', fresh.context), 0);
        assert.equal(fresh.calls.confirmations.length, 0);
        assert.ok(!/localStorage|sessionStorage|indexedDB/.test(script));
    } else {
        throw new Error('Unknown scenario');
    }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
"""


class UploadConfirmationRetryFrontendTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.node = shutil.which("node")
        template = (
            Path(__file__).parent / "templates" / "eventos" / "subir_fotos.html"
        ).read_text(encoding="utf-8")
        cls.script = re.search(r"<script>(.*?)</script>", template, re.DOTALL).group(1)

    def run_scenario(self, scenario):
        self.assertIsNotNone(self.node, "These frontend tests require the local Node runtime; no npm packages are needed.")
        result = subprocess.run(
            [self.node, "-e", HARNESS],
            input=json.dumps({"script": self.script, "scenario": scenario}),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_normal_confirmation(self):
        self.run_scenario("success")

    def test_transient_failure_retries_same_uuid_without_presign_or_put(self):
        self.run_scenario("transient")

    def test_network_failure_retries_only_confirmation(self):
        self.run_scenario("network")

    def test_500_non_json_has_friendly_message_and_retry(self):
        self.run_scenario("non_json")

    def test_ambiguous_success_does_not_report_success(self):
        self.run_scenario("ambiguous_success")

    def test_explicit_transient_status_allowlist(self):
        self.run_scenario("transient_statuses")

    def test_definitive_errors_do_not_retry(self):
        self.run_scenario("definitive")

    def test_duplicate_response_keeps_normal_visual_result(self):
        self.run_scenario("duplicate")

    def test_manual_retries_are_bounded_and_preserve_json_error(self):
        self.run_scenario("repeated_failure")

    def test_double_click_cannot_confirm_concurrently(self):
        self.run_scenario("double_click")

    def test_batch_resumes_without_reuploading_completed_files(self):
        self.run_scenario("batch")

    def test_failed_put_does_not_offer_confirmation_retry(self):
        self.run_scenario("put_failure")

    def test_context_is_only_in_page_memory(self):
        self.run_scenario("page_lifetime")
