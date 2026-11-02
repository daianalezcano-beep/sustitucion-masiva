"""Pruebas de la lista de costos manualizados (common/exclusiones.py) y de cómo la usa el script."""
import io
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import exclusiones as X

CAB = "Travel Date\tBkg. Reference\tBkgName\tConsultant\tBkg. Stat\tAgent\tSrv. Date\tSrv. Stat\tVoucher #\tSrv. Type\tOpt. Description\tDropOff Date\tPrice Code\tOverride\tFull option code"
FILAS = [
    "20/11/2026\tEAPR251948\tFAMTRIP\tAdele\tKK\tGVQ1\t21/11/2026\tOT\t2830276\tTR\tTR City 4hs Bus (20/33 pax)\t21/11/2026\tTR\tYes\tBUETR1TEP01CT437",
    "20/11/2026\tEAGP251948\tFAMTRIP\tAdele\tKK\tGVQ1\t22/11/2026\tOT\t2830288\tTR\tTR City 4hs Bus (20/33 pax)\t22/11/2026\tTR\tYes\tBUETR1TEP01CT437",
    "15/3/2027\tEAGP250538\tArgentina History Tour\tTatiana\tKK\tLR8A\t20/3/2027\tOK\t2822911\tTF\tTransfer\t20/3/2027\tTR\tYes\tBUETF1TEP01600TRF",
    "15/3/2027\tEAFI250999\tOtro\tT\tOK\tLR8A\t20/3/2027\tOK\t2822999\tTF\tTransfer\t20/3/2027\tTR\tNo\tBUETF1TEP01600TRF",   # Override = No: no cuenta
]
TSV = "\n".join([CAB] + FILAS).encode("utf-8")


class Lista(unittest.TestCase):
    def test_parte_el_codigo_completo(self):
        self.assertEqual(X.partir_codigo_completo("BUETR1TEP01CT437"), ("BUE", "TR", "1TEP01", "CT437"))
        self.assertEqual(X.partir_codigo_completo("BUETF1TEP01600TRF"), ("BUE", "TF", "1TEP01", "600TRF"))
        self.assertIsNone(X.partir_codigo_completo("BUETR"))

    def test_lee_la_tabla_dinamica_y_solo_cuenta_override_yes(self):
        r = X.leer_lista(TSV)
        self.assertEqual((r["total"], len(r["lineas"]), r["invalidas"]), (4, 3, []))
        l0 = r["lineas"][0]
        self.assertEqual((l0["REFERENCE"], l0["SERVICE DATE"], l0["LOCATION"], l0["SERVICE TYPE"], l0["SUPPLIER"], l0["CODIGO"], l0["VOUCHER"]),
                         ("EAPR251948", "21/11/2026", "BUE", "TR", "1TEP01", "CT437", "2830276"))
        self.assertEqual([l["TIPO"] for l in r["lineas"]], ["", "GRUPOS", "GRUPOS"])                   # PR no está clasificado

    def test_acepta_csv_con_punto_y_coma_y_excel(self):
        csv = TSV.decode().replace("\t", ";").encode()
        self.assertEqual(len(X.leer_lista(csv)["lineas"]), 3)
        from openpyxl import Workbook
        libro = Workbook()
        for fila in ([c for c in CAB.split("\t")], *[f.split("\t") for f in FILAS]):
            libro.active.append(fila)
        buf = io.BytesIO()
        libro.save(buf)
        self.assertEqual(len(X.leer_lista(buf.getvalue(), "lista.xlsx")["lineas"]), 3)

    def test_archivo_equivocado_o_filas_invalidas(self):
        with self.assertRaises(ValueError):
            X.leer_lista(b"A,B\n1,2\n")
        mala = TSV.decode().replace("BUETR1TEP01CT437", "BUETR", 1).replace("21/11/2026\tOT", "fecha rara\tOT", 1).encode()
        r = X.leer_lista(mala)
        self.assertEqual(len(r["invalidas"]), 1)
        self.assertIn("código completo no válido", r["invalidas"][0][1])

    def test_una_fila_se_excluye_si_su_combinacion_y_tipo_estan_en_la_lista(self):
        por = X.por_combinacion(X.leer_lista(TSV)["lineas"])
        base = {"LOCATION": "BUE", "SUPPLIER": "1TEP01", "SERVICE TYPE": "TR", "CODIGO": "CT437"}
        self.assertEqual(len(X.lineas_que_excluyen(dict(base, TIPO="GRUPOS"), por)), 1)               # la línea EAGP (grupos)
        self.assertEqual(X.lineas_que_excluyen(dict(base, TIPO="FITS"), por), [])                      # FITS de la misma combinación no se toca
        self.assertEqual(len(X.lineas_que_excluyen(dict(base), por)), 2)                               # fila sin TIPO: cualquiera
        self.assertEqual(X.lineas_que_excluyen(dict(base, CODIGO="OTRO", TIPO="GRUPOS"), por), [])
        self.assertIn("EAGP251948 22/11/2026 (voucher 2830288)", X.texto_de_exclusion(X.lineas_que_excluyen(dict(base, TIPO="GRUPOS"), por)))

    def test_guardar_y_leer_en_el_sheet(self):
        class Hoja:
            def __init__(self, titulo):
                self.title, self.valores = titulo, []

            def get_all_values(self):
                return self.valores

            def clear(self):
                self.valores = []

            def update(self, range_name=None, values=None, value_input_option=None):
                self.valores = [list(f) for f in values]

        class Libro:
            def __init__(self):
                self.hojas = []

            def worksheets(self):
                return self.hojas

            def add_worksheet(self, title, rows, cols):
                h = Hoja(title)
                self.hojas.append(h)
                return h

        ws = type("W", (), {"spreadsheet": Libro()})()
        self.assertEqual(X.leer_del_sheet(ws), [])                                                      # sin pestaña
        lineas = X.leer_lista(TSV)["lineas"]
        X.guardar_en_el_sheet(ws, lineas, ahora=datetime(2026, 11, 2, 10, 30))
        guardadas = X.leer_del_sheet(ws)
        self.assertEqual(len(guardadas), 3)
        self.assertEqual(X.fecha_de_carga(guardadas), datetime(2026, 11, 2, 10, 30))
        self.assertEqual(X.resumen_de_carga(guardadas), (3, 2))
        X.guardar_en_el_sheet(ws, [], ahora=datetime(2026, 11, 3))                                      # vaciar la lista
        self.assertEqual(X.leer_del_sheet(ws), [])
        self.assertEqual(X.leer_del_sheet(object()), [])                                                # un Sheet que falla no rompe nada


if __name__ == "__main__":
    unittest.main()
