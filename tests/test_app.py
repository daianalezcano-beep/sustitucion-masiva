"""Prueba de app.py renderizándola con el marco de pruebas de Streamlit (AppTest): sin Tourplan ni Sheets.
Se reemplaza subprocess.Popen para ver con qué entorno se lanzaría el script. Se omite si falta streamlit."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

try:
    from streamlit.testing.v1 import AppTest
    from common import ejecucion as E
    from common import user_config
except ModuleNotFoundError:
    AppTest = None


class ProcFalso:
    def __init__(self, *a, **kw):
        ProcFalso.llamada = (a, kw)
        self.stdout = iter(["línea de consola 1\n", "línea de consola 2\n"])
        self.returncode = 0

    def wait(self):
        return 0


@unittest.skipIf(AppTest is None, "falta streamlit")
class App(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.guardado = (user_config.CONFIG_DIR, user_config.CONFIG_PATH)
        user_config.CONFIG_DIR = self.tmp
        user_config.CONFIG_PATH = os.path.join(self.tmp, "config.json")

    def tearDown(self):
        user_config.CONFIG_DIR, user_config.CONFIG_PATH = self.guardado

    def app(self):
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        return at.run()

    def configurar(self, sheet="https://docs.google.com/spreadsheets/d/XYZ/edit"):
        user_config.guardar({"tp_usuario": "daiana", "tp_password": "secreta", "headless": False,
                             "sheet_urls": {"substitute_doble": sheet}})

    def boton(self, at, texto):
        return next(b for b in at.button if texto in b.label)

    @staticmethod
    def _aplanar(bloque):
        for hijo in bloque.children.values():
            yield hijo
            if hasattr(hijo, "children"):
                yield from App._aplanar(hijo)

    def test_orden_de_la_pantalla_cola_arriba_y_parametros_plegables(self):
        self.configurar()
        at = self.app()
        self.assertFalse(at.exception)
        tipos = [(e.type, getattr(e, "label", None) or getattr(e, "value", None)) for e in self._aplanar(at.main)]
        pos = lambda tipo, texto: next(i for i, (t, v) in enumerate(tipos) if t == tipo and texto in str(v))
        self.assertLess(pos("subheader", "Cola de trabajo"), pos("expander", "Configuración"))
        self.assertLess(pos("expander", "Configuración"), pos("expander", "Parámetros"))
        self.assertLess(pos("expander", "Parámetros"), pos("button", "Ejecutar"))       # Ejecutar queda fuera del plegable
        self.assertEqual([e.label for e in at.expander][-1], "Parámetros")
        self.assertTrue(at.expander[-1].proto.expanded)                                 # abierto cuando no hay corrida

    def test_los_parametros_se_pliegan_solos_mientras_corre(self):
        self.configurar()
        run_dir = Path(tempfile.mkdtemp())
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        at.session_state["_corrida"] = {
            "running": True, "finished": False, "log_lines": [], "returncode": None, "proc": None,
            "stop_file": run_dir / "ABORTAR.flag", "run_dir": run_dir, "abort_pedido": False, "modo": "lectura",
            "inicio": 0, "mensaje": None, "conteo": None, "conteo_error": None}
        at.run()
        self.assertFalse(at.expander[-1].proto.expanded)

    def test_boton_descargar_arriba_de_la_consola_y_sin_hueco_superior(self):
        self.configurar()
        at = self.app()
        self.assertFalse(at.exception)
        tipos = [(e.type, getattr(e, "label", None) or getattr(e, "value", None)) for e in self._aplanar(at.main)]
        pos_boton = next(i for i, (t, v) in enumerate(tipos) if t == "download_button" and v == "Descargar")
        pos_consola = next(i for i, (t, v) in enumerate(tipos) if t == "markdown" and 'class="sd-consola"' in str(v))
        self.assertLess(pos_boton, pos_consola)                                         # el botón va arriba de la consola
        self.assertFalse(any(v == "Descargar consola" for _, v in tipos))              # el texto es solo "Descargar"
        css = next(m.value for m in at.markdown if "stAppDeployButton" in m.value)
        self.assertIn("padding-top: 0 !important", css)

    def test_consola_altura_fija_escapada_y_con_la_ultima_linea_primero(self):
        self.configurar()
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        at.session_state["_corrida"] = {
            "running": False, "finished": False, "log_lines": ["primera\n", "<b>negrita</b> & más\n", "ULTIMA\n"],
            "returncode": None, "proc": None, "stop_file": None, "run_dir": None, "abort_pedido": False, "modo": "lectura",
            "inicio": None, "mensaje": None, "conteo": None, "conteo_error": None}
        at.run()
        self.assertFalse(at.exception)
        consola = next(m.value for m in at.markdown if 'class="sd-consola"' in m.value)
        self.assertNotIn("<b>negrita</b>", consola)
        self.assertIn("&lt;b&gt;negrita&lt;/b&gt; &amp; más", consola)
        # column-reverse: la última línea es la primera del HTML y queda visible abajo
        self.assertLess(consola.index("ULTIMA"), consola.index("primera"))
        self.assertNotIn("\n", consola)                                                  # una sola línea: Markdown no la parte
        css = next(m.value for m in at.markdown if "column-reverse" in m.value and "height: 340px" in m.value)
        self.assertIn("overflow-y: auto", css)

    def test_al_terminar_refresca_la_cola(self):
        self.configurar()
        run_dir = Path(tempfile.mkdtemp())
        ws = object()
        filas = [{"ESTADO": "PENDIENTE"}, {"ESTADO": "PENDIENTE"}, {"ESTADO": "OK"}, {"ESTADO": "ERROR_COLGADAS"}]
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        at.session_state["_corrida"] = {
            "running": True, "finished": True, "log_lines": ["x\n"], "returncode": 0, "proc": None, "stop_file": None,
            "run_dir": run_dir, "abort_pedido": False, "modo": "lectura", "inicio": 0, "mensaje": None, "conteo": None,
            "conteo_error": None}
        with mock.patch("common.sheets_client.conectar_sheets", return_value=ws), \
             mock.patch("common.sheets_client.cargar_sheet", return_value=(filas, ["ESTADO"])):
            at.run()
        self.assertFalse(at.exception)
        self.assertEqual({m.label: m.value for m in at.metric},
                         {"Pendiente": "2", "OK": "1", "Error": "1", "Duplicado": "0", "Otro": "0"})

    def test_renderiza_y_sin_configurar_no_se_puede_ejecutar(self):
        at = self.app()
        self.assertFalse(at.exception)
        self.assertEqual(at.title[0].value, "Actualización de costos")
        self.assertTrue(any("mediante sustitución de servicios" in m.value for m in at.markdown))
        self.assertTrue(self.boton(at, "Ejecutar").disabled)
        self.assertTrue(any("usuario" in c.value for c in at.caption))

    def test_la_url_del_sheet_solo_se_pide_en_configuracion(self):
        user_config.guardar({"tp_usuario": "u", "tp_password": "p", "headless": False, "sheet_urls": {}})
        at = self.app()
        self.assertFalse(at.exception)
        etiquetas = [t.label for t in at.text_input]
        self.assertEqual(sum("Sheet" in e for e in etiquetas), 1)             # solo el de Configuración
        self.assertNotIn("sheet_url", [t.key for t in at.text_input])
        self.assertTrue(self.boton(at, "Ejecutar").disabled)                    # sin URL guardada no se puede ejecutar
        self.assertTrue(any("URL del Google Sheet" in c.value and "Configuración" in c.value for c in at.caption))
        user_config.guardar({"tp_usuario": "u", "tp_password": "p", "headless": False,
                             "sheet_urls": {"substitute_doble": "https://docs.google.com/spreadsheets/d/Z/edit"}})
        at = self.app()
        self.assertFalse(self.boton(at, "Ejecutar").disabled)

    def test_con_configuracion_se_puede_ejecutar_en_lectura(self):
        self.configurar()
        at = self.app()
        self.assertFalse(at.exception)
        self.assertFalse(self.boton(at, "Ejecutar").disabled)
        self.assertEqual(at.radio(key="modo").value, "Solo revisar (lectura)")
        self.assertFalse(self.boton(at, "Abortar").disabled is False)      # Abortar deshabilitado sin corrida

    def test_ejecutar_lanza_el_script_con_el_entorno_correcto(self):
        self.configurar()
        at = self.app()
        at.text_input(key="base_url").set_value("https://prueba.example/tp")
        at.number_input(key="fila").set_value(2)
        with mock.patch("subprocess.Popen", ProcFalso):
            at = self.boton(at, "Ejecutar").click().run()
        self.assertFalse(at.exception)
        args, kw = ProcFalso.llamada
        self.assertTrue(args[0][1].endswith(os.path.join("scripts", "substitute_doble", "substitute_doble.py")))
        env = kw["env"]
        self.assertEqual((env["TOURPLAN_USERNAME"], env["TOURPLAN_PASSWORD"]), ("daiana", "secreta"))
        self.assertEqual((env["TOURPLAN_MODO"], env["TOURPLAN_FILA"], env["TOURPLAN_BASE_URL"]), ("lectura", "2", "https://prueba.example/tp"))
        self.assertEqual(env["TOURPLAN_FECHA_HASTA"], "31/12/49")
        self.assertRegex(env["TOURPLAN_FECHA_DESDE"], r"^\d\d/\d\d/\d\d$")
        self.assertEqual(env["TOURPLAN_SHEET_URL"], "https://docs.google.com/spreadsheets/d/XYZ/edit")
        self.assertEqual(kw["stdin"], __import__("subprocess").DEVNULL)    # nunca queda esperando una consola
        self.assertTrue(Path(env["TOURPLAN_CONFIRM_DIR"]).is_dir())

    def test_aplicar_exige_la_confirmacion_de_pendientes(self):
        self.configurar()
        at = self.app()
        at.radio(key="modo").set_value("Aplicar cambios (modifica Tourplan)").run()
        self.assertTrue(any("MODIFICA reservas reales" in w.value for w in at.warning))
        self.assertTrue(self.boton(at, "Ejecutar").disabled)
        self.assertTrue(any("000000" in c.value for c in at.caption))
        at.checkbox(key="confirmo").check().run()
        self.assertFalse(self.boton(at, "Ejecutar").disabled)
        with mock.patch("subprocess.Popen", ProcFalso):
            self.boton(at, "Ejecutar").click().run()
        env = ProcFalso.llamada[1]["env"]
        self.assertEqual((env["TOURPLAN_MODO"], env["TOURPLAN_CONFIRMO_SIN_PENDIENTES"], env["TOURPLAN_CONFIRMAR_OK"]), ("aplicar", "SI", "1"))

    def test_fechas_invalidas_bloquean(self):
        self.configurar()
        at = self.app()
        from datetime import date
        at.date_input(key="desde").set_value(date(2027, 1, 10))
        at.date_input(key="hasta").set_value(date(2027, 1, 5))
        at.run()
        self.assertTrue(self.boton(at, "Ejecutar").disabled)
        self.assertTrue(any("anterior" in c.value for c in at.caption))

    def test_botones_de_confirmacion(self):
        self.configurar()
        run_dir = Path(tempfile.mkdtemp())
        (run_dir / E.ARCHIVO_PENDIENTE).write_text("IDA: mover 1 reserva de BUE/1ACOS1/GU/700GUI", encoding="utf-8")
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        at.session_state["_corrida"] = {
            "running": True, "finished": False, "log_lines": ["hola\n"], "returncode": None, "proc": None,
            "stop_file": run_dir / "ABORTAR.flag", "run_dir": run_dir, "abort_pedido": False, "modo": "aplicar",
            "inicio": 0, "mensaje": None, "conteo": None, "conteo_error": None}
        at.run()
        self.assertFalse(at.exception)
        self.assertTrue(any("espera tu confirmación" in w.value for w in at.warning))
        self.assertTrue(self.boton(at, "Ejecutar").disabled)                 # mientras corre no se puede lanzar otra
        self.assertFalse(self.boton(at, "Abortar").disabled)
        self.boton(at, "Continuar").click().run()
        self.assertEqual((run_dir / E.ARCHIVO_RESPUESTA).read_text(encoding="utf-8"), "SI")
        (run_dir / E.ARCHIVO_RESPUESTA).unlink()
        self.boton(at, "Cancelar").click().run()
        self.assertEqual((run_dir / E.ARCHIVO_RESPUESTA).read_text(encoding="utf-8"), "NO")

    def test_abortar_crea_la_bandera(self):
        self.configurar()
        run_dir = Path(tempfile.mkdtemp())
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        at.session_state["_corrida"] = {
            "running": True, "finished": False, "log_lines": [], "returncode": None, "proc": None,
            "stop_file": run_dir / "ABORTAR.flag", "run_dir": run_dir, "abort_pedido": False, "modo": "lectura",
            "inicio": 0, "mensaje": None, "conteo": None, "conteo_error": None}
        at.run()
        self.boton(at, "Abortar").click().run()
        self.assertTrue((run_dir / "ABORTAR.flag").exists())

    def test_mensajes_finales_segun_el_codigo_de_salida(self):
        self.configurar()
        for rc, tipo, texto in ((0, "success", "Terminó bien"), (E.EXIT_ABORTADO, "info", "Abortada"),
                                (E.EXIT_DETENIDA, "error", "Corrida detenida"), (1, "error", "código 1")):
            at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
            at.session_state["_corrida"] = {
                "running": True, "finished": True, "log_lines": ["x\n"], "returncode": rc, "proc": None,
                "stop_file": None, "run_dir": None, "abort_pedido": False, "modo": "aplicar", "inicio": 0,
                "mensaje": None, "conteo": None, "conteo_error": None}
            with mock.patch("common.sheets_client.conectar_sheets", side_effect=RuntimeError("sin red en la prueba")):
                at.run()
            self.assertFalse(at.exception, rc)
            mensajes = getattr(at, tipo)
            self.assertTrue(any(texto in m.value for m in mensajes), (rc, [m.value for m in mensajes]))

    def test_guardar_configuracion(self):
        at = self.app()
        at.text_input[0].set_value("nuevo_usuario")
        at.text_input[1].set_value("clave")
        at.text_input[2].set_value("https://docs.google.com/spreadsheets/d/AAA/edit")
        self.boton(at, "Guardar").click().run()
        cfg = user_config.cargar()
        self.assertEqual((cfg["tp_usuario"], cfg["tp_password"]), ("nuevo_usuario", "clave"))
        self.assertEqual(cfg["sheet_urls"]["substitute_doble"], "https://docs.google.com/spreadsheets/d/AAA/edit")


if __name__ == "__main__":
    unittest.main()
