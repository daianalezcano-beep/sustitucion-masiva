"""Prueba por simulación del control de flujo de procesar_fila_aplicar (ida y vuelta).

No usa Tourplan ni Sheets: un "mundo" en memoria hace de Tourplan (original A y provisorio P) y
se reemplazan las funciones que tocan el navegador. Verifica el ORDEN de las acciones, la escritura
del detalle ANTES del OK de la ida, y qué estado final queda ante cada tipo de falla.

Correr:  python -m unittest discover -s tests -v   (necesita selenium instalado para importar el script)
"""
import os
import sys
import unittest
from datetime import datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "scripts", "substitute_doble"))

try:
    import substitute_doble as sd
    from common import ejecucion as E
    from common import logica as L
except ModuleNotFoundError as e:           # selenium / gspread sin instalar
    sd = None
    FALTA = str(e)


def fila(ref, status="IT", supplier="1ACOS1", code="700GUI", pudo="Pickup: 01/Nov/2026 00:00 Dropoff: 01/Nov/2026 00:00"):
    return {"location": "BUE", "service": "GU", "supplier": supplier, "code": code, "description": "Guía",
            "booking_name": "n", "booking_ref": ref, "travel_date": "01/Nov/2026", "date": "01/Nov/2026",
            "status": status, "pudo_raw": pudo, "pickup_fecha_hora": "01/Nov/2026 00:00", "pickup_texto": "",
            "dropoff_fecha_hora": "01/Nov/2026 00:00", "dropoff_texto": ""}


PANEL = {"pickup_fecha": "01/Nov/2026", "pickup_hora": "09:30", "pickup_texto": "Hotel Sheraton",
         "dropoff_fecha": "01/Nov/2026", "dropoff_hora": "00:00", "dropoff_texto": "", "remarks": "Silla de ruedas"}


class HojaDetalle:
    """Hoja SUBSTITUTE_DETALLE falsa: guarda filas y registra cada cambio de FASE."""

    def __init__(self):
        self.filas, self.updates = [], []

    def append_rows(self, valores, **kw):
        ini = len(self.filas) + 2
        self.filas.extend(list(v) for v in valores)
        return {"updates": {"updatedRange": f"'DET'!A{ini}:S{ini + len(valores) - 1}"}}

    def update(self, range_name=None, values=None):
        self.updates.append((range_name, values[0][0]))


class Mundo:
    """Tourplan simulado."""

    def __init__(self, filas, ignorar=0, panel=None, falla_en=None, cancela_en=None, vuelta_cambia_estado=False,
                 vuelta_deja_una_en_P=False):
        self.A, self.P = [dict(f) for f in filas], []
        self.ignorar, self.falla_en, self.cancela_en = ignorar, falla_en, cancela_en
        self.vuelta_cambia_estado = vuelta_cambia_estado
        self.vuelta_deja_una_en_P = vuelta_deja_una_en_P
        self.panel = panel if panel is not None else PANEL
        self.eventos, self.modal = [], None


@unittest.skipIf(sd is None, "falta una dependencia para importar el script")
class FlujoAplicar(unittest.TestCase):
    def preparar(self, mundo):
        self.m = mundo
        self.ws_det = HojaDetalle()
        self.ctx = {"corrida": "c1", "desde": datetime(2026, 11, 1), "hasta": datetime(2049, 12, 31),
                    "ws_det": self.ws_det, "cols_det": list(sd.COLS_DET), "provisorios_ok": set()}
        m = mundo

        def consultar(driver, ctx, loc, stype, sup, cod, d, h, leer=True, marca=None):
            m.eventos.append(f"consultar {'P' if sup == 'GUIAPO' else 'A'}")
            filas = m.P if sup == "GUIAPO" else m.A
            return {"found": len(filas), "filas": [dict(f) for f in filas] if leer else [], "modo": "completa", "desc": "d"}

        def seleccionar_todo(driver, found):
            m.eventos.append(f"seleccionar_todo {found}")
            return ("label", None)

        def abrir_modal(driver, found):
            m.eventos.append("abrir_modal")

        def llenar_modal(driver, loc, stype, sup, cod, status, pudo, etiqueta):
            m.eventos.append(f"llenar_modal {etiqueta} destino={sup}/{cod} status={status} pudo={'sí' if pudo else 'no'}")
            m.modal = (etiqueta, pudo)

        def confirmar(texto):
            m.eventos.append("confirmar")
            return m.cancela_en is None

        def ok_summary(driver, etiqueta):
            m.eventos.append(f"OK {etiqueta}")
            if m.falla_en == etiqueta:
                raise sd.ErrorFila("ERROR: summary", "no apareció el summary")
            if etiqueta == "ida":
                mover = m.A[m.ignorar:]
                m.A, m.P = m.A[:m.ignorar], [dict(f, supplier="GUIAPO", code="000000") for f in mover]
            else:
                for f in m.P:
                    f.update(supplier="1ACOS1", code="700GUI")
                    if m.vuelta_cambia_estado:
                        f["status"] = "OT"
                if m.vuelta_deja_una_en_P:
                    m.A, m.P = m.A + m.P[:-1], m.P[-1:]
                else:
                    m.A, m.P = m.A + m.P, []
            return f"summary {etiqueta}"

        def leer_panel(driver, filas):
            m.eventos.append("leer_panel")
            return [dict(m.panel, _ref=f["booking_ref"]) for f in filas]

        sd.consultar, sd.seleccionar_todo, sd.abrir_modal_reemplazo = consultar, seleccionar_todo, abrir_modal
        sd.llenar_modal, sd.confirmar, sd.ok_modal_y_summary, sd.leer_panel_filas = llenar_modal, confirmar, ok_summary, leer_panel
        sd.jc = lambda d, e: m.eventos.append("EXIT del modal")
        sd.dialog_repl = lambda d: type("D", (), {"find_element": lambda self, *a: None})()

    def correr(self, mundo):
        self.preparar(mundo)
        row = {"LOCATION": "BUE", "SUPPLIER": "1ACOS1", "SERVICE TYPE": "GU", "CODIGO": "700GUI"}
        return sd.procesar_fila_aplicar(None, self.ctx, row)

    # ── casos ────────────────────────────────────────────────────────────────
    def test_camino_feliz(self):
        estado, obs, found = self.correr(Mundo([fila("R1"), fila("R2")]))
        self.assertEqual((estado, found), ("OK", 2))
        self.assertIn("idéntico", obs)
        ev = self.m.eventos
        # la guarda del provisorio va primero y una sola vez; la foto inicial lee el panel antes de la ida
        self.assertEqual(ev[0], "consultar P")
        self.assertLess(ev.index("leer_panel"), ev.index("OK ida"))
        # UNA sola confirmación por combinación (antes de la ida; la vuelta va sola) y el detalle se escribe ANTES del OK de la ida
        self.assertEqual(ev.count("confirmar"), 1)
        self.assertLess(ev.index("confirmar"), ev.index("OK ida"))
        self.assertLess(ev.index("OK ida"), ev.index("OK vuelta"))
        # P se consulta 2 veces (guarda inicial + tras la ida): tras la vuelta no, porque A quedó idéntico a la foto
        self.assertEqual(ev.count("consultar P"), 2)
        self.assertEqual(len(self.ws_det.filas), 2)                 # una fila de detalle por reserva
        self.assertEqual([u[1] for u in self.ws_det.updates][:2], ["IDA", "VUELTA"])
        self.assertEqual([u[1] for u in self.ws_det.updates][-2:], ["VERIFICADO", "VERIFICADO"])
        # la ida ya carga pickup/drop-off/remarks (prueba los selectores antes de mover nada); la vuelta los repone
        self.assertIn("llenar_modal ida destino=GUIAPO/000000 status=IT pudo=sí", ev)
        self.assertIn("llenar_modal vuelta destino=1ACOS1/700GUI status=IT pudo=sí", ev)
        self.assertEqual((self.m.P, len(self.m.A)), ([], 2))
        self.assertIn(("1ACOS1", "700GUI"), {(f["supplier"], f["code"]) for f in self.m.A})

    def test_sin_datos_de_pickup_no_se_repone(self):
        vacio = {k: "" for k in L.CAMPOS_PANEL}
        estado, _, _ = self.correr(Mundo([fila("R1")], panel=dict(vacio, pickup_hora="00:00", dropoff_hora="00:00")))
        self.assertEqual(estado, "OK")
        self.assertIn("llenar_modal ida destino=GUIAPO/000000 status=IT pudo=no", self.m.eventos)
        self.assertIn("llenar_modal vuelta destino=1ACOS1/700GUI status=IT pudo=no", self.m.eventos)

    def test_found_cero_no_toca_nada(self):
        estado, obs, found = self.correr(Mundo([]))
        self.assertEqual((estado, found), ("OK", 0))
        self.assertNotIn("OK ida", self.m.eventos)
        self.assertEqual(self.ws_det.filas, [])

    def test_tourplan_ignora_algunas(self):
        estado, obs, _ = self.correr(Mundo([fila("R1"), fila("R2"), fila("R3")], ignorar=1))
        self.assertEqual(estado, "OK")
        self.assertIn("no sustituidas 1", obs)
        fases = [u[1] for u in self.ws_det.updates[-3:]]
        self.assertEqual(sorted(fases), ["NO_SUSTITUIDA", "VERIFICADO", "VERIFICADO"])
        self.assertEqual(len(self.m.A), 3)

    def test_tourplan_no_sustituye_ninguna(self):
        estado, obs, _ = self.correr(Mundo([fila("R1"), fila("R2")], ignorar=2))
        self.assertEqual(estado, "ERROR_REVISAR")
        self.assertNotIn("OK vuelta", self.m.eventos)           # sin nada en P no hay vuelta
        self.assertEqual(self.ws_det.updates[-1][1], "NO_SUSTITUIDA")

    def test_rechazos_previos_no_tocan_nada(self):
        casos = [
            ([fila("R1", "IT"), fila("R2", "OT")], "único estado"),                 # dos estados
            ([fila(f"R{i}") for i in range(sd.PANEL_MAX + 1)], "TOURPLAN_PANEL_MAX"),  # demasiadas reservas
        ]
        for filas, texto in casos:
            with self.assertRaises(sd.ErrorFila) as cm:
                self.correr(Mundo(filas))
            self.assertEqual(cm.exception.estado, "ERROR: no soportado en esta fase")
            self.assertIn(texto, cm.exception.detalle)
            self.assertNotIn("OK ida", self.m.eventos)
            self.assertEqual(self.ws_det.filas, [])

    def test_paneles_distintos_se_rechazan_antes_de_la_ida(self):
        m = Mundo([fila("R1"), fila("R2")])
        self.preparar(m)
        sd.leer_panel_filas = lambda d, filas: [dict(PANEL, _ref="R1"), dict(PANEL, _ref="R2", remarks="otro")]
        with self.assertRaises(sd.ErrorFila) as cm:
            sd.procesar_fila_aplicar(None, self.ctx, {"LOCATION": "BUE", "SUPPLIER": "1ACOS1", "SERVICE TYPE": "GU", "CODIGO": "700GUI"})
        self.assertIn("distintos datos de pickup", cm.exception.detalle)
        self.assertNotIn("OK ida", m.eventos)

    def test_service_type_sin_provisorio(self):
        self.preparar(Mundo([fila("R1")]))
        with self.assertRaises(sd.ErrorFila) as cm:
            sd.procesar_fila_aplicar(None, self.ctx, {"LOCATION": "BUE", "SUPPLIER": "X", "SERVICE TYPE": "HT", "CODIGO": "C"})
        self.assertEqual(cm.exception.estado, "ERROR: sin provisorio")
        self.assertEqual(self.m.eventos, [])                    # ni siquiera abrió Tourplan

    def test_provisorio_en_uso_frena_antes_de_todo(self):
        m = Mundo([fila("R1")])
        m.P = [fila("RX", supplier="GUIAPO", code="000000")]
        with self.assertRaises(sd.ErrorFila) as cm:
            self.correr(m)
        self.assertEqual(cm.exception.estado, "ERROR: provisorio en uso")
        self.assertNotIn("OK ida", m.eventos)
        self.assertNotIn("leer_panel", m.eventos)

    def test_guarda_del_provisorio_una_vez_por_corrida(self):
        self.correr(Mundo([fila("R1")]))
        primero = self.m.eventos.count("consultar P")
        self.m.A = [fila("R9")]
        self.m.eventos.clear()
        sd.procesar_fila_aplicar(None, self.ctx, {"LOCATION": "BUE", "SUPPLIER": "1ACOS1", "SERVICE TYPE": "GU", "CODIGO": "700GUI"})
        self.assertEqual(primero, 2)       # guarda + verificación de P tras la ida (sin comprobación final: A quedó idéntico)
        self.assertEqual(self.m.eventos.count("consultar P"), 1)   # la guarda ya no se repite

    def test_cancelar_en_la_ida_no_mueve_nada(self):
        with self.assertRaises(sd.ErrorFila) as cm:
            self.correr(Mundo([fila("R1")], cancela_en=True))
        self.assertEqual(cm.exception.estado, "ERROR: cancelado")
        self.assertNotIn("OK ida", self.m.eventos)
        self.assertIn("EXIT del modal", self.m.eventos)
        self.assertEqual(self.ws_det.filas, [])                 # el detalle se escribe recién antes del OK

    def test_la_vuelta_no_pide_confirmacion(self):
        self.correr(Mundo([fila("R1")]))
        self.assertEqual(self.m.eventos.count("confirmar"), 1)
        self.assertLess(self.m.eventos.index("OK ida"), self.m.eventos.index("llenar_modal vuelta destino=1ACOS1/700GUI status=IT pudo=sí"))

    def test_falla_en_el_summary_de_la_ida_es_colgada(self):
        with self.assertRaises(sd.ErrorFila) as cm:
            self.correr(Mundo([fila("R1")], falla_en="ida"))
        self.assertEqual(cm.exception.estado, "ERROR_COLGADAS")
        self.assertEqual(len(self.ws_det.filas), 1)             # el detalle ya estaba escrito (IDA_EN_CURSO)

    def test_falla_en_el_summary_de_la_vuelta_es_colgada(self):
        with self.assertRaises(sd.ErrorFila) as cm:
            self.correr(Mundo([fila("R1")], falla_en="vuelta"))
        self.assertEqual(cm.exception.estado, "ERROR_COLGADAS")

    def test_excepcion_inesperada_despues_de_la_ida_es_colgada(self):
        m = Mundo([fila("R1")])
        self.preparar(m)
        original = sd.ok_modal_y_summary
        sd.ok_modal_y_summary = lambda d, e: (_ for _ in ()).throw(KeyError("boom")) if e == "vuelta" else original(d, e)
        with self.assertRaises(sd.ErrorFila) as cm:
            sd.procesar_fila_aplicar(None, self.ctx, {"LOCATION": "BUE", "SUPPLIER": "1ACOS1", "SERVICE TYPE": "GU", "CODIGO": "700GUI"})
        self.assertEqual(cm.exception.estado, "ERROR_COLGADAS")
        self.assertIn("KeyError", cm.exception.detalle)

    def test_provisorio_con_restos_tras_la_vuelta_es_colgada(self):
        with self.assertRaises(sd.ErrorFila) as cm:
            self.correr(Mundo([fila("R1"), fila("R2")], vuelta_deja_una_en_P=True))
        self.assertEqual(cm.exception.estado, "ERROR_COLGADAS")        # NO "REVISAR": hay un sustituto sin asignar
        self.assertIn("quedó con 1 reserva", cm.exception.detalle)

    def test_diferencias_tras_la_vuelta_se_marcan_para_revision(self):
        estado, obs, _ = self.correr(Mundo([fila("R1")], vuelta_cambia_estado=True))
        self.assertEqual(estado, "ERROR_REVISAR")
        self.assertIn("DIFERENCIAS", obs)
        self.assertEqual(self.ws_det.updates[-1][1], "REVISION_MANUAL")

    def test_pickup_que_no_se_repone_se_detecta_en_la_verificacion_final(self):
        m = Mundo([fila("R1")])
        self.preparar(m)
        llamadas = []
        sd.leer_panel_filas = lambda d, filas: (llamadas.append(1), [dict(PANEL if len(llamadas) == 1 else dict(PANEL, pickup_texto=""),
                                                                           _ref="R1")])[1]
        estado, obs, _ = sd.procesar_fila_aplicar(None, self.ctx, {"LOCATION": "BUE", "SUPPLIER": "1ACOS1", "SERVICE TYPE": "GU", "CODIGO": "700GUI"})
        self.assertEqual(estado, "ERROR_REVISAR")
        self.assertIn("panel distinto", obs)


@unittest.skipIf(sd is None, "falta una dependencia para importar el script")
class SinPendientes(unittest.TestCase):
    def test_exige_confirmacion_y_detalle_limpio(self):
        import types
        ws = types.SimpleNamespace()
        sd.cargar_sheet = lambda w: ([{"FASE": "IDA", "LOCATION": "BUE", "SUPPLIER": "S", "SERVICE TYPE": "GU", "CODIGO": "C",
                                      "CORRIDA": "c0"}], list(sd.COLS_DET))
        with self.assertRaises(SystemExit) as cm:
            sd.exigir_sin_pendientes(ws)
        self.assertIn("a mitad de camino", str(cm.exception))
        sd.cargar_sheet = lambda w: ([{"FASE": "VERIFICADO"}], list(sd.COLS_DET))
        sd.CONFIRMO_SIN_PENDIENTES = ""
        with self.assertRaises(SystemExit) as cm:
            sd.exigir_sin_pendientes(ws)
        self.assertIn("TOURPLAN_CONFIRMO_SIN_PENDIENTES=SI", str(cm.exception))
        sd.CONFIRMO_SIN_PENDIENTES = "SI"
        sd.exigir_sin_pendientes(ws)         # no levanta


@unittest.skipIf(sd is None, "falta una dependencia para importar el script")
class HoraEnCadaLinea(unittest.TestCase):
    def test_marca_cada_linea_no_las_vacias_y_no_duplica(self):
        import io
        import re
        destino = io.StringIO()
        w = sd._ConHora(sd._ConHora(destino))      # instalada dos veces: la hora sale una sola vez
        w.write("primera\n\nsegunda ")
        w.write("sigue\ntercera\n")
        lineas = destino.getvalue().split("\n")
        self.assertRegex(lineas[0], r"^\[\d\d:\d\d:\d\d\] primera$")
        self.assertEqual(lineas[1], "")
        self.assertRegex(lineas[2], r"^\[\d\d:\d\d:\d\d\] segunda sigue$")   # una línea escrita en dos partes: una sola hora
        self.assertEqual(len(re.findall(r"\[\d\d:\d\d:\d\d\]", destino.getvalue())), 3)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(sd is None, "falta una dependencia para importar el script")
class MainOrquestador(unittest.TestCase):
    """main() con todo lo externo reemplazado (navegador, login, Sheets)."""

    def setUp(self):
        import tempfile
        self.escrituras, self.llamadas = [], []
        self.guardado = {k: getattr(sd, k) for k in ("MODO", "SHEET_URL", "FECHA_DESDE", "FECHA_HASTA", "FILA", "CONFIRMO_SIN_PENDIENTES",
                                                       "cargar_hojas", "actualizar_fila_sheet", "procesar_fila_lectura",
                                                       "procesar_fila_aplicar", "chequear_abort", "exigir_sin_pendientes")}
        self.tpg = {k: getattr(sd.tp, k) for k in ("crear_driver", "login", "logout", "preparar_carpeta_corrida", "USERNAME", "PASSWORD")}
        sd.SHEET_URL, sd.FECHA_DESDE, sd.FECHA_HASTA, sd.FILA, sd.MODO = "x", "01/11/26", "31/12/49", "", "lectura"
        sd.tp.USERNAME, sd.tp.PASSWORD = "u", "p"
        sd.tp.preparar_carpeta_corrida = lambda prefijo: (tempfile.mkdtemp(), "c1")
        driver = type("D", (), {"quit": lambda s: self.llamadas.append("quit")})()
        sd.tp.crear_driver = lambda: (self.llamadas.append("crear_driver"), driver)[1]
        sd.tp.login = lambda d: self.llamadas.append("login")
        sd.tp.logout = lambda d: self.llamadas.append("logout")
        sd.actualizar_fila_sheet = lambda ws, idx, cols, vals: self.escrituras.append((idx, vals))
        filas = [{"__row_idx__": 2, "LOCATION": "BUE", "SUPPLIER": "S1", "SERVICE TYPE": "GU", "CODIGO": "C1", "ESTADO": "PENDIENTE"},
                 {"__row_idx__": 3, "LOCATION": "bue", "SUPPLIER": "s1", "SERVICE TYPE": "gu", "CODIGO": "c1", "ESTADO": "PENDIENTE"},   # repetida
                 {"__row_idx__": 4, "LOCATION": "FTE", "SUPPLIER": "S2", "SERVICE TYPE": "GU", "CODIGO": "C2", "ESTADO": "PENDIENTE"},
                 {"__row_idx__": 5, "LOCATION": "FTE", "SUPPLIER": "S2", "SERVICE TYPE": "GU", "CODIGO": "C3", "ESTADO": "OK"}]
        sd.cargar_hojas = lambda: (object(), filas, sd.COLS_INPUT + ["OBSERVACIONES", "TIMESTAMP"], None, None)
        sd.procesar_fila_lectura = lambda d, ctx, row: (f"obs {row['CODIGO']}", [], 3, {"abrir": 1})
        sd.procesar_fila_aplicar = lambda d, ctx, row: ("OK", f"aplicada {row['CODIGO']}", 3)
        sd.exigir_sin_pendientes = lambda ws_det: None

    def tearDown(self):
        for k, v in self.guardado.items():
            setattr(sd, k, v)
        for k, v in self.tpg.items():
            setattr(sd.tp, k, v)

    def test_lectura_procesa_solo_pendientes_unicas_y_marca_duplicados(self):
        sd.main()
        estados = {idx: v.get("ESTADO") for idx, v in self.escrituras}
        self.assertEqual(estados[3], "DUPLICADO")                       # la repetida
        self.assertEqual([i for i, v in self.escrituras if "obs" in str(v.get("OBSERVACIONES"))], [2, 4])
        self.assertTrue(all(v.get("ESTADO") is None for i, v in self.escrituras if i in (2, 4)))   # siguen PENDIENTE
        self.assertNotIn(5, [i for i, _ in self.escrituras])            # la que ya estaba OK no se toca
        self.assertEqual(self.llamadas, ["crear_driver", "login", "logout", "quit"])

    def test_si_el_login_falla_no_se_hace_logout(self):
        def login_roto(d):
            raise AssertionError("Login falló")
        sd.tp.login = login_roto
        with self.assertRaises(AssertionError):
            sd.main()
        self.assertNotIn("logout", self.llamadas)
        self.assertIn("quit", self.llamadas)

    def test_abort_corta_entre_filas_hace_logout_y_sale_con_75(self):
        n = []
        sd.chequear_abort = lambda: (n.append(1), (_ for _ in ()).throw(sd.AbortadoPorUsuario()) if len(n) == 2 else None)[1]
        with self.assertRaises(SystemExit) as cm:
            sd.main()
        self.assertEqual(cm.exception.code, sd.ABORT_EXIT_CODE)
        self.assertEqual(self.llamadas[-2:], ["logout", "quit"])
        self.assertEqual(len([i for i, v in self.escrituras if "obs" in str(v.get("OBSERVACIONES"))]), 1)  # la primera sí, la segunda no

    def test_un_error_de_fila_no_frena_el_lote(self):
        def falla_la_primera(d, ctx, row):
            if row["CODIGO"] == "C1":
                raise sd.ErrorFila("ERROR: algo", "detalle")
            return ("obs", [], 1, {})
        sd.procesar_fila_lectura = falla_la_primera
        sd.main()
        e = {i: v for i, v in self.escrituras}
        self.assertEqual(e[2]["ESTADO"], "ERROR: algo")
        self.assertIn("obs", e[4]["OBSERVACIONES"])

    def test_excepcion_inesperada_queda_como_error(self):
        sd.procesar_fila_lectura = lambda d, ctx, row: 1 / 0
        sd.main()
        e = {i: v for i, v in self.escrituras}
        self.assertEqual(e[2]["ESTADO"], "ERROR")
        self.assertIn("ZeroDivisionError", e[2]["OBSERVACIONES"])

    def test_aplicar_escribe_el_estado_de_cada_fila(self):
        sd.MODO = "aplicar"
        sd.main()
        e = {i: v for i, v in self.escrituras}
        self.assertEqual((e[2]["ESTADO"], e[4]["ESTADO"]), ("OK", "OK"))
        self.assertIn("aplicada C1", e[2]["OBSERVACIONES"])

    # ── regla: nunca empezar otra combinación si hay sustitutos sin asignar ──
    def _aplicar_y_ver_filas_procesadas(self, comportamiento):
        sd.MODO = "aplicar"
        procesadas = []

        def fila_aplicar(d, ctx, row):
            procesadas.append(row["CODIGO"])
            return comportamiento(row["CODIGO"])
        sd.procesar_fila_aplicar = fila_aplicar
        codigo_salida = None
        try:
            sd.main()
        except SystemExit as e:
            codigo_salida = e.code
        return procesadas, codigo_salida, {i: v for i, v in self.escrituras}

    def test_colgadas_detiene_la_corrida(self):
        def comp(cod):
            if cod == "C1":
                raise sd.ErrorFila("ERROR_COLGADAS", "quedaron en el provisorio")
            return ("OK", "ok", 1)
        procesadas, salida, e = self._aplicar_y_ver_filas_procesadas(comp)
        self.assertEqual(procesadas, ["C1"])                          # C2 NO se empieza
        self.assertEqual(salida, E.EXIT_DETENIDA)
        self.assertEqual(e[2]["ESTADO"], "ERROR_COLGADAS")
        self.assertNotIn(4, e)                                        # la siguiente queda PENDIENTE (sin tocar)
        self.assertEqual(self.llamadas[-2:], ["logout", "quit"])      # igual se cierra la sesión

    def test_revisar_y_provisorio_en_uso_tambien_detienen(self):
        for estado_devuelto, como in (("ERROR_REVISAR", "retorna"), ("ERROR: provisorio en uso", "lanza")):
            self.llamadas.clear(); self.escrituras.clear()

            def comp(cod, estado_devuelto=estado_devuelto, como=como):
                if como == "lanza":
                    raise sd.ErrorFila(estado_devuelto, "x")
                return (estado_devuelto, "dif", 1)
            procesadas, salida, _ = self._aplicar_y_ver_filas_procesadas(comp)
            self.assertEqual((procesadas, salida), (["C1"], E.EXIT_DETENIDA), estado_devuelto)

    def test_un_rechazo_previo_no_detiene(self):
        def comp(cod):
            if cod == "C1":
                raise sd.ErrorFila("ERROR: no soportado en esta fase", "varios estados")
            return ("OK", "ok", 1)
        procesadas, salida, e = self._aplicar_y_ver_filas_procesadas(comp)
        self.assertEqual(procesadas, ["C1", "C2"])                    # no se movió nada, se sigue
        self.assertIsNone(salida)
        self.assertEqual(e[4]["ESTADO"], "OK")

    def test_en_lectura_los_errores_no_detienen(self):
        sd.procesar_fila_lectura = lambda d, ctx, row: (_ for _ in ()).throw(sd.ErrorFila("ERROR_COLGADAS", "x"))
        sd.main()                                                      # no debe cortar ni salir con 76
        self.assertEqual(len([1 for i, v in self.escrituras if v.get("ESTADO") == "ERROR_COLGADAS"]), 2)

    def test_aplicar_sin_confirmacion_no_abre_el_navegador(self):
        sd.MODO = "aplicar"
        sd.exigir_sin_pendientes = self.guardado["exigir_sin_pendientes"]
        sd.CONFIRMO_SIN_PENDIENTES = ""
        sd.cargar_sheet = lambda w: ([], list(sd.COLS_DET))
        with self.assertRaises(SystemExit):
            sd.main()
        self.assertEqual(self.llamadas, [])                              # ni driver ni login

    def test_fecha_hasta_invalida_corta_antes_de_todo(self):
        sd.FECHA_HASTA = "31/12/1949"            # 1949: anterior a Desde (y fuera de 2000-2099)
        with self.assertRaises(ValueError):
            sd.main()
        self.assertEqual(self.llamadas, [])

    def test_modo_invalido(self):
        sd.MODO = "borrar"
        with self.assertRaises(SystemExit):
            sd.main()


@unittest.skipIf(sd is None, "falta una dependencia para importar el script")
class LecturaSobreConsultar(unittest.TestCase):
    def test_observaciones_y_detalle(self):
        guardado = (sd.consultar, sd.probar_seleccion_total)
        filas = [fila("R1"), fila("R2", status="OT")]
        sd.consultar = lambda *a, **k: {"found": 2, "filas": filas, "modo": "completa", "desc": "d"}
        sd.probar_seleccion_total = lambda d, f: "label"
        try:
            ctx = {"corrida": "c1", "desde": datetime(2026, 11, 1), "hasta": datetime(2049, 12, 31), "panel_pendiente": False}
            obs, detalle, found, marcas = sd.procesar_fila_lectura(None, ctx, {"LOCATION": "BUE", "SUPPLIER": "1ACOS1",
                                                                              "SERVICE TYPE": "GU", "CODIGO": "700GUI"})
        finally:
            sd.consultar, sd.probar_seleccion_total = guardado
        self.assertEqual(found, 2)
        self.assertIn("provisorio=BUE/GUIAPO/GU/000000", obs)
        self.assertIn("seleccion_total=OK(2 via label)", obs)
        self.assertEqual([d["FASE"] for d in detalle], ["LECTURA", "LECTURA"])
        self.assertEqual({d["STATUS_ORIGINAL"] for d in detalle}, {"IT", "OT"})
        self.assertTrue(all(set(d) <= set(sd.COLS_DET) for d in detalle))   # solo columnas que existen en la hoja
        with self.assertRaises(sd.ErrorFila):                                # fila incompleta
            sd.procesar_fila_lectura(None, ctx, {"LOCATION": "BUE", "SUPPLIER": "", "SERVICE TYPE": "GU", "CODIGO": "X"})


@unittest.skipIf(sd is None, "falta una dependencia para importar el script")
class CeldaPudo(unittest.TestCase):
    def test_pickup_dropoff_y_remarks_de_la_celda(self):
        f = {"pudo_filas": [["Pickup:", "01/Nov/2026 09:00", "hotel prueba"], ["Dropoff:", "01/Nov/2026 00:00", ""],
                            ["Remarks:", "texto prueba"]]}
        n = sd.normalizar_fila(dict(f))
        self.assertEqual((n["pickup_fecha_hora"], n["pickup_texto"]), ("01/Nov/2026 09:00", "hotel prueba"))
        self.assertEqual((n["dropoff_fecha_hora"], n["dropoff_texto"]), ("01/Nov/2026 00:00", ""))
        self.assertEqual(n["remarks"], "texto prueba")
        self.assertNotIn("pudo_filas", n)

    def test_remarks_en_otra_celda_o_ausente(self):
        n = sd.normalizar_fila({"pudo_filas": [["Remarks:", "", "dato"]]})
        self.assertEqual(n["remarks"], "dato")
        self.assertEqual(sd.normalizar_fila({"pudo_filas": [["Pickup:", "01/Nov/2026 00:00", ""]]})["remarks"], "")
        self.assertEqual(sd.normalizar_fila({})["remarks"], "")

    def test_el_detalle_lleva_los_remarks_de_la_grilla(self):
        ctx = {"corrida": "c1"}
        f = fila("R1")
        f["remarks"] = "texto prueba"
        d = sd.armar_detalle(ctx, "BUE", "1ACOS1", "GU", "700GUI", [f], "LECTURA", "LEIDA")
        self.assertEqual(d[0]["REMARKS"], "texto prueba")
        d = sd.armar_detalle(ctx, "BUE", "1ACOS1", "GU", "700GUI", [fila("R2")], "LECTURA", "LEIDA")   # sin la clave
        self.assertEqual(d[0]["REMARKS"], "")
