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
        self.assertEqual([l["TIPO"] for l in r["lineas"]], ["PROMO", "GRUPOS", "GRUPOS"])               # PR: promoción

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

    def fila_results(self, ref, fecha="02/Nov/2026", cod="EZHT19"):
        return {"booking_ref": ref, "date": fecha, "location": "BUE", "service": "TR", "supplier": "1TEP01", "code": cod}

    def lista_ez(self, ref="EAGR123456", fecha="02/11/2026"):
        return X.por_combinacion([{"REFERENCE": ref, "SERVICE DATE": fecha, "LOCATION": "BUE", "SERVICE TYPE": "TR", "SUPPLIER": "1TEP01", "CODIGO": "EZHT19",
                                   "TIPO": "GRUPOS", "VOUCHER": "2830276", "DESCRIPCION": ""}])

    def test_excluye_solo_la_fila_que_coincide_en_file_fecha_y_codigo(self):
        filas = [self.fila_results("EAGR123456"), self.fila_results("EAGR999999"), self.fila_results("EAGR123456", "03/Nov/2026"),
                 self.fila_results("EAGR123456", cod="OTRO")]
        a_procesar, excluidas = X.separar_resultados(filas, self.lista_ez(), "BUE", "1TEP01", "TR", "EZHT19")
        self.assertEqual([(e["fila"]["booking_ref"], e["fila"]["date"]) for e in excluidas], [("EAGR123456", "02/Nov/2026")])
        self.assertEqual(len(a_procesar), 3)                                       # otros files, otra fecha u otro código: se procesan
        self.assertIn("Costo manualizado (Override)", excluidas[0]["motivo"])
        self.assertIn("voucher 2830276", excluidas[0]["motivo"])

    def test_si_hay_mas_filas_iguales_que_lineas_en_la_lista_se_excluyen_todas(self):
        filas = [self.fila_results("EAGR123456"), self.fila_results("EAGR123456"), self.fila_results("EAGR999999")]
        a_procesar, excluidas = X.separar_resultados(filas, self.lista_ez(), "BUE", "1TEP01", "TR", "EZHT19")
        self.assertEqual(len(excluidas), 2)
        self.assertEqual([f["booking_ref"] for f in a_procesar], ["EAGR999999"])      # los otros files se procesan
        self.assertTrue(all("Hay 2 líneas" in e["motivo"] and "no se pueden distinguir" in e["motivo"] for e in excluidas))

    def test_con_dos_lineas_en_la_lista_y_dos_en_results_no_hay_ambiguedad(self):
        lista = X.por_combinacion(sum((list(v) for v in self.lista_ez().values()), []) * 2)
        _, excluidas = X.separar_resultados([self.fila_results("EAGR123456")] * 2, lista, "BUE", "1TEP01", "TR", "EZHT19")
        self.assertEqual(len(excluidas), 2)
        self.assertTrue(all("no se pueden distinguir" not in e["motivo"] for e in excluidas))

    def test_sin_lista_para_la_combinacion_todo_se_procesa(self):
        filas = [self.fila_results("EAGR123456")]
        self.assertEqual(X.separar_resultados(filas, {}, "BUE", "1TEP01", "TR", "EZHT19"), (filas, []))
        self.assertEqual(X.separar_resultados(filas, self.lista_ez(), "BUE", "1TEP01", "TR", "OTRO"), (filas, []))

    def test_formatos_de_fecha_y_clave_para_la_pantalla(self):
        self.assertEqual(X.clave_de_resultado(self.fila_results(" eagr1 ", "02/Nov/2026"))[:2], ("EAGR1", "02/11/2026"))
        self.assertEqual(X.clave_para_pantalla(self.fila_results("EAGR1", "02/Nov/2026")), "EAGR1|02/NOV/2026|BUE|TR|1TEP01|EZHT19")

    def test_lee_la_tabla_de_tourplan_pegada_tal_cual_en_la_pestana(self):
        class Hoja:
            title = "EXCLUSIONES"

            def get_all_values(self):
                return [f.split("\t") for f in [CAB] + FILAS] + [[""] * 15]                         # incluye una fila vacía al final, como suele quedar al pegar

        ws = type("W", (), {"spreadsheet": type("L", (), {"worksheets": lambda self: [Hoja()]})()})()
        lineas = X.leer_del_sheet(ws)
        self.assertEqual(len(lineas), 3)                                                              # Override = No no cuenta
        self.assertEqual((lineas[0]["REFERENCE"], lineas[0]["CODIGO"], lineas[0]["TIPO"]), ("EAPR251948", "CT437", "PROMO"))
        self.assertEqual(list(lineas.invalidas), [])
        self.assertIsNone(X.fecha_de_carga(lineas))                                                   # pegada a mano: no tiene fecha de carga
        self.assertEqual(len(X.por_combinacion(lineas)), 2)

    def test_las_filas_pegadas_que_no_se_pueden_leer_se_informan(self):
        class Hoja:
            title = "EXCLUSIONES"

            def get_all_values(self):
                mala = FILAS[0].replace("BUETR1TEP01CT437", "BUE")
                return [f.split("\t") for f in [CAB, mala, FILAS[1]]]

        ws = type("W", (), {"spreadsheet": type("L", (), {"worksheets": lambda self: [Hoja()]})()})()
        lineas = X.leer_del_sheet(ws)
        self.assertEqual(len(lineas), 1)
        self.assertEqual(len(lineas.invalidas), 1)
        self.assertIn("código completo no válido", lineas.invalidas[0][1])

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
