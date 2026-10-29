"""Pruebas del reporte PRE obligatorio: lectura, cobertura de las combinaciones PENDIENTES y guardado."""
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import pre_reporte as P

CAB = "Agent,AgentName,Supplier,SupplierName,Loc,Service,Option,Description, Description 2,Date,Pax,Child,Qty,Total,Rooms,Nights,Currency,Cost,Agent,Retail,Reference,BookingName,BookingNameAlias,Status,D.O.T,VoucherNo"
PRE = (CAB + "\n"
       "HOLB,H,1FAGA1,F,BUE,GU,AEHTID,TF,,21-Oct-2026,20,0,1,20,1,1,USD,75.97,99.30,99.30,EAGP246321,X,,KK,21-Oct-2026,1\n"
       "RIV1,R,1FAGA1,F,BUE,GU,CT4ID,CT,,26-Oct-2026,40,0,1,40,1,1,USD,115.38,164.99,206.24,EAGP246528,Y,,KK,25-Oct-2026,2\n")


def fila(loc, sup, st, cod):
    return {"LOCATION": loc, "SUPPLIER": sup, "SERVICE TYPE": st, "CODIGO": cod, "ESTADO": "PENDIENTE"}


class Pre(unittest.TestCase):
    def test_lee_un_product_analysis(self):
        self.assertEqual(len(P.leer_pre(PRE.encode())), 2)

    def test_rechaza_lo_que_no_es_product_analysis(self):
        with self.assertRaises(ValueError):
            P.leer_pre(b"Status,Supplier,Service Date\nOK,X,01-Nov-2026\n")
        with self.assertRaises(ValueError):
            P.leer_pre(b"")

    def test_cobertura_completa(self):
        filas = P.leer_pre(PRE.encode())
        pend = [fila("BUE", "1FAGA1", "GU", "AEHTID"), fila("bue", "1faga1", "gu", "ct4id")]       # mayúsculas/minúsculas no importan
        self.assertEqual(P.combinaciones_faltantes(filas, pend), [])

    def test_informa_las_faltantes_sin_repetir(self):
        filas = P.leer_pre(PRE.encode())
        pend = [fila("BUE", "1FAGA1", "GU", "OTRO"), fila("BUE", "1FAGA1", "GU", "OTRO"), fila("BUE", "1FAGA1", "GU", "AEHTID")]
        self.assertEqual(P.combinaciones_faltantes(filas, pend), [("BUE", "1FAGA1", "GU", "OTRO")])

    def test_las_fechas_fuera_del_rango_no_cuentan(self):
        filas = P.leer_pre(PRE.encode())
        pend = [fila("BUE", "1FAGA1", "GU", "CT4ID")]            # su única línea es del 26/10
        self.assertEqual(P.combinaciones_faltantes(filas, pend, datetime(2026, 10, 1), datetime(2026, 10, 31)), [])
        self.assertEqual(len(P.combinaciones_faltantes(filas, pend, datetime(2026, 11, 1), datetime(2049, 12, 31))), 1)

    def test_guardar_y_ultimo(self):
        d = tempfile.mkdtemp()
        self.assertIsNone(P.ultimo_pre(os.path.join(d, "no_existe")))
        r = P.guardar_pre(b"abc", os.path.join(d, "pre_reportes"))
        self.assertEqual(P.ultimo_pre(os.path.join(d, "pre_reportes")), r)
        self.assertEqual(r.read_bytes(), b"abc")


if __name__ == "__main__":
    unittest.main()
