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


def estado_falso(**kw):
    """Estado de la corrida (lo que guarda la app en st.session_state['_corrida']) con valores neutros."""
    e = {"running": False, "finished": False, "log_lines": [], "returncode": None, "proc": None, "stop_file": None, "run_dir": None,
         "abort_pedido": False, "modo": "lectura", "inicio": 0, "mensaje": None, "conteo": None, "conteo_error": None, "aviso_cerrado": False}
    e.update(kw)
    return e


@unittest.skipIf(AppTest is None, "falta streamlit")
class App(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.guardado = (user_config.CONFIG_DIR, user_config.CONFIG_PATH)
        user_config.CONFIG_DIR = self.tmp
        user_config.CONFIG_PATH = os.path.join(self.tmp, "config.json")

    def tearDown(self):
        user_config.CONFIG_DIR, user_config.CONFIG_PATH = self.guardado

    def app(self, estado=None):
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        if estado is not None:
            at.session_state["_corrida"] = estado
        return at.run()

    def configurar(self, sheet="https://docs.google.com/spreadsheets/d/XYZ/edit"):
        user_config.guardar({"tp_usuario": "daiana", "tp_password": "secreta", "headless": False,
                             "sheet_urls": {"substitute_doble": sheet}})

    def boton(self, at, clave):
        return next(b for b in at.button if b.key == clave)

    def elegir(self, at, clave, valor):
        """Elige una opción de un control segmentado (AppTest no lo maneja directo: se fija el valor de su clave y se vuelve a correr)."""
        at.session_state[clave] = valor
        return at.run()

    def textos(self, at):
        return " ".join(m.value for m in at.markdown)

    @staticmethod
    def _aplanar(bloque):
        for hijo in bloque.children.values():
            yield hijo
            if hasattr(hijo, "children"):
                yield from App._aplanar(hijo)

    def test_orden_de_la_pantalla(self):
        self.configurar()
        at = self.app()
        self.assertFalse(at.exception)
        tipos = [(e.type, getattr(e, "label", None) or getattr(e, "value", None)) for e in self._aplanar(at.main)]
        pos = lambda tipo, texto: next(i for i, (t, v) in enumerate(tipos) if t == tipo and texto in str(v))
        self.assertLess(pos("markdown", "Actualización de costos"), pos("button_group", "Vista"))                 # las pestañas, bajo el encabezado
        self.assertLess(pos("button_group", "Vista"), pos("markdown", "Cola de trabajo"))
        self.assertLess(pos("markdown", "Cola de trabajo"), pos("button_group", "Combinaciones a procesar"))     # los parámetros, bajo la cola
        self.assertLess(pos("button_group", "Modo"), pos("markdown", 'class="sd-consola"'))                       # y la consola, bajo los parámetros
        self.assertLess(pos("markdown", 'class="sd-consola"'), pos("expander", "Historial de corridas"))

    def test_selectores_segmentados_con_ayuda_que_cambia_y_siempre_una_opcion(self):
        self.configurar()
        at = self.app()
        grupos = {g.key: g for g in at.get("button_group")}
        self.assertEqual([o.content for o in grupos["modo"].proto.options], ["Solo revisar", "Aplicar cambios"])
        self.assertEqual([o.content for o in grupos["tipo_reservas"].proto.options], ["FITS", "GRUPOS", "AMBOS"])
        self.assertEqual([o.content for o in grupos["origen"].proto.options][1], "Reporte nuevo")
        self.assertTrue(all(g.proto.required for g in grupos.values() if g.key in ("origen", "modo", "tipo_reservas")))   # nunca sin opción
        self.assertIn("Lectura. No modifica nada en Tourplan.", self.textos(at))
        self.assertIn("Procesar lo que ya está cargado", self.textos(at))
        self.elegir(at, "modo", "aplicar")
        self.assertIn("Modifica reservas reales en Tourplan.", self.textos(at))
        self.assertNotIn("Lectura. No modifica nada en Tourplan.", self.textos(at))
        self.elegir(at, "origen", "reporte")
        self.assertIn("Subir el Service Status y sumar combinaciones", self.textos(at))

    def test_la_opcion_pendientes_muestra_la_cantidad_real(self):
        self.configurar()
        filas = [{"ESTADO": "PENDIENTE", "TIPO": "FITS"}, {"ESTADO": "PENDIENTE", "TIPO": "FITS"}]
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        with mock.patch("common.sheets_client.conectar_sheets", return_value=object()), \
             mock.patch("common.sheets_client.cargar_sheet", return_value=(filas, ["ESTADO", "TIPO"])):
            at.run()
        grupos = {g.key: g for g in at.get("button_group")}
        self.assertEqual(grupos["origen"].proto.options[0].content, "Pendientes (2)")

    def test_ejecutar_solo_se_habilita_con_los_obligatorios_y_sin_reporte_nuevo_a_medias(self):
        self.configurar()
        filas = [{"ESTADO": "PENDIENTE", "TIPO": "FITS"}]
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        with mock.patch("common.sheets_client.conectar_sheets", return_value=object()), \
             mock.patch("common.sheets_client.cargar_sheet", return_value=(filas, ["ESTADO", "TIPO"])):
            at.run()
            self.assertFalse(self.boton(at, "ejecutar_revision").disabled)
            self.assertEqual(self.boton(at, "ejecutar_revision").label, "Ejecutar")
            self.elegir(at, "origen", "reporte")                                                   # elegir «Reporte nuevo» bloquea Ejecutar
            self.assertTrue(self.boton(at, "ejecutar_revision").disabled)
            self.assertIn("Para ejecutar falta: terminar la carga del reporte nuevo.", self.textos(at))
            self.elegir(at, "origen", "pendientes")
            self.assertFalse(self.boton(at, "ejecutar_revision").disabled)

    def test_el_texto_de_ayuda_usa_el_singular_y_el_plural(self):
        self.configurar()
        for n, esperado in ((1, "<b>1 combinación</b>"), (3, "<b>3 combinaciones</b>")):
            filas = [{"ESTADO": "PENDIENTE", "TIPO": "FITS"}] * n
            at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
            with mock.patch("common.sheets_client.conectar_sheets", return_value=object()), \
                 mock.patch("common.sheets_client.cargar_sheet", return_value=(filas, ["ESTADO", "TIPO"])):
                at.run()
            self.assertIn(f"Va a leer {esperado}. No modifica nada.", self.textos(at))
            self.assertIn("Ejecutar", self.boton(at, "ejecutar_revision").label)

    def test_las_vistas_son_pestanas_de_texto_y_la_inactiva_se_oculta(self):
        self.configurar()
        at = self.app()
        vista = next(g for g in at.get("button_group") if g.key == "paso")
        self.assertEqual([o.content for o in vista.proto.options], ["Ejecutar corrida", "Comparar reportes"])
        self.assertEqual(at.session_state["paso"], "run")
        self.assertIn(".st-key-panel_cmp { display: none; }", self.textos(at))                        # Comparar está oculto
        self.assertIn("No hace falta haber ejecutado antes: podés subir los dos reportes a mano.", self.textos(at))
        self.elegir(at, "paso", "cmp")
        self.assertIn(".st-key-panel_run { display: none; }", self.textos(at))
        self.assertIn("ejecutar_revision", [b.key for b in at.button])                               # sigue dibujado (no pierde lo cargado)
        self.assertNotIn("ir_comparar_manual", [b.key for b in at.button])                            # ya no hay botones sueltos para cambiar de vista
        self.assertNotIn("volver_ejecutar", [b.key for b in at.button])

    def test_cambiar_de_vista_con_una_corrida_en_curso_no_la_toca(self):
        self.configurar()
        run_dir = Path(tempfile.mkdtemp())
        estado = estado_falso(running=True, modo="aplicar", run_dir=run_dir, stop_file=run_dir / "ABORTAR.flag",
                              log_lines=["[10:00:00] Filas PENDIENTE a procesar: 3\n", "[10:00:01] Fila 2: BUE/A/GU/X\n"])
        at = self.app(estado)
        antes = self.textos(at)
        self.assertIn("APLICANDO", antes)
        self.elegir(at, "paso", "cmp")
        self.assertFalse(at.exception)
        estado_despues = at.session_state["_corrida"]
        self.assertTrue(estado_despues["running"])                                                    # sigue corriendo
        self.assertEqual(estado_despues["log_lines"], estado["log_lines"])                            # la consola no se pierde
        self.assertIn("APLICANDO", self.textos(at))
        self.assertIn("Combinación 1 de 3", self.textos(at))
        self.elegir(at, "paso", "run")
        self.assertIn("Fila 2: BUE/A/GU/X", self.textos(at))

    def test_sin_rango_de_fechas_ni_fila_ni_avanzado(self):
        self.configurar()
        at = self.app()
        self.assertEqual([d.key for d in at.date_input], [])
        self.assertEqual([n.key for n in at.number_input], [])
        self.assertNotIn("base_url", [t.key for t in at.text_input])
        self.assertFalse(any(e.label == "Avanzado" for e in at.expander))

    def test_ejecutar_lanza_el_script_con_el_entorno_correcto(self):
        user_config.guardar({"tp_usuario": "daiana", "tp_password": "secreta", "headless": False, "tp_base_url": "https://prueba.example/tp",
                             "sheet_urls": {"substitute_doble": "https://docs.google.com/spreadsheets/d/XYZ/edit"}})
        at = self.app()
        self.assertIn("Tourplan: <b>https://prueba.example/tp</b>", self.textos(at))                        # un ambiente distinto de producción se nota arriba
        with mock.patch("subprocess.Popen", ProcFalso):
            at = self.boton(at, "ejecutar_revision").click().run()
        self.assertFalse(at.exception)
        args, kw = ProcFalso.llamada
        self.assertTrue(args[0][1].endswith(os.path.join("scripts", "substitute_doble", "substitute_doble.py")))
        env = kw["env"]
        self.assertEqual((env["TOURPLAN_USERNAME"], env["TOURPLAN_PASSWORD"]), ("daiana", "secreta"))
        self.assertEqual((env["TOURPLAN_MODO"], env["TOURPLAN_FILA"], env["TOURPLAN_BASE_URL"]), ("lectura", "", "https://prueba.example/tp"))
        self.assertEqual(env["TOURPLAN_FECHA_HASTA"], "31/12/49")
        self.assertRegex(env["TOURPLAN_FECHA_DESDE"], r"^\d\d/\d\d/\d\d$")
        self.assertEqual(env["TOURPLAN_SHEET_URL"], "https://docs.google.com/spreadsheets/d/XYZ/edit")
        self.assertEqual(env["TOURPLAN_PUDO_SERVICE_TYPES"], "")                                       # la app ya no pasa Service Types
        self.assertEqual(kw["stdin"], __import__("subprocess").DEVNULL)                                # nunca queda esperando una consola
        self.assertTrue(Path(env["TOURPLAN_CONFIRM_DIR"]).is_dir())

    def test_la_url_de_tourplan_por_defecto_es_produccion(self):
        self.configurar()
        at = self.app()
        self.assertIn("Tourplan: <b>producción</b>", self.textos(at))

    def test_abortar_esta_al_lado_de_refrescar_y_no_en_la_tarjeta(self):
        self.configurar()
        at = self.app()
        claves = [b.key for b in at.button]
        self.assertIn("abortar_cola", claves)
        self.assertNotIn("abortar_tarjeta", claves)
        self.assertLess(claves.index("refrescar"), claves.index("abortar_cola"))                     # Abortar a la derecha de Refrescar
        self.assertTrue(self.boton(at, "abortar_cola").disabled)                                      # sin corrida no se puede abortar

    def test_la_confirmacion_de_aplicar_no_se_borra_al_ejecutar_sino_al_terminar(self):
        self.configurar()
        at = self.app()
        self.elegir(at, "modo", "aplicar")
        at.checkbox(key="confirmo").check().run()
        self.assertTrue(at.session_state["confirmo"])
        # al terminar la corrida se vuelve a pedir
        at.session_state["_corrida"] = estado_falso(running=True, finished=True, log_lines=["x\n"], returncode=0, modo="aplicar")
        with mock.patch("common.sheets_client.conectar_sheets", side_effect=RuntimeError("sin red en la prueba")):
            at.run()
        self.assertFalse(at.session_state["confirmo"])

    def test_barra_pegada_y_consola_240(self):
        self.configurar()
        at = self.app()
        css = next(m.value for m in at.markdown if "stAppDeployButton" in m.value)
        self.assertIn("padding-top: 0 !important", css)
        self.assertIn("position: sticky", css)
        self.assertIn("height: 240px", css)
        self.assertTrue(any(c.key == "barra_corrida" for c in self._aplanar(at.main)) or "barra_corrida" in css)

    def test_boton_descargar_arriba_de_la_consola(self):
        self.configurar()
        at = self.app()
        self.assertFalse(at.exception)
        tipos = [(e.type, getattr(e, "label", None) or getattr(e, "value", None)) for e in self._aplanar(at.main)]
        pos_boton = next(i for i, (t, v) in enumerate(tipos) if t == "download_button" and v == "Descargar log")
        pos_consola = next(i for i, (t, v) in enumerate(tipos) if t == "markdown" and 'class="sd-consola"' in str(v))
        self.assertLess(pos_boton, pos_consola)

    def test_consola_escapada_y_con_la_ultima_linea_primero(self):
        self.configurar()
        at = self.app(estado_falso(log_lines=["primera\n", "<b>negrita</b> & más\n", "ULTIMA\n"]))
        self.assertFalse(at.exception)
        consola = next(m.value for m in at.markdown if 'class="sd-consola"' in m.value)
        self.assertNotIn("<b>negrita</b>", consola)
        self.assertIn("&lt;b&gt;negrita&lt;/b&gt; &amp; más", consola)
        self.assertLess(consola.index("ULTIMA"), consola.index("primera"))                           # column-reverse: la última queda visible abajo
        self.assertNotIn("\n", consola)                                                               # una sola línea: Markdown no la parte
        css = next(m.value for m in at.markdown if "column-reverse" in m.value)
        self.assertIn("overflow-y: auto", css)

    def test_al_terminar_refresca_la_cola_y_muestra_el_aviso(self):
        self.configurar()
        filas = [{"ESTADO": "PENDIENTE"}, {"ESTADO": "PENDIENTE"}, {"ESTADO": "OK"}, {"ESTADO": "ERROR_COLGADAS"}]
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        at.session_state["_corrida"] = estado_falso(running=True, finished=True, log_lines=["x\n"], returncode=0, run_dir=Path(tempfile.mkdtemp()),
                                                    modo="aplicar")
        with mock.patch("common.sheets_client.conectar_sheets", return_value=object()), \
             mock.patch("common.sheets_client.cargar_sheet", return_value=(filas, ["ESTADO"])):
            at.run()
        self.assertFalse(at.exception)
        cola = next(m.value for m in at.markdown if 'class="sd-cola"' in m.value)
        for esperado in ('<span class="n">2</span><span class="l">Pendiente', '<span class="n">1</span><span class="l">OK',
                         'class="mal"><span class="n">1</span><span class="l">Error'):
            self.assertIn(esperado, cola)
        self.assertIn("Terminó bien", self.textos(at))
        self.assertIn("ir_comparar", [b.key for b in at.button])                                       # tras aplicar, sugiere comparar

    def test_mientras_corre_barra_cola_y_botones(self):
        self.configurar()
        filas = [{"ESTADO": "PENDIENTE"}, {"ESTADO": "OK"}, {"ESTADO": "OK"}]
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        at.session_state["_corrida"] = estado_falso(
            running=True, modo="aplicar", run_dir=Path(tempfile.mkdtemp()), conteo={"Pendiente": 3, "OK": 0, "Error": 0, "Duplicado": 0, "Otro": 0},
            log_lines=["[10:00:00] Filas PENDIENTE a procesar: 3\n", "[10:00:01] Fila 2: BUE/A/GU/X\n", "[10:01:00] Fila 3: BUE/A/GU/Y\n"])
        with mock.patch("common.sheets_client.conectar_sheets", return_value=object()), \
             mock.patch("common.sheets_client.cargar_sheet", return_value=(filas, ["ESTADO"])):
            at.run()
        self.assertFalse(at.exception)
        cola = next(m.value for m in at.markdown if 'class="sd-cola"' in m.value)
        self.assertIn('<span class="n">2</span><span class="l">OK', cola)                              # leyó la hoja sin tocar Refrescar
        barra = self.textos(at)
        self.assertIn("APLICANDO", barra)
        self.assertIn("Combinación 2 de 3", barra)
        self.assertTrue(self.boton(at, "refrescar").disabled)
        self.assertTrue(self.boton(at, "ejecutar_revision").disabled)
        self.assertFalse(self.boton(at, "abortar_barra").disabled)
        self.assertTrue(all(g.proto.disabled for g in at.get("button_group") if g.key in ("origen", "modo", "tipo_reservas")))   # los parámetros quedan bloqueados

    def test_cargar_desde_reporte_esta_en_el_paso_ejecutar(self):
        self.configurar()
        at = self.app()
        self.assertEqual(at.session_state["origen"], "pendientes")
        self.assertNotIn("analizar_reporte", [b.key for b in at.button])                               # solo aparece al elegir «reporte nuevo»
        self.elegir(at, "origen", "reporte")
        self.assertFalse(at.exception)
        self.assertTrue(self.boton(at, "analizar_reporte").disabled)                                   # sin archivo no se puede analizar
        self.assertNotIn("confirmar_carga", [b.key for b in at.button])                                # y nada se carga sin confirmar

    def test_la_cola_se_lee_sola_al_abrir_la_pagina(self):
        self.configurar()
        filas = [{"ESTADO": "PENDIENTE", "TIPO": "FITS"}, {"ESTADO": "PENDIENTE", "TIPO": "FITS"}, {"ESTADO": "OK", "TIPO": "FITS"}]
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        with mock.patch("common.sheets_client.conectar_sheets", return_value=object()), \
             mock.patch("common.sheets_client.cargar_sheet", return_value=(filas, ["ESTADO", "TIPO"])):
            at.run()
        self.assertFalse(at.exception)
        cola = next(m.value for m in at.markdown if 'class="sd-cola"' in m.value)
        self.assertIn('<span class="n">2</span><span class="l">Pendiente', cola)                       # sin tocar Refrescar
        self.assertIn("Combinaciones pendientes en el Sheet (FITS): <b>2</b>", self.textos(at))

    def test_tipo_de_reservas_filtra_los_pendientes_y_avisa_de_las_filas_sin_tipo(self):
        self.configurar()
        filas = [{"ESTADO": "PENDIENTE", "TIPO": "FITS"}, {"ESTADO": "PENDIENTE", "TIPO": "GRUPOS"}, {"ESTADO": "PENDIENTE", "TIPO": "GRUPOS"},
                 {"ESTADO": "PENDIENTE", "TIPO": ""}]
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        with mock.patch("common.sheets_client.conectar_sheets", return_value=object()), \
             mock.patch("common.sheets_client.cargar_sheet", return_value=(filas, ["ESTADO", "TIPO"])):
            at.run()
            self.assertEqual(at.session_state["tipo_reservas"], "FITS")                              # por defecto, el más seguro
            self.assertIn("Combinaciones pendientes en el Sheet (FITS): <b>1</b>", self.textos(at))
            self.assertIn("1 fila(s) PENDIENTE sin TIPO", " ".join(c.value for c in at.caption))
            self.elegir(at, "tipo_reservas", "GRUPOS")
            self.assertIn("Combinaciones pendientes en el Sheet (GRUPOS): <b>2</b>", self.textos(at))
            self.elegir(at, "tipo_reservas", "AMBOS")
            self.assertIn("Combinaciones pendientes en el Sheet (AMBOS): <b>4</b>", self.textos(at))        # incluye la fila sin TIPO
            self.assertNotIn("sin TIPO", " ".join(c.value for c in at.caption))
        self.assertFalse(self.boton(at, "ejecutar_revision").disabled)

    def test_el_tipo_de_reservas_viaja_al_script(self):
        self.configurar()
        at = self.app()
        self.elegir(at, "tipo_reservas", "GRUPOS")
        with mock.patch("subprocess.Popen", ProcFalso):
            self.boton(at, "ejecutar_revision").click().run()
        self.assertEqual(ProcFalso.llamada[1]["env"]["TOURPLAN_TIPO_RESERVAS"], "GRUPOS")

    def test_la_cola_avisa_cuantas_se_suman_al_confirmar_el_reporte(self):
        self.configurar()
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        at.session_state["_plan_reporte"] = {"a_cargar": [{}, {}, {}], "descartadas": [], "resumen": {}, "conteo": {}}
        with mock.patch("common.sheets_client.conectar_sheets", return_value=object()), \
             mock.patch("common.sheets_client.cargar_sheet", return_value=([{"ESTADO": "PENDIENTE"}], ["ESTADO"])):
            at.run()
        self.assertIn("+3 al confirmar la carga del reporte", self.textos(at))

    def test_sin_configurar_no_se_puede_ejecutar(self):
        at = self.app()
        self.assertFalse(at.exception)
        self.assertIn("Falta configurar el usuario de Tourplan", self.textos(at))
        self.assertTrue(self.boton(at, "ejecutar_revision").disabled)
        self.assertIn("configurar el usuario y el Sheet", self.textos(at))

    def test_con_configuracion_se_puede_ejecutar_en_lectura(self):
        self.configurar()
        at = self.app()
        self.assertFalse(at.exception)
        self.assertIn("Usuario: <b>daiana</b>", self.textos(at))
        self.assertFalse(self.boton(at, "ejecutar_revision").disabled)
        self.assertEqual(at.session_state["modo"], "lectura")
        self.assertTrue(self.boton(at, "abortar_cola").disabled)                                      # Abortar deshabilitado sin corrida
        self.assertNotIn("rep_pre", [u.key for u in at.get("file_uploader")])                           # el PRE se pide solo en Aplicar
        self.assertNotIn("pudo_types", [t.key for t in at.text_input])                                  # Service Types ya no se configura en pantalla

    def test_aplicar_exige_pre_y_confirmacion(self):
        self.configurar()
        at = self.app()
        self.elegir(at, "modo", "aplicar")
        self.assertFalse(at.exception)
        self.assertIn("MODO APLICAR", self.textos(at))
        self.assertIn("rep_pre", [u.key for u in at.get("file_uploader")])
        self.assertIn("confirmo", [c.key for c in at.checkbox])
        self.assertNotIn("confirmar_ok", [c.key for c in at.checkbox])
        self.assertNotIn("pudo_types", [t.key for t in at.text_input])
        self.assertTrue(self.boton(at, "ejecutar_aplicar").disabled)
        self.assertIn("Para ejecutar falta: adjuntar el reporte PRE · confirmar el modo Aplicar.", self.textos(at))
        at.checkbox(key="confirmo").check().run()
        self.assertIn("Para ejecutar falta: adjuntar el reporte PRE.", self.textos(at))                # la confirmación ya no falta, el PRE sí
        self.assertTrue(self.boton(at, "ejecutar_aplicar").disabled)
        self.elegir(at, "modo", "lectura")
        self.assertFalse(self.boton(at, "ejecutar_revision").disabled)

    def test_la_url_del_sheet_solo_se_pide_en_configuracion(self):
        user_config.guardar({"tp_usuario": "u", "tp_password": "p", "headless": False, "sheet_urls": {}})
        at = self.app()
        self.assertFalse(at.exception)
        self.assertNotIn("sheet_url", [t.key for t in at.text_input])
        self.assertTrue(self.boton(at, "ejecutar_revision").disabled)                                  # sin URL guardada no se puede ejecutar
        self.assertIn("Falta la URL del Sheet", self.textos(at))
        user_config.guardar({"tp_usuario": "u", "tp_password": "p", "headless": False,
                             "sheet_urls": {"substitute_doble": "https://docs.google.com/spreadsheets/d/Z/edit"}})
        at = self.app()
        self.assertFalse(self.boton(at, "ejecutar_revision").disabled)

    def test_botones_de_confirmacion(self):
        self.configurar()
        run_dir = Path(tempfile.mkdtemp())
        (run_dir / E.ARCHIVO_PENDIENTE).write_text("IDA: mover 1 reserva de BUE/1ACOS1/GU/700GUI", encoding="utf-8")
        at = self.app(estado_falso(running=True, log_lines=["hola\n"], stop_file=run_dir / "ABORTAR.flag", run_dir=run_dir, modo="aplicar"))
        self.assertFalse(at.exception)
        self.assertTrue(any("espera tu confirmación" in w.value for w in at.warning))
        self.assertTrue(self.boton(at, "ejecutar_revision").disabled)                                  # mientras corre no se puede lanzar otra
        self.assertFalse(self.boton(at, "abortar_barra").disabled)
        self.boton(at, "conf_si").click().run()
        self.assertEqual((run_dir / E.ARCHIVO_RESPUESTA).read_text(encoding="utf-8"), "SI")
        (run_dir / E.ARCHIVO_RESPUESTA).unlink()
        self.boton(at, "conf_no").click().run()
        self.assertEqual((run_dir / E.ARCHIVO_RESPUESTA).read_text(encoding="utf-8"), "NO")

    def test_abortar_crea_la_bandera(self):
        self.configurar()
        run_dir = Path(tempfile.mkdtemp())
        at = self.app(estado_falso(running=True, stop_file=run_dir / "ABORTAR.flag", run_dir=run_dir))
        self.boton(at, "abortar_barra").click().run()
        self.assertTrue((run_dir / "ABORTAR.flag").exists())

    def test_mensajes_finales_segun_el_codigo_de_salida(self):
        self.configurar()
        for rc, texto, clase in ((0, "Terminó bien", "ok"), (E.EXIT_ABORTADO, "Abortada", "warn"),
                                 (E.EXIT_DETENIDA, "Corrida detenida", "mal"), (1, "código 1", "mal")):
            at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
            at.session_state["_corrida"] = estado_falso(running=True, finished=True, log_lines=["x\n"], returncode=rc, modo="aplicar")
            with mock.patch("common.sheets_client.conectar_sheets", side_effect=RuntimeError("sin red en la prueba")):
                at.run()
            self.assertFalse(at.exception, rc)
            aviso = next((m.value for m in at.markdown if 'class="sd-run' in m.value and texto in m.value), None)
            self.assertIsNotNone(aviso, (rc, texto))
            self.assertIn(f'sd-run {clase}"', aviso)
            self.assertEqual("ir_comparar" in [b.key for b in at.button], rc == 0)                    # «Ir a Comparar» solo tras aplicar sin problemas

    def test_sin_usuario_el_dialogo_de_configuracion_se_abre_solo(self):
        at = self.app()
        self.assertEqual(at.session_state["_config_abierta_sola"], True)                                # sin usuario, el diálogo se abre solo (una vez)

    def test_historial_lista_corridas_con_descarga_del_log(self):
        self.configurar()
        base = Path(RAIZ) / "salida"
        base.mkdir(exist_ok=True)
        carpetas = []
        try:
            for nombre, texto in (("aplicar_20990101-101010", "[10:10:10] Filas PENDIENTE a procesar: 3\n[10:10:10] 🏁 Fin. Duración: 3m 05s · filas OK=2 ERROR=1\n"),
                                  ("lectura_20990101-090000", "[09:00:00] arrancó y se cortó\n")):
                carpeta = base / nombre
                carpeta.mkdir(exist_ok=True)
                (carpeta / "consola.log").write_text(texto, encoding="utf-8")
                carpetas.append(carpeta)
            (base / "diag_ignorada").mkdir(exist_ok=True)       # no es una corrida: no se lista
            carpetas.append(base / "diag_ignorada")
            at = self.app()
            self.assertFalse(at.exception)
            historial = next(e for e in at.expander if e.label.startswith("🕘 Historial de corridas"))
            textos = " ".join(m.value for m in historial.markdown) + " " + " ".join(str(w.value) for w in historial.markdown) + " ".join(
                str(getattr(c, "value", "")) for c in self._aplanar(historial))
            self.assertIn("01/01/2099 10:10:10", textos)
            self.assertIn("01/01/2099 09:00:00", textos)
            self.assertIn("APLICAR", textos)
            self.assertIn("Con errores (1)", textos)                                                   # 1 de las 3 combinaciones dio error
            self.assertIn("3 de 3", textos)
            self.assertIn("Sin cierre", textos)                                                        # la que se cortó
            self.assertLess(textos.index("01/01/2099 10:10:10"), textos.index("01/01/2099 09:00:00"))   # la más nueva primero
            self.assertNotIn("diag_ignorada", textos)
            self.assertGreaterEqual(len([b for b in at.get("download_button") if b.proto.label == "Descargar"]), 2)   # una por corrida
        finally:
            for c in carpetas:
                for f in c.glob("*"):
                    f.unlink()
                c.rmdir()


if __name__ == "__main__":
    unittest.main()
