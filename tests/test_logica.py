"""Pruebas por simulación de common/logica.py (sin Tourplan ni Sheets).

Correr desde la raíz del repo:  python -m unittest discover -s tests -v
"""
import os
import random
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import logica as L


def fila(ref, code="700GUI", viaje="01/Nov/2026", status="IT", pudo="", **kw):
    f = {"location": "BUE", "service": "GU", "supplier": "1ACOS1", "code": code,
         "description": "d", "booking_name": "n", "booking_ref": ref,
         "travel_date": viaje, "date": viaje, "status": status, "pudo_raw": pudo}
    f.update(kw)
    return f


class Fechas(unittest.TestCase):
    def test_parseo(self):
        self.assertEqual(L.parsear_fecha("31/12/49"), datetime(2049, 12, 31))
        self.assertEqual(L.parsear_fecha("01/01/20"), datetime(2020, 1, 1))
        self.assertEqual(L.parsear_fecha("01/Nov/2026"), datetime(2026, 11, 1))
        self.assertEqual(L.parsear_fecha("31/12/2049"), datetime(2049, 12, 31))
        for malo in ("", None, "x", "32/01/26", "01-11-26", "01/13/26"):
            self.assertIsNone(L.parsear_fecha(malo), malo)

    def test_formato_siempre_dos_digitos(self):
        self.assertEqual(L.fmt_ddmmyy(datetime(2049, 12, 31)), "31/12/49")
        self.assertEqual(L.fmt_ddmmyy(datetime(2020, 1, 1)), "01/01/20")
        self.assertEqual(L.fmt_tp(datetime(2026, 11, 1)), "01/Nov/2026")

    def test_ida_y_vuelta_de_formato(self):
        dt = datetime(2049, 12, 31)
        self.assertEqual(L.parsear_fecha(L.fmt_tp(dt)), dt)
        self.assertEqual(L.parsear_fecha(L.fmt_ddmmyy(dt)), dt)

    def test_rango(self):
        d, h = datetime(2026, 11, 1), datetime(2049, 12, 31)
        self.assertIsNone(L.validar_rango(d, h))
        self.assertIsNone(L.validar_rango(d, d))
        self.assertIn("anterior", L.validar_rango(h, d))
        self.assertIn("Desde", L.validar_rango(None, h))
        self.assertIn("Hasta", L.validar_rango(d, None))
        # 1949 sería el caso "año interpretado como pasado": tampoco se escribe con 2 dígitos
        self.assertIn("2000", L.validar_rango(d, datetime(1949, 12, 31)))
        self.assertIn("2000", L.validar_rango(datetime(2100, 1, 1), datetime(2100, 2, 1)))


class InputYProvisorios(unittest.TestCase):
    def test_deduplicado_ignora_mayusculas_y_espacios(self):
        a = {"LOCATION": "BUE", "SUPPLIER": "1ACOS1", "SERVICE TYPE": "GU", "CODIGO": "700GUI"}
        b = {"LOCATION": " bue ", "SUPPLIER": "1acos1", "SERVICE TYPE": "gu", "CODIGO": "700gui"}
        c = dict(a, CODIGO="OTRO")
        vacia = {"LOCATION": "BUE", "SUPPLIER": "", "SERVICE TYPE": "GU", "CODIGO": "X"}
        unicas, rep = L.deduplicar_input([a, b, c, vacia, dict(vacia)])
        self.assertEqual(unicas, [a, c, vacia, dict(vacia)])
        self.assertEqual(rep, [(b, a)])

    def test_provisorios(self):
        mapa = L.mapa_provisorios([
            {"LOCATION": "bue", "SERVICE TYPE": "gu", "SUPPLIER_PROV": "guiapo", "CODIGO_PROV": "000000"},
            {"LOCATION": "FTE", "SERVICE TYPE": "GU", "SUPPLIER_PROV": "", "CODIGO_PROV": "000000"},
        ])
        self.assertEqual(mapa, {("BUE", "GU"): ("GUIAPO", "000000")})
        filas = [{"LOCATION": "BUE", "SERVICE TYPE": "GU"}, {"LOCATION": "FTE", "SERVICE TYPE": "GU"},
                 {"LOCATION": "FTE", "SERVICE TYPE": "GU"}, {"LOCATION": "USH", "SERVICE TYPE": "GU"}]
        pares, faltan = L.pares_sin_provisorio(filas, mapa)
        self.assertEqual(pares, [("BUE", "GU"), ("FTE", "GU"), ("USH", "GU")])
        self.assertEqual(faltan, [("FTE", "GU"), ("USH", "GU")])
        self.assertEqual(L.provisorio_de(filas[0], mapa), ("GUIAPO", "000000"))
        self.assertIsNone(L.provisorio_de(filas[1], mapa))


class Encabezados(unittest.TestCase):
    CANON = ["LOCATION", "SERVICE TYPE", "SUPPLIER_PROV", "CODIGO_PROV", "ESTADO"]

    def test_variantes_equivalentes(self):
        self.assertEqual(L.canonizar_columnas(
            ["Location", "service_type", "SUPPLIER PROV", " codigo  prov ", "Estado", "OTRA"], self.CANON),
            ["LOCATION", "SERVICE TYPE", "SUPPLIER_PROV", "CODIGO_PROV", "ESTADO", "OTRA"])

    def test_fila_y_mapa_con_encabezados_distintos(self):
        crudas = [{"Location": "bue", "SERVICE_TYPE": "gu", "supplier prov": "guiapo", "Codigo_Prov": "000000",
                   "__row_idx__": 2}]
        filas = [L.canonizar_fila(f, self.CANON) for f in crudas]
        self.assertEqual(filas[0]["__row_idx__"], 2)
        self.assertEqual(L.mapa_provisorios(filas), {("BUE", "GU"): ("GUIAPO", "000000")})

    def test_provisorios_con_el_formato_de_la_hoja_substitute(self):
        # Hoja real de la usuaria: LOCATION / SUPPLIER / SERVICE_TYPE / CODIGO = datos del provisorio
        canon = ["LOCATION", "SERVICE TYPE", "SUPPLIER_PROV", "CODIGO_PROV"]
        alias = {"SUPPLIER": "SUPPLIER_PROV", "CODIGO": "CODIGO_PROV"}
        cols = ["LOCATION", "SUPPLIER", "SERVICE_TYPE", "CODIGO"]
        self.assertEqual(L.canonizar_columnas(cols, canon, alias), ["LOCATION", "SUPPLIER_PROV", "SERVICE TYPE", "CODIGO_PROV"])
        fila = L.canonizar_fila(dict(zip(cols, ["BUE", "GUIAPO", "GU", "000000"])), canon, alias)
        self.assertEqual(L.mapa_provisorios([fila]), {("BUE", "GU"): ("GUIAPO", "000000")})

    def test_celda_vacia_invalida_la_fila(self):
        filas = [L.canonizar_fila({"LOCATION": "BUE", "SERVICE TYPE": "GU", "SUPPLIER_PROV": "GUIAPO",
                                   "CODIGO_PROV": ""}, self.CANON)]
        self.assertEqual(L.mapa_provisorios(filas), {})


class LecturaProgresiva(unittest.TestCase):
    def leer(self, filas, ventana, paso):
        """Simula un virtual scroll: ventana de N filas que avanza de a 'paso'."""
        acc, pos = [], 0
        while True:
            acc = L.fusionar_ventana(acc, filas[pos:pos + ventana])
            if len(acc) >= len(filas) or pos + ventana >= len(filas):
                return acc
            pos += paso

    def test_sin_scroll(self):
        filas = [fila(f"R{i}") for i in range(7)]
        self.assertEqual(L.fusionar_ventana([], filas), filas)
        self.assertEqual(L.fusionar_ventana(filas, filas), filas)

    def test_ventana_con_solapamiento(self):
        filas = [fila(f"R{i:04d}") for i in range(1002)]
        for ventana, paso in ((12, 5), (20, 7), (9, 9 - 1), (15, 1)):
            self.assertEqual(self.leer(filas, ventana, paso), filas, (ventana, paso))

    def test_filas_distintas_aleatorias(self):
        rnd = random.Random(7)
        filas = [fila(f"R{rnd.randint(0, 400)}", status=rnd.choice(["IT", "OT", "OP"])) for _ in range(500)]
        self.assertEqual(self.leer(filas, 14, 6), filas)

    def test_sin_solapamiento_se_detecta_por_conteo(self):
        # Si el scroll salta más que la ventana, faltan filas: el total no cierra con Found.
        filas = [fila(f"R{i}") for i in range(100)]
        self.assertLess(len(self.leer(filas, 10, 25)), len(filas))

    def test_duplicados_identicos_dentro_de_la_ventana(self):
        # Dos líneas 100% idénticas no se pierden mientras estén en la misma ventana.
        filas = [fila("A"), fila("B"), fila("B"), fila("C"), fila("D"), fila("E")]
        self.assertEqual(self.leer(filas, 4, 2), filas)


class Analisis(unittest.TestCase):
    def test_duplicados(self):
        filas = [fila("A"), fila("A"), fila("B", status="IT"), fila("B", status="OT"),
                 fila("C", pudo="x"), fila("C", pudo="y"), fila("D")]
        r = L.analizar_duplicados(filas)
        self.assertEqual(r, {"claves": 3, "filas": 6, "difieren_estado": 1, "difieren_pudo": 1})
        self.assertEqual(L.analizar_duplicados([fila("A")])["claves"], 0)

    def test_estados_y_filtro(self):
        filas = [fila("A"), fila("B"), fila("C", status="OT")]
        self.assertEqual(L.conteo_estados(filas), {"IT": 2, "OT": 1})
        self.assertEqual(L.filas_fuera_del_filtro(filas, "bue", "gu", "1acos1", "700gui"), [])
        raro = fila("Z", supplier="OTRO")
        self.assertEqual(L.filas_fuera_del_filtro(filas + [raro], "BUE", "GU", "1ACOS1", "700GUI"), [raro])

    def test_pudo(self):
        self.assertEqual(L.texto_pudo(["01/Nov/2026 00:00", "hotel"]), ("01/Nov/2026 00:00", "hotel"))
        self.assertEqual(L.texto_pudo([]), ("", ""))


if __name__ == "__main__":
    unittest.main()


class EscrituraEnLote(unittest.TestCase):
    """common/sheets_batch.py con una hoja falsa (sin Google)."""

    class HojaFalsa:
        def __init__(self):
            self.llamadas = []

        def append_rows(self, valores, value_input_option=None, table_range=None, **kw):
            self.llamadas.append((valores, value_input_option, table_range))

    def test_lotes_y_orden_de_columnas(self):
        try:
            from common.sheets_batch import agregar_filas_batch
        except ModuleNotFoundError as e:
            self.skipTest(f"falta una dependencia ({e}); instalar requirements.txt")
        ws = self.HojaFalsa()
        cols = ["A", "B", "C"]
        filas = [{"B": i, "A": f"a{i}", "X": "ignorada"} for i in range(1000)]
        self.assertEqual(agregar_filas_batch(ws, cols, filas, tamano_lote=400), 1000)
        self.assertEqual([len(v) for v, _, _ in ws.llamadas], [400, 400, 200])
        self.assertEqual(ws.llamadas[0][0][0], ["a0", "0", ""])        # por nombre, faltante vacío
        self.assertTrue(all(o == "RAW" and t == "A1" for _, o, t in ws.llamadas))
        self.assertEqual(agregar_filas_batch(ws, cols, []), 0)

    def test_columnas_con_otro_formato(self):
        try:
            from common.sheets_batch import agregar_filas_batch
        except ModuleNotFoundError as e:
            self.skipTest(f"falta una dependencia ({e})")
        ws = self.HojaFalsa()
        agregar_filas_batch(ws, ["SERVICE_TYPE", "Booking Name"], [{"SERVICE TYPE": "GU", "BOOKING_NAME": "x"}])
        self.assertEqual(ws.llamadas[0][0], [["GU", "x"]])
