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
        self.assertEqual(r["Líneas excluidas (proveedor por asignar)"], 1)                         # GUIAPO / "por asignar"
        self.assertEqual([(c["SUPPLIER"], c["CODIGO"], c["RESERVAS"]) for c in p["a_cargar"]], [("1SANC9", "CT3ID", 3)])
        self.assertEqual((r["Combinaciones a cargar"], r["Reservas a cargar"]), (1, 3))
        self.assertEqual(p["a_cargar"][0]["ESTADOS"], "IT 1, OT 2")
        self.assertEqual((p["a_cargar"][0]["DESDE"], p["a_cargar"][0]["HASTA"]), ("29/10/2026", "31/10/2026"))

    def test_descarta_las_que_no_se_pueden_procesar_con_su_motivo(self):
        motivos = {(c["LOCATION"], c["CODIGO"]): c["MOTIVO"] for c in self.plan()["descartadas"]}
        self.assertIn("Sin provisorio definido para MDZ/GU", motivos[("MDZ", "AEHTID")])
        self.assertIn("Ya está en la hoja (estado: OK)", motivos[("BUE", "CT4ID")])
        self.assertEqual(len(motivos), 2)

    def test_descarta_los_codigos_genericos_700_y_600(self):
        lineas = "".join(f"OT,1SANC9,29-Oct-2026,27-May-2026,GU,BUE,{c},B x2,EAFI{i},OK,2,1,0,0,0,A,V,V,,Sanchez\n"
                          for i, c in enumerate(("700GUI", "600TRA", "700gui", "700GUIA", "7000GU", "CT3ID")))
        p = R.armar_plan(R.leer_reporte((CAB + "\n" + lineas).encode("utf-8")), PROV)
        self.assertEqual([c["CODIGO"] for c in p["a_cargar"]], ["7000GU", "700GUIA", "CT3ID"])         # solo 700/600 + 3 letras
        genericos = {c["CODIGO"]: c["MOTIVO"] for c in p["descartadas"]}
        self.assertEqual(sorted(genericos), ["600TRA", "700GUI"])                                      # mayúsculas/minúsculas: 700gui = 700GUI
        self.assertIn("Código genérico", genericos["700GUI"])

    def test_separa_cada_combinacion_en_fits_y_grupos_por_el_department_de_la_reference(self):
        lineas = ("OT,1SANC9,29-Oct-2026,27-May-2026,GU,BUE,CT3ID,A x2,EAFI9,OK,2,1,0,0,0,A,V,V,,Sanchez\n"      # FI: FITS
                  "OT,1SANC9,29-Oct-2026,27-May-2026,GU,BUE,CT3ID,B x2,EAGP9,KK,2,1,0,0,0,A,V,V,,Sanchez\n"      # GP: GRUPOS
                  "OT,1SANC9,30-Oct-2026,27-May-2026,GU,BUE,CT3ID,C x2,TDGR9,KK,2,1,0,0,0,A,V,V,,Sanchez\n"      # TD + GR: GRUPOS
                  "OT,1SANC9,29-Oct-2026,27-May-2026,GU,BUE,CT3ID,D x2,EAFG9,OK,2,1,0,0,0,A,V,V,,Sanchez\n")     # FG: GRUPOS (se desprende de un grupo)
        p = R.armar_plan(R.leer_reporte((CAB + "\n" + lineas).encode("utf-8")), PROV)
        self.assertEqual([(c["TIPO"], c["RESERVAS"]) for c in p["a_cargar"]], [("FITS", 1), ("GRUPOS", 3)])
        self.assertEqual(R.filas_para_hoja(p["a_cargar"])[1]["TIPO"], "GRUPOS")

    def test_un_department_sin_clasificar_no_se_procesa(self):
        lineas = ("OT,1SANC9,29-Oct-2026,27-May-2026,GU,BUE,CT3ID,A x2,EAPR9,KK,2,1,0,0,0,A,V,V,,Sanchez\n"      # PR: marcado para ambos
                  "OT,1SANC9,29-Oct-2026,27-May-2026,GU,BUE,CT3ID,B x2,EAFI9,OK,2,1,0,0,0,A,V,V,,Sanchez\n")
        p = R.armar_plan(R.leer_reporte((CAB + "\n" + lineas).encode("utf-8")), PROV)
        self.assertEqual([c["TIPO"] for c in p["a_cargar"]], ["FITS"])
        self.assertEqual(p["resumen"]["Líneas de Department sin clasificar"], 1)
        motivo = p["descartadas"][0]["MOTIVO"]
        self.assertIn("Department sin clasificar (PR)", motivo)

    def test_en_hoja_distingue_el_tipo_y_una_fila_sin_tipo_cubre_a_los_dos(self):
        lineas = ("OT,1SANC9,29-Oct-2026,27-May-2026,GU,BUE,CT3ID,A x2,EAFI9,OK,2,1,0,0,0,A,V,V,,Sanchez\n"
                  "OT,1SANC9,29-Oct-2026,27-May-2026,GU,BUE,CT3ID,B x2,EAGP9,KK,2,1,0,0,0,A,V,V,,Sanchez\n")
        filas = R.leer_reporte((CAB + "\n" + lineas).encode("utf-8"))
        p = R.armar_plan(filas, PROV, en_hoja={("BUE", "1SANC9", "GU", "CT3ID", "FITS"): "OK"})
        self.assertEqual([c["TIPO"] for c in p["a_cargar"]], ["GRUPOS"])                                # FITS ya está, GRUPOS no
        p = R.armar_plan(filas, PROV, en_hoja={("BUE", "1SANC9", "GU", "CT3ID"): "OK"})                 # fila anterior a la columna TIPO
        self.assertEqual((len(p["a_cargar"]), len(p["descartadas"])), (0, 2))

    def test_excluye_por_nombre_aunque_el_codigo_sea_otro(self):
        csv = (CAB + "\nOT,XYZ1,29-Oct-2026,27-May-2026,GU,BUE,CT3ID,B x2,EAFI9,OK,2,1,0,0,0,A,V,V,,Guía POR ASIGNAR\n").encode("utf-8")
        p = R.armar_plan(R.leer_reporte(csv), PROV)
        self.assertEqual((p["resumen"]["Líneas excluidas (proveedor por asignar)"], len(p["a_cargar"])), (1, 0))

    def test_orden_por_location_service_supplier_code(self):
        filas = R.leer_reporte(CSV)
        orden = [(c["SUPPLIER"], c["CODIGO"]) for c in R.armar_plan(filas, PROV)["a_cargar"]]
        self.assertEqual(orden, sorted(orden, key=lambda t: (t[0], t[1])))

    def test_filas_para_la_hoja_y_tiempo(self):
        p = self.plan()
        hoja = R.filas_para_hoja(p["a_cargar"])
        self.assertEqual(hoja[0], {"LOCATION": "BUE", "SUPPLIER": "1SANC9", "SERVICE TYPE": "GU", "CODIGO": "CT3ID", "TIPO": "FITS",
                           "ESTADO": "PENDIENTE"})
        # 75 fijos + (22 + 1.5*3 + 12*1)
        self.assertEqual(p["resumen"]["Segundos estimados"], int(75 + 22 + 4.5 + 12))
        self.assertEqual(R.segundos_estimados([]), 0)
        self.assertEqual((R.texto_duracion(90), R.texto_duracion(9000)), ("2 min", "2 h 30 min"))

    @unittest.skipUnless(os.path.exists(REAL), "falta el reporte real de ejemplo")
    def test_reporte_real_de_ejemplo(self):
        p = R.armar_plan(R.leer_reporte(open(REAL, "rb").read()), PROV)
        r = p["resumen"]
        self.assertEqual((r["Líneas del reporte"], r["Líneas excluidas (servicio cancelado)"]), (1595, 202))
        self.assertEqual(r["Líneas excluidas (proveedor por asignar)"], 628)
        self.assertEqual((r["Combinaciones a cargar"], r["Reservas a cargar"]), (311, 737))       # una fila por combinación y TIPO (ver el recuento por tipo abajo); la corrida real del 27/10 procesó 276 combinaciones
        self.assertEqual(r["Líneas de Department sin clasificar"], 8)                              # las de EAPR (Department PR)
        self.assertEqual(sorted({c["TIPO"] for c in p["a_cargar"]}), ["FITS", "GRUPOS"])
        self.assertEqual((sum(c["TIPO"] == "FITS" for c in p["a_cargar"]), sum(c["TIPO"] == "GRUPOS" for c in p["a_cargar"])), (148, 163))
        self.assertTrue(all(c["SUPPLIER"] != "GUIAPO" for c in p["a_cargar"]))
        self.assertEqual(r["Desde"].strftime("%d/%m/%Y"), "28/10/2026")


if __name__ == "__main__":
    unittest.main()
