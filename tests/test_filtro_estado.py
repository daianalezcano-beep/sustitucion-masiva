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
        self.assertEqual([t for t in self.tildados() if t.endswith("(no)")], ["Cancelled(no)"])    # todos menos los excluidos (CX)

    def test_sin_estado_deja_destildado_solo_cancelled_exacto(self):
        sd.fijar_service_status(self.d, "Initial")
        sd.fijar_service_status(self.d)
        estados = dict((e["texto"], e["tilda"]) for e in self.d.execute_script(sd.JS_ESTADOS, "__DOC__", "service status"))
        self.assertFalse(estados["Cancelled"])
        self.assertTrue(all(v for k, v in estados.items() if k not in ("Cancelled", "All")))   # "Cancel Hyperg c/IVA" y los demás, tildados

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


def html_booking(nombres):
    items = "".join(f'<li class="list-row"><label class="tpcheckbox"><input type="checkbox" checked><i></i></label>'
                    f'<label class="tplabel-list">{n}</label></li>' for n in nombres)
    return f'<div><div class="legend">Booking Status</div><ul>{items}</ul></div>'


@unittest.skipIf(sd is None or not os.path.exists(CHROMIUM), "falta playwright o chromium")
class BookingStatusCancelado(unittest.TestCase):
    def comprobar(self, nombres, esperado):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
            pagina = browser.new_page()
            pagina.set_content(html_booking(nombres))
            guardado = (sd.dialog_subs, sd.pausa)
            sd.dialog_subs, sd.pausa = (lambda d: "__DOC__"), (lambda s: None)
            try:
                d = Driver(pagina)
                excluido, tildados = sd.destildar_booking_status(d)
                estados = d.execute_script(sd.JS_ESTADOS, "__DOC__", "booking status")
            finally:
                sd.dialog_subs, sd.pausa = guardado
                browser.close()
        self.assertEqual(excluido.lower(), esperado.lower())
        self.assertEqual([e["texto"] for e in estados if not e["tilda"]], [esperado])     # solo ese quedó destildado
        self.assertNotIn(esperado, tildados)

    def test_produccion_cx_cancelled(self):
        self.comprobar(["All", "CC- CXL with Charges", "CX - Cancelled", "IC", "OK"], "CX - Cancelled")

    def test_test_se_llama_cancelled(self):
        self.comprobar(["ALL", "CANCELLED", "CXL WITH CHARGES", "FIT CONFIRMED", "QUOTE"], "CANCELLED")

    def test_sin_ninguno_es_error(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
            pagina = browser.new_page()
            pagina.set_content(html_booking(["All", "CXL With Charges", "Quote"]))
            guardado = sd.dialog_subs
            sd.dialog_subs = lambda d: "__DOC__"
            try:
                with self.assertRaises(sd.ErrorFila):
                    sd.destildar_booking_status(Driver(pagina))
            finally:
                sd.dialog_subs = guardado
                browser.close()


DEPARTAMENTOS = ["All", "", "AC - Accounts", "FI - Fit´ Bookings", "FG - GRUPOS en FITS", "GP - Groups", "GR - Groups",
                 "PE-Promocion Comercial Emisivo", "PR-PromocionComercialReceptivo"]


def html_department(nombres):
    items = "".join(f'<li class="list-row"><label class="tpcheckbox"><input type="checkbox" checked><i></i></label>'
                    f'<label class="tplabel-list">{n}</label></li>' for n in nombres)
    return f'<div><div class="legend">Booking Status</div><ul><li class="list-row"><input type="checkbox" checked><i></i><label class="tplabel-list">X</label></li></ul></div><div><div class="legend">Department</div><ul>{items}</ul></div>'


@unittest.skipIf(sd is None or not os.path.exists(CHROMIUM), "falta playwright o chromium")
class FiltroDepartment(unittest.TestCase):
    def setUp(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
        pagina = self.browser.new_page()
        pagina.set_content(html_department(DEPARTAMENTOS))
        self.d = Driver(pagina)
        self.guardado = (sd.dialog_subs, sd.pausa)
        sd.dialog_subs, sd.pausa = (lambda d: "__DOC__"), (lambda s: None)

    def tearDown(self):
        sd.dialog_subs, sd.pausa = self.guardado
        self.browser.close()
        self.pw.stop()

    def tildados(self):
        return [sd.codigo_department(e["texto"]) or "(vacío)" for e in self.d.execute_script(sd.JS_ESTADOS, "__DOC__", "department")
                if e["tilda"] and e["texto"].lower() != "all"]

    def test_el_codigo_sale_del_texto_con_o_sin_espacios(self):
        self.assertEqual([sd.codigo_department(t) for t in ("GP - Groups", "PE-Promocion Comercial", "", "All")], ["GP", "PE", "", ""])

    def test_solo_los_codigos_pedidos_quedan_tildados(self):
        tildados = sd.fijar_departamentos(self.d, {"FI", "FG", "PE"})
        self.assertEqual(sorted(tildados), ["FG", "FI", "PE"])
        self.assertEqual(sorted(self.tildados()), ["FG", "FI", "PE"])                    # el ítem vacío, AC, GP, GR y PR quedaron sin tildar
        sd.fijar_departamentos(self.d, {"GP", "GR"})
        self.assertEqual(sorted(self.tildados()), ["GP", "GR"])

    def test_sin_codigos_se_tilda_todo(self):
        sd.fijar_departamentos(self.d, {"GP"})
        sd.fijar_departamentos(self.d, None)
        self.assertEqual(len(self.tildados()), len(DEPARTAMENTOS) - 1)                    # todos menos 'All'

    def test_si_ya_esta_como_se_pide_no_clickea(self):
        sd.fijar_departamentos(self.d, {"GP", "GR"})
        clicks = []
        original = self.d.execute_script
        self.d.execute_script = lambda src, *a: (clicks.append(1) if src is sd.JS_CLICK_ESTADOS_IDX else None) or original(src, *a)
        sd.fijar_departamentos(self.d, {"GP", "GR"})
        self.assertEqual(clicks, [])

    def test_sin_el_grupo_o_sin_ninguno_de_los_pedidos_es_error(self):
        with self.assertRaises(sd.ErrorFila) as cm:
            sd.fijar_departamentos(self.d, {"ZZ"})
        self.assertIn("Ningún Department", cm.exception.detalle)
        self.d.pagina.set_content(html_booking(["All", "OK"]))
        with self.assertRaises(sd.ErrorFila) as cm:
            sd.fijar_departamentos(self.d, {"GP"})
        self.assertIn("No encontré el grupo Department", cm.exception.detalle)


if __name__ == "__main__":
    unittest.main()
