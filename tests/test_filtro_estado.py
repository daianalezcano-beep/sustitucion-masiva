"""Prueba del filtro Service Status (fijar_service_status) con un HTML que imita la estructura real de la pestaña
Filter (grupos div.legend + li.list-row con checkbox, <i> y label.tplabel-list). Usa Chromium con Playwright;
se omite si no están disponibles."""
import os
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "scripts", "substitute_doble"))

CHROMIUM = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium")
try:
    import substitute_doble as sd
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
    sd = None

NOMBRES = ["All", "Cancel Hyperg c/IVA", "Cancellation Charge", "Cancellation Charge", "Cancelled", "Initial", "OK Tráfico"]
ITEMS = "".join(f'<li class="list-row"><label class="tpcheckbox"><input type="checkbox" id="c{i}" checked><i></i></label>'
                f'<label class="tplabel-list" for="c{i}">{n}</label></li>' for i, n in enumerate(NOMBRES))
HTML = ('<div><div class="legend">Booking Status</div><ul><li class="list-row"><input type="checkbox" checked><i></i>'
        f'<label class="tplabel-list">X</label></li></ul></div><div><div class="legend">Service Status</div><ul>{ITEMS}</ul></div>')


class Driver:
    """Hace de driver de Selenium sobre una página de Playwright (el elemento '__DOC__' es el document)."""

    def __init__(self, pagina):
        self.pagina = pagina

    def execute_script(self, src, *args):
        return self.pagina.evaluate("([src, args]) => new Function(src).apply(null, args.map(a => a === '__DOC__' ? document : a))",
                                    [src, list(args)])


@unittest.skipIf(sd is None or not os.path.exists(CHROMIUM), "falta playwright o chromium")
class FiltroServiceStatus(unittest.TestCase):
    def setUp(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
        pagina = self.browser.new_page()
        pagina.set_content(HTML)
        self.d = Driver(pagina)
        self.guardado = (sd.dialog_subs, sd.pausa)
        sd.dialog_subs, sd.pausa = (lambda d: "__DOC__"), (lambda s: None)

    def tearDown(self):
        sd.dialog_subs, sd.pausa = self.guardado
        self.browser.close()
        self.pw.stop()

    def tildados(self, grupo="service status"):
        return [e["texto"] + ("" if e["tilda"] else "(no)") for e in self.d.execute_script(sd.JS_ESTADOS, "__DOC__", grupo)]

    def test_solo_un_estado_y_volver_a_todos(self):
        sd.fijar_service_status(self.d, "Initial")
        self.assertEqual([t for t in self.tildados() if not t.endswith("(no)") and t != "All"], ["Initial"])
        sd.fijar_service_status(self.d)
        self.assertFalse([t for t in self.tildados() if t.endswith("(no)")])

    def test_texto_repetido_se_distingue_por_n(self):
        sd.fijar_service_status(self.d, "Cancellation Charge", 1)
        self.assertEqual(self.tildados()[2:4], ["Cancellation Charge(no)", "Cancellation Charge"])
        sd.fijar_service_status(self.d, "Cancellation Charge", 0)
        self.assertEqual(self.tildados()[2:4], ["Cancellation Charge", "Cancellation Charge(no)"])

    def test_item_inexistente_es_error_y_booking_status_no_se_toca(self):
        with self.assertRaises(sd.ErrorFila) as cm:
            sd.fijar_service_status(self.d, "NoExiste")
        self.assertIn("no tiene el ítem", cm.exception.detalle)
        self.assertEqual(self.tildados("booking status"), ["X"])


if __name__ == "__main__":
    unittest.main()
