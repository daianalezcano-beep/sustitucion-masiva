"""Prueba de app.py renderizándola con el marco de pruebas de Streamlit (AppTest): sin Tourplan ni Sheets.
Se reemplaza subprocess.Popen para ver con qué entorno se lanzaría el script. Se omite si falta streamlit."""
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
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
         "abort_pedido": False, "modo": "lectura", "inicio": 0, "mensaje": None, "conteo": None, "conteo_error": None, "aviso_cerrado": False,
         "rango": (datetime(2026, 12, 1), datetime(2026, 12, 31))}          # el Sheet «tiene» fechas cargadas (es un requisito)
    e.update(kw)
    return e


@unittest.skipIf(AppTest is None, "falta streamlit")
class App(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.guardado = (user_config.CONFIG_DIR, user_config.CONFIG_PATH)
        user_config.CONFIG_DIR = self.tmp
        user_config.CONFIG_PATH = os.path.join(self.tmp, "config.json")
        # Por defecto el Sheet «tiene» un rango de fechas cargado (es un requisito) y escribir las fechas no hace nada; los tests de fechas lo cambian.
        self.rango_sheet = mock.patch("common.parametros_sheet.leer_del_sheet", return_value=(datetime(2026, 12, 1), datetime(2026, 12, 31)))
        self.escribe_sheet = mock.patch("common.parametros_sheet.escribir_en_el_sheet")
        self.rango_sheet.start()
        self.mock_escribe = self.escribe_sheet.start()
        self.addCleanup(self.rango_sheet.stop)
        self.addCleanup(self.escribe_sheet.stop)

    def tearDown(self):
        user_config.CONFIG_DIR, user_config.CONFIG_PATH = self.guardado

    def app(self, estado=None):
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        at.session_state["_corrida"] = estado if estado is not None else estado_falso()
        return at.run()

    def configurar(self, sheet="https://docs.google.com/spreadsheets/d/XYZ/edit", separar=True):
        """Configuración de la prueba. `separar`: «Separar por tipo» encendido (los tests del tipo de reservas) o apagado (el valor por defecto de la app)."""
        user_config.guardar({"tp_usuario": "daiana", "tp_password": "secreta", "headless": False, "avisos": False, "separar_tipo": separar,
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
        self.assertLess(pos("button_group", "Vista"), pos("button_group", "Combinaciones a procesar"))
        self.assertLess(pos("button_group", "Combinaciones a procesar"), pos("markdown", "Cola de trabajo"))     # la cola va en la barra lateral derecha
        self.assertLess(pos("button_group", "Modo"), pos("markdown", 'class="sd-consola"'))                       # y la consola, bajo los parámetros

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
            self.assertIn("🔒 Reporte sin cargar", self.textos(at))
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
            self.assertIn(f"Va a leer {esperado} y no modifica nada.", self.textos(at))
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
        self.assertEqual((env["TOURPLAN_FECHA_DESDE"], env["TOURPLAN_FECHA_HASTA"]), ("01/12/26", "31/12/26"))   # las del Sheet (PARAMETROS)
        self.assertEqual(env["TOURPLAN_SHEET_URL"], "https://docs.google.com/spreadsheets/d/XYZ/edit")
        self.assertEqual(env["TOURPLAN_PUDO_SERVICE_TYPES"], "")                                       # la app ya no pasa Service Types
        self.assertEqual(kw["stdin"], __import__("subprocess").DEVNULL)                                # nunca queda esperando una consola
        self.assertTrue(Path(env["TOURPLAN_CONFIRM_DIR"]).is_dir())

    def test_la_url_de_tourplan_por_defecto_es_produccion(self):
        self.configurar()
        at = self.app()
        self.assertIn("Tourplan: <b>producción</b>", self.textos(at))

    def test_abortar_solo_esta_en_la_barra_de_progreso(self):
        self.configurar()
        at = self.app()
        claves = [b.key for b in at.button]
        self.assertNotIn("abortar_cola", claves)                                                       # ni en la barra lateral
        self.assertNotIn("abortar_franja", claves)                                                     # ni en la franja
        self.assertNotIn("abortar_tarjeta", claves)
        run_dir = Path(tempfile.mkdtemp())
        at = self.app(estado_falso(running=True, run_dir=run_dir, stop_file=run_dir / "ABORTAR.flag", modo="aplicar", foto=dict(self.FOTO)))
        self.assertEqual([b.key for b in at.button if b.key and b.key.startswith("abortar")], ["abortar_barra"])

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
                         'class="mal" title=', '<span class="n">1</span><span class="l">Error'):
            self.assertIn(esperado, cola)
        self.assertIn("title=\"Procesadas: el costo se actualizó", cola)                              # cada contador explica qué significa (tooltip)
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
        self.assertNotIn("ejecutar_revision", [b.key for b in at.button])                              # los parámetros se reemplazan por la franja resumen
        self.assertFalse(self.boton(at, "abortar_barra").disabled)
        self.assertIn("Los parámetros ya están completos: se ocultan mientras corre.", self.textos(at))
        self.assertFalse([g for g in at.get("button_group") if g.key in ("origen", "modo", "tipo_reservas")])

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
        at.session_state["_req_detalle"] = True                                          # detalle de requisitos desplegado
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
        at.session_state["_req_detalle"] = True                                          # detalle de requisitos desplegado
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

    def test_boton_de_guia_y_glosarios(self):
        self.configurar()
        at = self.app()
        self.assertIn("btn_guia", [b.key for b in at.button])
        self.assertIn("¿Qué significa cada estado?", [e.label for e in at.expander])                                # en la cola (barra lateral)
        self.assertIn("¿Qué significa cada resultado?", [p.proto.popover.label for p in at.get("popover")])        # en el comparador
        texto = self.textos(at)
        self.assertIn("ERROR_COLGADAS", texto)
        self.assertIn("Venta modificada", texto)
        self.assertIn("Product Analysis exportado después de la corrida", " ".join(c.value for c in at.caption))

    def test_tiempo_restante_estimado_en_la_barra(self):
        self.configurar()
        run_dir = Path(tempfile.mkdtemp())
        log = ["[10:00:00] Filas PENDIENTE a procesar: 10\n"] + [f"[10:00:0{i}] Fila {i}: BUE/A/GU/X\n" for i in range(1, 6)]
        at = self.app(estado_falso(running=True, modo="aplicar", run_dir=run_dir, stop_file=run_dir / "ABORTAR.flag", log_lines=log[:3],
                                   t_primera=time.time() - 60))
        self.assertIn("calculando el tiempo restante", self.textos(at))                              # con menos de 3 terminadas todavía no se estima
        at = self.app(estado_falso(running=True, modo="aplicar", run_dir=run_dir, stop_file=run_dir / "ABORTAR.flag", log_lines=log,
                                   t_primera=time.time() - 120))
        self.assertIn("quedan ~", self.textos(at))                                                    # 4 terminadas en 2 min: faltan unos minutos

    def test_al_terminar_avisa_si_esta_activado(self):
        for activado, veces in ((True, 1), (False, 0)):
            user_config.guardar({"tp_usuario": "daiana", "tp_password": "secreta", "headless": False, "avisos": activado,
                                 "sheet_urls": {"substitute_doble": "https://docs.google.com/spreadsheets/d/XYZ/edit"}})
            at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
            at.session_state["_corrida"] = estado_falso(running=True, finished=True, log_lines=["x\n"], returncode=0, modo="aplicar")
            with mock.patch("common.avisos.avisar") as avisar, \
                 mock.patch("common.sheets_client.conectar_sheets", side_effect=RuntimeError("sin red en la prueba")):
                at.run()
            self.assertEqual(avisar.call_count, veces, activado)

    def test_las_fechas_salen_de_la_pestana_parametros_del_sheet(self):
        self.configurar()

        class Hoja:
            title = "PARAMETROS"

            def get_all_values(self):
                return [["DESDE", "01/12/2026"], ["HASTA", "31/12/2026"]]

        class WSFalsa:
            spreadsheet = type("L", (), {"worksheets": lambda self: [Hoja()]})()

        filas = [{"ESTADO": "PENDIENTE", "TIPO": "FITS"}]
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        at.session_state["_req_detalle"] = True                                          # detalle de requisitos desplegado
        with mock.patch("common.sheets_client.conectar_sheets", return_value=WSFalsa()), \
             mock.patch("common.sheets_client.cargar_sheet", return_value=(filas, ["ESTADO", "TIPO"])), \
             mock.patch("subprocess.Popen", ProcFalso):
            at.run()
            self.assertIn("Fechas de los servicios: <b>01/12/2026 – 31/12/2026</b>", self.textos(at))
            self.boton(at, "ejecutar_revision").click().run()
        env = ProcFalso.llamada[1]["env"]
        self.assertEqual((env["TOURPLAN_FECHA_DESDE"], env["TOURPLAN_FECHA_HASTA"]), ("01/12/26", "31/12/26"))

    def test_sin_fechas_en_el_sheet_el_requisito_queda_pendiente(self):
        self.configurar()
        with mock.patch("common.parametros_sheet.leer_del_sheet", return_value=(None, None)):
            at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
            at.session_state["_req_detalle"] = True                                      # detalle de requisitos desplegado
            with mock.patch("common.sheets_client.conectar_sheets", return_value=object()), \
                 mock.patch("common.sheets_client.cargar_sheet", return_value=([{"ESTADO": "PENDIENTE", "TIPO": "FITS"}], ["ESTADO", "TIPO"])):
                at.run()
        self.assertIn("Fechas de los servicios: sin definir. Completá DESDE y HASTA en la pestaña PARAMETROS", self.textos(at))
        self.assertIn("🔒 Fechas", self.textos(at))
        self.assertTrue(self.boton(at, "ejecutar_revision").disabled)                    # ya no se ejecuta «de mañana en adelante» sin querer

    def test_al_abrir_la_app_se_vacian_las_fechas_de_la_corrida_anterior(self):
        self.configurar()
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        with mock.patch("common.sheets_client.conectar_sheets", return_value=object()), \
             mock.patch("common.sheets_client.cargar_sheet", return_value=([], ["ESTADO", "TIPO"])):
            at.run()
            at.run()                                                                     # un segundo rerun de la misma sesión no vuelve a borrar
        self.mock_escribe.assert_called_once()
        self.assertEqual(self.mock_escribe.call_args[0][1:], (None, None))

    def _aplicar_con_lista(self, excl_info, eleccion=None):
        self.configurar()
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        at.session_state["_req_detalle"] = True                                                       # detalle de requisitos desplegado
        at.session_state["_corrida"] = estado_falso(excl_info=excl_info, conteo={"Pendiente": 1, "OK": 0, "Error": 0, "Duplicado": 0, "Otro": 0},
                                                    pend_tipo={"FITS": 1, "GRUPOS": 0, "PROMO": 0, "": 0})
        at.session_state["modo"] = "aplicar"
        if eleccion:
            at.session_state["costos_manualizados"] = eleccion
        return at.run()

    def test_costos_manualizados_hay_o_no_hay_sin_eleccion_inicial(self):
        at = self._aplicar_con_lista((3, 2, None, 0))
        self.assertIn("costos_manualizados", [g.key for g in at.get("button_group")])
        self.assertIsNone(at.session_state["costos_manualizados"])                                    # sin opción elegida: el requisito queda pendiente
        self.assertIn("Costos manualizados: indicá «Hay» o «No hay»", self.textos(at))
        self.assertIn("🔒 Costos manualizados", self.textos(at))
        self.assertNotIn("sin_manuales", [c.key for c in at.checkbox])                                # ya no hay casillas
        self.assertNotIn("lista_actualizada", [c.key for c in at.checkbox])

    def test_hay_refleja_la_cantidad_de_lineas_de_la_pestana(self):
        at = self._aplicar_con_lista((3, 2, None, 0), "hay")
        self.assertIn("Costos manualizados: <b>hay lista en EXCLUSIONES</b> (3 líneas en 2 combinaciones)", self.textos(at))
        self.assertIn("En el Sheet: <b>3</b> línea(s) en <b>2</b> combinación(es)", self.textos(at))

    def test_hay_con_la_pestana_vacia_no_cumple_el_requisito(self):
        at = self._aplicar_con_lista(None, "hay")
        self.assertIn("la pestaña EXCLUSIONES está vacía", self.textos(at) + " ".join(w.value for w in at.warning))
        self.assertIn("🔒 Costos manualizados", self.textos(at))

    def test_no_hay_cumple_y_el_script_ignora_la_lista(self):
        at = self._aplicar_con_lista((3, 2, None, 0), "no_hay")
        self.assertIn("Costos manualizados: <b>no hay</b>", self.textos(at))
        self.assertIn("con «No hay» se ignoran", " ".join(w.value for w in at.warning))                # avisa que la pestaña tiene líneas
        from common import ejecucion as E
        p = {"usuario": "u", "password": "p", "sheet_url": "x", "base_url": "https://t", "modo": "aplicar", "desde": datetime(2026, 11, 3).date(),
             "hasta": datetime(2049, 12, 31).date(), "fila": 0, "sin_exclusiones": True}
        self.assertEqual(E.armar_entorno(p, {}, "/r", "/r/run", "/r/s", "/r/stop", "/c", "/t")["TOURPLAN_SIN_EXCLUSIONES"], "1")
        self.assertEqual(E.armar_entorno(dict(p, sin_exclusiones=False), {}, "/r", "/r/run", "/r/s", "/r/stop", "/c", "/t")["TOURPLAN_SIN_EXCLUSIONES"], "0")

    def test_filas_ilegibles_de_la_lista_se_avisan(self):
        at = self._aplicar_con_lista((4, 3, None, 1), "hay")
        self.assertIn("1 fila(s) de la pestaña EXCLUSIONES no se pueden leer", " ".join(w.value for w in at.warning))

    def test_el_resumen_de_excluidas_aparece_al_terminar(self):
        self.configurar()
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        log = ["x\n", "[10:00:00] 📋 Excluidas por costo manualizado (revisar a mano): 2 línea(s)\n"]
        at.session_state["_corrida"] = estado_falso(running=True, finished=True, log_lines=log, returncode=0, modo="aplicar")
        with mock.patch("common.sheets_client.conectar_sheets", side_effect=RuntimeError("sin red en la prueba")):
            at.run()
        self.assertIn("2 línea(s) excluida(s) por costo manualizado", self.textos(at))

    def test_al_terminar_se_muestra_el_resumen_con_que_hacer(self):
        self.configurar()
        log = ["Filas PENDIENTE a procesar: 3\n", "Fila 2: BUE/1ACOS1/GU/AEEZID\n", "  ✅ OK: 8 reserva(s)\n",
               "Fila 3: BUE/1ACOS1/GU/AEHTID\n", "  ⚠ ERROR_COLGADAS: Falló después del OK de la ida\n",
               "🛑 CORRIDA DETENIDA: x\n", "🏁 Fin. Duración: 3m 05s · filas OK=1 ERROR=1 · reservas leídas=8\n"]
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        at.session_state["_corrida"] = estado_falso(running=True, finished=True, log_lines=log, returncode=E.EXIT_DETENIDA, modo="aplicar")
        with mock.patch("common.sheets_client.conectar_sheets", side_effect=RuntimeError("sin red en la prueba")):
            at.run()
        t = self.textos(at)
        self.assertIn("Resultado de la corrida", t)
        self.assertIn("Fila 3: BUE/1ACOS1/GU/AEHTID", t)
        self.assertIn("¿Hay riesgo?", t)
        self.assertIn("1 combinación(es) quedaron sin procesar", t)

    def test_la_vista_previa_aparece_antes_de_ejecutar(self):
        self.configurar()
        filas = [{"ESTADO": "PENDIENTE", "TIPO": "FITS"}] * 2
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        with mock.patch("common.sheets_client.conectar_sheets", return_value=object()), \
             mock.patch("common.sheets_client.cargar_sheet", return_value=(filas, ["ESTADO", "TIPO"])):
            at.run()
        self.assertIn("Esto es lo que va a pasar", self.textos(at))

    def test_costos_manualizados_solo_en_aplicar_y_se_bloquea_al_correr(self):
        self.configurar()
        at = self.app()
        self.assertNotIn("costos_manualizados", [g.key for g in at.get("button_group")])               # en revisión no se pide
        self.assertNotIn("rep_excl", [u.key for u in at.get("file_uploader")])                          # la lista se pega en el Sheet: no se adjunta
        self.elegir(at, "modo", "aplicar")
        self.assertIn("costos_manualizados", [g.key for g in at.get("button_group")])
        run_dir = Path(tempfile.mkdtemp())
        at = self.app(estado_falso(running=True, run_dir=run_dir, stop_file=run_dir / "ABORTAR.flag"))
        self.assertNotIn("costos_manualizados", [g.key for g in at.get("button_group")])               # mientras corre no se toca

    def test_por_defecto_no_se_separa_por_tipo_y_se_procesa_todo(self):
        self.configurar(separar=False)
        filas = [{"ESTADO": "PENDIENTE", "TIPO": ""}, {"ESTADO": "PENDIENTE", "TIPO": ""}, {"ESTADO": "PENDIENTE", "TIPO": "GRUPOS"}]
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        at.session_state["_req_detalle"] = True                                          # detalle de requisitos desplegado
        with mock.patch("common.sheets_client.conectar_sheets", return_value=object()), \
             mock.patch("common.sheets_client.cargar_sheet", return_value=(filas, ["ESTADO", "TIPO"])), \
             mock.patch("subprocess.Popen", ProcFalso):
            at.run()
            self.assertEqual([g.key for g in at.get("button_group") if g.key in ("origen", "modo", "tipo_reservas")], ["origen", "modo"])   # sin selector de tipo
            self.assertEqual(at.session_state["tipo_reservas"], "AMBOS")
            self.assertIn("Combinaciones pendientes en el Sheet: <b>3</b>", self.textos(at))          # cuenta todas, sin el tipo entre paréntesis
            self.assertNotIn("sin TIPO", " ".join(c.value for c in at.caption))
            self.boton(at, "ejecutar_revision").click().run()
        env = ProcFalso.llamada[1]["env"]
        self.assertEqual((env["TOURPLAN_TIPO_RESERVAS"], env["TOURPLAN_SEPARAR_TIPO"]), ("AMBOS", "0"))
        self.assertFalse(any(r.key == "tipo_comparacion" for r in at.radio))                          # tampoco en el comparador

    def test_con_el_interruptor_encendido_el_tipo_viaja_al_script_como_separado(self):
        self.configurar(separar=True)
        at = self.app()
        with mock.patch("subprocess.Popen", ProcFalso):
            self.boton(at, "ejecutar_revision").click().run()
        self.assertEqual(ProcFalso.llamada[1]["env"]["TOURPLAN_SEPARAR_TIPO"], "1")

    def test_sin_configurar_no_se_puede_ejecutar(self):
        at = self.app()
        at.session_state["_req_detalle"] = True; at.run()
        self.assertFalse(at.exception)
        self.assertIn("Falta configurar el usuario de Tourplan", self.textos(at))
        self.assertTrue(self.boton(at, "ejecutar_revision").disabled)
        self.assertIn("🔒 Configuración", self.textos(at))

    def test_con_configuracion_se_puede_ejecutar_en_lectura(self):
        self.configurar()
        at = self.app()
        self.assertFalse(at.exception)
        self.assertIn("Usuario: <b>daiana</b>", self.textos(at))
        self.assertFalse(self.boton(at, "ejecutar_revision").disabled)
        self.assertEqual(at.session_state["modo"], "lectura")
        self.assertNotIn("rep_pre", [u.key for u in at.get("file_uploader")])                           # el PRE se pide solo en Aplicar
        self.assertNotIn("pudo_types", [t.key for t in at.text_input])                                  # Service Types ya no se configura en pantalla

    FOTO = {"modo": "aplicar", "pend": 3, "desde": datetime(2026, 12, 1).date(), "hasta": datetime(2026, 12, 31).date(), "pre": "pre_x.csv", "usuario": "daiana",
            "tipo_reservas": "AMBOS"}

    def test_franja_resumen_mientras_corre_con_los_datos_de_la_corrida(self):
        self.configurar()
        run_dir = Path(tempfile.mkdtemp())
        at = self.app(estado_falso(running=True, run_dir=run_dir, stop_file=run_dir / "ABORTAR.flag", modo="aplicar", foto=dict(self.FOTO)))
        t = self.textos(at)
        self.assertIn("3 combinaciones · Fechas 01/12/2026 – 31/12/2026 · PRE pre_x.csv · daiana", t)
        self.assertNotIn("Requisitos", t)                                                              # sin requisitos en rojo mientras corre
        self.assertIn('class="sd-consola grande"', t)                                                  # consola más alta con los parámetros plegados
        self.boton(at, "abortar_barra").click().run()
        self.assertTrue(self.boton(at, "abortar_barra").disabled)

    def _terminada(self, pend_tipo, **kw):
        self.configurar()
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        at.session_state["_corrida"] = estado_falso(mensaje=("success", "Terminó bien."), modo="aplicar", returncode=0, foto=dict(self.FOTO),
                                                    conteo={"Pendiente": 0, "OK": 3, "Error": 0, "Duplicado": 0, "Otro": 0}, pend_tipo=pend_tipo, **kw)
        with mock.patch("common.sheets_client.conectar_sheets", side_effect=RuntimeError("sin red en la prueba")):
            return at.run()

    def test_al_terminar_sin_pendientes_ofrece_cargar_reporte_o_refrescar(self):
        at = self._terminada({"FITS": 0, "GRUPOS": 0, "PROMO": 0, "": 0})
        t = self.textos(at)
        self.assertIn("No quedan combinaciones pendientes.", t)
        self.assertNotIn("Requisitos", t)                                                              # no reaparecen los requisitos en rojo
        claves = [b.key for b in at.button]
        self.assertIn("cargar_reporte_nuevo", claves)
        self.assertIn("refrescar_estado", claves)
        self.boton(at, "cargar_reporte_nuevo").click().run()
        self.assertEqual(at.session_state["origen"], "reporte")
        self.assertEqual(at.session_state["modo"], "aplicar")                                          # se restaura el modo
        self.assertIn("analizar_reporte", [b.key for b in at.button])                                  # tarjeta completa

    def test_al_terminar_con_pendientes_cuenta_en_singular_y_plural(self):
        for n, texto in ((1, "Queda 1 combinación pendiente."), (4, "Quedan 4 combinaciones pendientes.")):
            at = self._terminada({"FITS": 0, "GRUPOS": 0, "PROMO": 0, "": 0, "AMBOS": 0} | {"FITS": n})
            self.assertIn(texto, self.textos(at))
            self.assertNotIn("1 combinaciones", self.textos(at))
            self.assertIn("preparar_nueva", [b.key for b in at.button])

    def test_preparar_nueva_corrida_limpia_confirmacion_costos_y_pre(self):
        at = self._terminada({"FITS": 2, "GRUPOS": 0, "PROMO": 0, "": 0})
        at.session_state["confirmo"] = True
        at.session_state["costos_manualizados"] = "no_hay"
        self.boton(at, "preparar_nueva").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["modo"], "aplicar")
        self.assertFalse(at.session_state["confirmo"])
        self.assertIsNone(at.session_state["costos_manualizados"])
        self.assertIn("ejecutar_aplicar", [b.key for b in at.button])                                  # tarjeta completa, Ejecutar bloqueado hasta cumplir los requisitos
        self.assertTrue(self.boton(at, "ejecutar_aplicar").disabled)
        self.assertEqual(at.session_state["_pre_n"], 1)                                                # el cargador del PRE arranca vacío (clave nueva)

    def test_boton_abrir_sheet_en_la_barra_lateral(self):
        self.configurar("https://docs.google.com/spreadsheets/d/XYZ/edit")
        at = self.app()
        botones = [b for b in at.get("link_button") if b.key == "abrir_sheet"]
        self.assertEqual(len(botones), 1)
        self.assertEqual(botones[0].proto.url, "https://docs.google.com/spreadsheets/d/XYZ/edit")
        self.assertFalse(botones[0].proto.disabled)
        at.session_state["lateral"] = "historial"
        at.run()
        self.assertEqual(len([b for b in at.get("link_button") if b.key == "abrir_sheet"]), 1)      # sigue en la pestaña Historial

    def test_requisitos_compactos_y_detalle_plegable(self):
        self.configurar()
        filas = [{"ESTADO": "PENDIENTE", "TIPO": "FITS"}]
        at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
        with mock.patch("common.sheets_client.conectar_sheets", return_value=object()), \
             mock.patch("common.sheets_client.cargar_sheet", return_value=(filas, ["ESTADO", "TIPO"])):
            at.run()
            t = self.textos(at)
            self.assertIn("✔ Todo listo", t)
            self.assertIn("Requisitos <b>3 de 3</b>", t)
            self.assertNotIn("Usuario de Tourplan:", t)                                                 # el detalle está plegado
            self.boton(at, "ver_detalle_req").click().run()
            self.assertIn("Usuario de Tourplan:", self.textos(at))
            self.assertEqual(self.boton(at, "ver_detalle_req").label, "Ocultar detalle")

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
        t = self.textos(at)
        for pastilla in ("🔒 Falta el PRE", "🔒 Costos manualizados", "🔒 Confirmación"):
            self.assertIn(pastilla, t)
        self.assertNotIn("Para ejecutar falta", t)                                                      # ya no hay mensaje rojo duplicado bajo el botón
        self.assertIn("Requisitos <b>3 de 6</b>", t)
        at.checkbox(key="confirmo").check().run()
        self.elegir(at, "costos_manualizados", "no_hay")
        t = self.textos(at)
        self.assertIn("🔒 Falta el PRE", t)                                                             # confirmación y costos manualizados listos, el PRE no
        self.assertNotIn("🔒 Confirmación", t)
        self.assertIn("Requisitos <b>5 de 6</b>", t)
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
        self.assertNotIn("ejecutar_revision", [b.key for b in at.button])                              # mientras corre no se puede lanzar otra
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
            self.assertNotIn("**", aviso)                                                              # sin asteriscos sueltos (la negrita se dibuja como <b>)
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
            at = AppTest.from_file(os.path.join(RAIZ, "app.py"), default_timeout=30)
            at.session_state["lateral"] = "historial"                                                  # pestaña «Historial» de la barra lateral
            at.run()
            self.assertFalse(at.exception)
            textos = " ".join(m.value for m in at.markdown)
            self.assertNotIn("🕘 Historial de corridas", [e.label for e in at.expander])               # ya no es un expander al final de la página
            self.assertIn("01/01 10:10", textos)
            self.assertIn("01/01 09:00", textos)
            self.assertIn("APLICAR", textos)
            self.assertIn("Con 1 a revisar", textos)                                                   # 1 de las 3 combinaciones dio error
            self.assertIn("3 de 3", textos)
            self.assertIn("Sin cierre", textos)                                                        # la que se cortó
            self.assertLess(textos.index("01/01 10:10"), textos.index("01/01 09:00"))                  # la más nueva primero
            self.assertNotIn("diag_ignorada", textos)
            self.assertGreaterEqual(len([b for b in at.get("download_button") if str(b.proto.id) and b.key and b.key.startswith("hist_")]), 2)   # una por corrida
        finally:
            for c in carpetas:
                for f in c.glob("*"):
                    f.unlink()
                c.rmdir()


if __name__ == "__main__":
    unittest.main()
