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

    def test_renderiza_y_sin_configurar_no_se_puede_ejecutar(self):
        at = self.app()
        self.assertFalse(at.exception)
        self.assertEqual(at.title[0].value, "Doble sustitución de costos")
        self.assertTrue(self.boton(at, "Ejecutar").disabled)
        self.assertTrue(any("usuario" in c.value for c in at.caption))

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
            at.run()
            self.assertFalse(at.exception, rc)
            mensajes = getattr(at, tipo)
            self.assertTrue(any(texto in m.value for m in mensajes), (rc, [m.value for m in mensajes]))

    def test_guardar_configuracion(self):
        at = self.app()
        at.text_input[0].set_value("nuevo_usuario")
        at.text_input[1].set_value("clave")
        at.text_input[2].set_value("https://docs.google.com/spreadsheets/d/AAA/edit")
        at.button[0].click().run()
        cfg = user_config.cargar()
        self.assertEqual((cfg["tp_usuario"], cfg["tp_password"]), ("nuevo_usuario", "clave"))
        self.assertEqual(cfg["sheet_urls"]["substitute_doble"], "https://docs.google.com/spreadsheets/d/AAA/edit")


if __name__ == "__main__":
    unittest.main()
