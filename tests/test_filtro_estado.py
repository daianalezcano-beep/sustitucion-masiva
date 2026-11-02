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


def html_results(filas, scroll=False):
    """Results de Substitute Service simulado: encabezado con checkbox (marca/desmarca todo), filas con checkbox y los contadores Found / Selected."""
    cuerpo = "".join(
        f'<tr><td class="tpcol-checkbox tpcol-substituteservice"><label><i></i><input type="checkbox"></label></td>'
        f'<td class="tpcol-locationcode">BUE</td><td class="tpcol-servicecode">GU</td><td class="tpcol-suppliercode">1ACOS1</td>'
        f'<td class="tpcol-optioncode">700GUI</td><td class="tpcol-bookingreference">{ref}</td><td class="tpcol-servicedate">{fecha}</td></tr>'
        for ref, fecha in filas)
    estilo = "height:90px;overflow:auto;" if scroll else ""
    return f"""
    <div id="resultsTab"><table><tr><td><tp-label tplabeltype="Found"></tp-label></td><td class="align-right"><label id="found">{len(filas)}</label></td></tr>
      <tr><td><tp-label tplabeltype="Selected"></tp-label></td><td class="align-right"><label id="sel">0</label></td></tr></table>
      <div style="{estilo}"><tp-grid><table><thead><tr><th class="tpcol-checkbox"><label><i></i><input type="checkbox" id="todos"></label></th></tr></thead><tbody>{cuerpo}</tbody></table></tp-grid></div></div>
    <script>
      const cuenta = () => document.querySelector('#sel').textContent = document.querySelectorAll('tp-grid tbody input:checked').length;
      document.querySelector('#todos').addEventListener('click', e => {{ document.querySelectorAll('tp-grid tbody input').forEach(i => i.checked = e.target.checked); cuenta(); }});
      document.querySelectorAll('tp-grid tbody input').forEach(i => i.addEventListener('click', cuenta));
    </script>"""


@unittest.skipIf(sd is None or not os.path.exists(CHROMIUM), "falta playwright o chromium")
class ExclusionPorLinea(unittest.TestCase):
    FILAS = [("EAGR1", "02/Nov/2026"), ("EAGR2", "02/Nov/2026"), ("EAGR2", "02/Nov/2026"), ("EAGR3", "03/Nov/2026"), ("EAGR4", "02/Nov/2026")]

    def setUp(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
        self.pagina = self.browser.new_page()
        self.d = Driver(self.pagina)
        self.guardado = (sd.pausa, sd.jc)
        sd.pausa = lambda s: None
        sd.jc = lambda d, el: d.pagina.evaluate("document.querySelector('#todos').click()")      # el único click por jc es el del encabezado

    def tearDown(self):
        sd.pausa, sd.jc = self.guardado
        self.browser.close()
        self.pw.stop()

    def cargar(self, filas, scroll=False):
        self.pagina.set_content(html_results(filas, scroll))

    def seleccionadas(self):
        return self.pagina.evaluate("Array.from(document.querySelectorAll('tp-grid tbody tr')).filter(r => r.querySelector('input').checked)"
                                    ".map(r => r.querySelector('.tpcol-bookingreference').textContent + ' ' + r.querySelector('.tpcol-servicedate').textContent)")

    def excluir(self, *claves):
        return [{"fila": {"booking_ref": r, "date": f, "location": "BUE", "service": "GU", "supplier": "1ACOS1", "code": "700GUI"}} for r, f in claves]

    def test_destilda_solo_las_filas_pedidas_y_no_vuelve_a_clickear_las_ya_destildadas(self):
        self.cargar(self.FILAS)
        self.pagina.evaluate("document.querySelectorAll('tp-grid tbody input').forEach(i => i.checked = true)")
        claves = [sd.X.clave_para_pantalla(e["fila"]) for e in self.excluir(("EAGR2", "02/11/2026"))]
        self.assertEqual(claves, ["EAGR2|02/NOV/2026|BUE|GU|1ACOS1|700GUI"])
        self.assertEqual(len(self.d.execute_script(sd.JS_DESTILDAR_FILAS, claves)), 2)           # las dos filas con esa clave (no se distinguen)
        self.assertEqual(self.d.execute_script(sd.JS_DESTILDAR_FILAS, claves), [])               # segunda pasada: ya están destildadas, no se tocan
        self.assertEqual(sorted(self.seleccionadas()), ["EAGR1 02/Nov/2026", "EAGR3 03/Nov/2026", "EAGR4 02/Nov/2026"])

    def test_la_fecha_distingue_la_clave(self):
        self.cargar([("EAGR1", "02/Nov/2026"), ("EAGR1", "03/Nov/2026")])
        self.pagina.evaluate("document.querySelectorAll('tp-grid tbody input').forEach(i => i.checked = true)")
        self.d.execute_script(sd.JS_DESTILDAR_FILAS, ["EAGR1|03/NOV/2026|BUE|GU|1ACOS1|700GUI"])
        self.assertEqual(self.seleccionadas(), ["EAGR1 02/Nov/2026"])

    def probar(self, scroll=False):
        """seleccionar_con_exclusiones del script, con 'jc' haciendo el click real sobre el elemento."""
        sd.seleccionar_con_exclusiones(self.d, len(self.FILAS), self.excluir(("EAGR2", "02/11/2026")), len(self.FILAS) - 2)

    def test_selecciona_todo_y_deja_el_conteo_esperado(self):
        self.cargar(self.FILAS)
        self.probar()
        self.assertEqual(self.pagina.evaluate("document.querySelector('#sel').textContent"), "3")
        self.assertEqual(sorted(self.seleccionadas()), ["EAGR1 02/Nov/2026", "EAGR3 03/Nov/2026", "EAGR4 02/Nov/2026"])

    def test_con_scroll_virtual_encuentra_las_filas_fuera_de_vista(self):
        filas = [(f"EAGR{i}", "02/Nov/2026") for i in range(1, 30)]
        self.FILAS = filas
        self.cargar(filas, scroll=True)
        sd.seleccionar_con_exclusiones(self.d, len(filas), self.excluir(("EAGR28", "02/11/2026")), len(filas) - 1)     # la fila 28 está bajo el scroll
        self.assertNotIn("EAGR28 02/Nov/2026", self.seleccionadas())
        self.assertEqual(len(self.seleccionadas()), len(filas) - 1)

    def test_si_el_conteo_no_coincide_rechaza_y_deja_todo_sin_seleccionar(self):
        self.cargar(self.FILAS)
        with self.assertRaises(sd.ErrorFila) as cm:                                               # se espera destildar 2 filas y la clave no existe en la grilla
            sd.seleccionar_con_exclusiones(self.d, len(self.FILAS), self.excluir(("NOEXISTE", "02/11/2026")), len(self.FILAS) - 1)
        self.assertIn("no se movió nada", cm.exception.detalle)
        self.assertEqual(self.seleccionadas(), [])                                                # no queda una selección parcial


if __name__ == "__main__":
    unittest.main()
