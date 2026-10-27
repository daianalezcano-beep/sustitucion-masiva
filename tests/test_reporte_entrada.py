"""Pruebas de reporte_entrada: arma las combinaciones a procesar a partir de un reporte Service Status."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import reporte_entrada as R

CAB = "Status,Supplier,Service Date,DateEntered,Service,Location,Option,BookingName,Reference,Status,Pax,SCU_Qty,Escort,Driver,Guide,Agent,Consultant,ConsultantCode,RoomConfig,SupplierName"
LINEAS = [
    "CX,GUIAPO,28-Oct-2026,27-Apr-2026,GU,BUE,CT3SIB,DAM x2,EAFI1,QU,2,1,0,0,0,DELM,Carla,CCU,,Guia",   # servicio cancelado: afuera
    "OT,1SANC9,29-Oct-2026,27-May-2026,GU,BUE,CT3ID,\"DEL POZO , MARIA x4\",EAFI2,OK,4,1,0,0,0,AM38,Vic,VBA,,Sanchez",
    "IT,1SANC9,30-Oct-2026,31-Aug-2026,GU,BUE,CT3ID,GUIJARRO x2,EAFI3,OK,2,1,0,0,0,AM38,Vic,VBA,,Sanchez",
    "OT,1SANC9,31-Oct-2026,10-Jun-2026,GU,BUE,CT3ID,Pena x2,EAFI4,OK,2,1,0,0,0,AM38,And,AJA,,Sanchez",
    "OT,GUIAPO,29-Oct-2026,17-Apr-2026,GU,BUE,EZHTID,VAN x2,EAFT5,OK,2,1,0,0,0,27VA,Jul,JJU,,Guia",
    "OT,6ADR1,02-Nov-2026,02-Jun-2025,GU,MDZ,AEHTID,Andean,EAGP6,KK,11,1,1,0,0,VARL,Tat,THA,,Adriana",     # MDZ/GU: sin provisorio
    "OT,6JUA1,03-Nov-2026,31-Oct-2025,GU,BUE,CT4ID,HOBO,EAGP7,KK,16,1,1,0,0,HOBO,Tat,THA,,Juaya",         # ya está en la hoja
    "CX,6BAL1,31-Oct-2026,18-Feb-2026,GU,BUE,AEHTID,CEC x2,EAFI8,OK,2,1,0,0,0,ALID,Anahi,AED,,Baldo",     # combinación solo con cancelados
]
CSV = (CAB + "\n" + "\n".join(LINEAS) + "\n").encode("utf-8")
PROV = {("BUE", "GU"): "1EURO1"}
REAL = "/root/.claude/uploads/cb0aad81-6516-5f31-b57f-bfe3c4c9eb8a/e470052d-Service_Status_20261027_105442.csv"


class ReporteEntrada(unittest.TestCase):
    def plan(self, **kw):
        return R.armar_plan(R.leer_reporte(CSV), PROV, en_hoja={("BUE", "6JUA1", "GU", "CT4ID"): "OK"}, **kw)

    def test_lee_y_valida_las_columnas(self):
        filas = R.leer_reporte(CSV)
        self.assertEqual((len(filas), filas[0]["Status"], filas[0]["Status (2)"]), (8, "CX", "QU"))     # servicio vs reserva
        with self.assertRaises(ValueError):
            R.leer_reporte(b"A,B\n1,2\n")
        with self.assertRaises(ValueError):
            R.leer_reporte(b"")

    def test_excluye_cancelados_y_deduplica_por_combinacion(self):
        p = self.plan()
        r = p["resumen"]
        self.assertEqual((r["Líneas del reporte"], r["Líneas excluidas (servicio cancelado)"]), (8, 2))
        self.assertEqual([(c["SUPPLIER"], c["CODIGO"], c["RESERVAS"]) for c in p["a_cargar"]], [("1SANC9", "CT3ID", 3), ("GUIAPO", "EZHTID", 1)])
        self.assertEqual((r["Combinaciones a cargar"], r["Reservas a cargar"]), (2, 4))
        self.assertEqual(p["a_cargar"][0]["ESTADOS"], "IT 1, OT 2")
        self.assertEqual((p["a_cargar"][0]["DESDE"], p["a_cargar"][0]["HASTA"]), ("29/10/2026", "31/10/2026"))

    def test_descarta_las_que_no_se_pueden_procesar_con_su_motivo(self):
        motivos = {(c["LOCATION"], c["CODIGO"]): c["MOTIVO"] for c in self.plan()["descartadas"]}
        self.assertIn("Sin provisorio definido para MDZ/GU", motivos[("MDZ", "AEHTID")])
        self.assertIn("Ya está en la hoja (estado: OK)", motivos[("BUE", "CT4ID")])
        self.assertEqual(len(motivos), 2)

    def test_orden_por_location_service_supplier_code(self):
        filas = R.leer_reporte(CSV)
        orden = [(c["SUPPLIER"], c["CODIGO"]) for c in R.armar_plan(filas, PROV)["a_cargar"]]
        self.assertEqual(orden, sorted(orden, key=lambda t: (t[0], t[1])))

    def test_filas_para_la_hoja_y_tiempo(self):
        p = self.plan()
        hoja = R.filas_para_hoja(p["a_cargar"])
        self.assertEqual(hoja[0], {"LOCATION": "BUE", "SUPPLIER": "1SANC9", "SERVICE TYPE": "GU", "CODIGO": "CT3ID", "ESTADO": "PENDIENTE"})
        # 75 fijos + (22 + 1.5*3 + 12*1) + (22 + 1.5*1)
        self.assertEqual(p["resumen"]["Segundos estimados"], int(75 + 22 + 4.5 + 12 + 22 + 1.5))
        self.assertEqual(R.segundos_estimados([]), 0)
        self.assertEqual((R.texto_duracion(90), R.texto_duracion(9000)), ("2 min", "2 h 30 min"))

    @unittest.skipUnless(os.path.exists(REAL), "falta el reporte real de ejemplo")
    def test_reporte_real_de_ejemplo(self):
        p = R.armar_plan(R.leer_reporte(open(REAL, "rb").read()), PROV)
        r = p["resumen"]
        self.assertEqual((r["Líneas del reporte"], r["Líneas excluidas (servicio cancelado)"]), (1595, 202))
        self.assertEqual((r["Combinaciones a cargar"], r["Reservas a cargar"]), (306, 1393))
        self.assertEqual(r["Desde"].strftime("%d/%m/%Y"), "28/10/2026")


if __name__ == "__main__":
    unittest.main()
