"""Pruebas del comparador de reportes Product Analysis, con las líneas reales de los dos reportes de la corrida en test
(el 'antes' exportado con ';' y fechas '21-oct-26', el 'después' con ',' y '21-Oct-2026')."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import comparador as C

CAB = "Agent,AgentName,Supplier,SupplierName,Loc,Service,Option,Description, Description 2,Date,Pax,Child,Qty,Total,Rooms,Nights,Currency,Cost,Agent,Retail,Reference,BookingName,BookingNameAlias,Status,D.O.T,VoucherNo,Bkg Analysis 1,Bkg Analysis 2,Bkg Analysis 3,Bkg Analysis 4,Bkg Analysis 5,Bkg Analysis 6,Consultant,ConsultantName"
ANTES = (CAB.replace(",", ";") + "\n"
         "HOLB;Holbrook Travel;1FAGA1;Facio, Graciela (Guia);BUE;GU;AEHTID;TF AEP/HT;;21-oct-26;20;0;1;20;1;1;USD;75.97;99.30;99.30;EAGP246321;Road Scholar;;KK;21-oct-26;2,516,199;EN;;;;;;AQU;Maria\n"
         "RIV1;Riviera Travel;1FAGA1;Facio, Graciela (Guia);BUE;GU;AEHTID;TF AEP/HT;;28-oct-26;40;0;1;40;1;1;USD;83.92;138.35;172.94;EAGP246528;SOUTH;;KK;25-oct-26;2,525,164;EN;SE;;;;;LSR;Luciana\n"
         "RIV1;Riviera Travel;1FAGA1;Facio, Graciela (Guia);BUE;GU;DSID;Dinner Show;;26-oct-26;40;0;1;40;1;1;USD;115.38;179.86;224.83;EAGP246528;SOUTH;;KK;25-oct-26;2,525,150;EN;SE;;;;;LSR;Luciana\n"
         "RIV1;Riviera Travel;1FAGA1;Facio, Graciela (Guia);BUE;GU;CT4ID;City tour (4 hs);;26-oct-26;40;0;1;40;1;1;USD;115.38;164.99;206.24;EAGP246528;SOUTH;;KK;25-oct-26;2,525,146;EN;SE;;;;;LSR;Luciana\n")
DESPUES = (CAB + "\n"
           'HOLB,Holbrook Travel,1FAGA1,"Facio, Graciela (Guia)",BUE,GU,AEHTID,TF AEP/HT,,21-Oct-2026,20,0,1,20,1,1,USD,81.65,0.00,0.00,EAGP246321,Road Scholar,,KK,21-Oct-2026,"2,516,199",EN,,,,,,AQU,Maria\n'
           'RIV1,Riviera Travel,1FAGA1,"Facio, Graciela (Guia)",BUE,GU,AEHTID,TF AEP/HT,,28-Oct-2026,40,0,1,40,1,1,USD,81.65,0.00,0.00,EAGP246528,SOUTH,,KK,25-Oct-2026,"2,525,164",EN,SE,,,,,LSR,Luciana\n'
           'RIV1,Riviera Travel,1FAGA1,"Facio, Graciela (Guia)",BUE,GU,DSID,Dinner Show,,26-Oct-2026,40,0,1,40,1,1,USD,129.95,0.00,0.00,EAGP246528,SOUTH,,KK,25-Oct-2026,"2,525,150",EN,SE,,,,,LSR,Luciana\n'
           'RIV1,Riviera Travel,1FAGA1,"Facio, Graciela (Guia)",BUE,GU,CT4ID,City tour (4 hs),,26-Oct-2026,40,0,1,40,1,1,USD,129.95,0.00,0.00,EAGP246528,SOUTH,,KK,25-Oct-2026,"2,525,146",EN,SE,,,,,LSR,Luciana\n')


class Comparador(unittest.TestCase):
    def test_formatos_de_fecha_y_numero(self):
        self.assertEqual(C.fecha("21-oct-26"), C.fecha("21-Oct-2026"))
        self.assertEqual(C.fecha("01-ene-27").month, 1)
        self.assertIsNone(C.fecha("nada"))
        self.assertEqual((C.numero("75.97"), C.numero("75,97"), C.numero("1,234.50"), C.numero("1.234,50")), (75.97, 75.97, 1234.5, 1234.5))
        self.assertIsNone(C.numero(""))

    def test_lee_los_dos_formatos_y_la_columna_agent_repetida(self):
        a, d = C.leer_reporte(ANTES.encode("utf-8")), C.leer_reporte(DESPUES.encode("latin-1"))
        self.assertEqual((len(a), len(d)), (4, 4))
        self.assertEqual((a[0]["Agent"], a[0]["Agent (2)"], a[0]["Retail"]), ("HOLB", "99.30", "99.30"))
        self.assertEqual((d[0]["Agent"], d[0]["Agent (2)"], d[0]["Cost"]), ("HOLB", "0.00", "81.65"))
        self.assertIn("Description 2", a[0])                      # el encabezado ' Description 2' se normaliza

    def test_detecta_la_venta_perdida_y_los_costos(self):
        r = C.comparar(ANTES.encode("utf-8"), DESPUES.encode("utf-8"))
        self.assertEqual(r["resumen"]["Venta modificada"], 4)
        self.assertEqual(r["resumen"]["Líneas antes"], 4)
        self.assertEqual({x["Resultado"] for x in r["lineas"]}, {"Venta modificada"})
        dsid = next(x for x in r["lineas"] if x["Option"] == "DSID")
        self.assertEqual((dsid["Costo antes"], dsid["Costo después"], dsid["Δ Costo"], dsid["Δ %"]), (115.38, 129.95, 14.57, 12.6))
        self.assertEqual((dsid["Retail antes"], dsid["Retail después"], dsid["Agent antes"], dsid["Agent después"]), (224.83, 0.0, 179.86, 0.0))
        self.assertIn("venta", dsid["Detalle"])
        self.assertEqual((dsid["Δ Retail %"], dsid["Δ Agent %"], dsid["Moneda"]), (-100.0, -100.0, "USD"))
        self.assertEqual(r["variaciones_venta"][0], {"Δ Retail %": -100.0, "Moneda": "USD", "Líneas": 4})

    def test_variacion_comun_de_la_venta(self):
        # todas las líneas suben 5 %: típico de un cambio de tipo de cambio
        despues = DESPUES.replace("0.00,0.00,EAGP246321", "104.27,104.27,EAGP246321").replace("0.00,0.00,EAGP246528", "181.72,226.62,EAGP246528")
        r = C.comparar(ANTES.encode("utf-8"), despues.encode("utf-8"))
        self.assertTrue(r["variaciones_venta"])
        self.assertTrue(all("Líneas" in v and "Δ Retail %" in v for v in r["variaciones_venta"]))

    def test_grupo_de_control_con_lineas_no_tocadas(self):
        # la línea DSID (RIV1) se considera "no tocada": si su venta cambió, el control lo informa
        r = C.comparar(ANTES.encode("utf-8"), DESPUES.encode("utf-8"), no_tocada=lambda f: f.get("Option") == "DSID")
        self.assertEqual((r["resumen"]["Control: líneas no tocadas"], r["resumen"]["Control: con venta modificada"]), (1, 1))
        self.assertEqual([x["Option"] for x in r["lineas"] if x["Grupo"] == "No tocada"], ["DSID"])
        self.assertTrue(all("Supplier" in x for x in r["lineas"]))
        sin_control = C.comparar(ANTES.encode("utf-8"), DESPUES.encode("utf-8"))
        self.assertNotIn("Control: líneas no tocadas", sin_control["resumen"])

    def test_lineas_repetidas_se_numeran_y_traen_voucher(self):
        extra = "RIV1;Riviera Travel;1FAGA1;Facio, Graciela (Guia);BUE;GU;DSID;Dinner Show;;26-oct-26;40;0;1;40;1;1;USD;100.00;150.00;200.00;EAGP246528;SOUTH;;KK;25-oct-26;2,525,151;EN;SE;;;;;LSR;Luciana\n"
        r = C.comparar((ANTES + extra).encode("utf-8"), (DESPUES + extra.replace(";", ",").replace("2,525,151", '"2,525,151"')).encode("utf-8"))
        dsid = [x for x in r["lineas"] if x["Option"] == "DSID"]
        self.assertEqual(sorted(x["Línea con esta clave"] for x in dsid), ["1 de 2", "2 de 2"])
        self.assertEqual({x["Voucher"] for x in dsid}, {"2,525,150", "2,525,151"})
        self.assertEqual(next(x for x in r["lineas"] if x["Option"] == "CT4ID")["Línea con esta clave"], "única")
        self.assertIn("Pax", dsid[0])

    def test_lineas_repetidas_se_emparejan_por_voucher_aunque_cambie_el_orden(self):
        cab = CAB
        fila = lambda voucher, cost, agent, retail: (f"RIV1,Riviera Travel,1FAGA1,Facio,BUE,GU,DSID,Dinner Show,,26-Oct-2026,40,0,1,40,1,1,USD,{cost},{agent},{retail},"
                                                      f"EAGP1,SOUTH,,KK,25-Oct-2026,\"{voucher}\",EN,SE,,,,,LSR,Luciana\n")
        antes = cab + "\n" + fila("2,525,150", 100, 150, 200) + fila("2,525,151", 80, 120, 160)
        despues = cab + "\n" + fila("2,525,151", 90, 120, 160) + fila("2,525,150", 110, 150, 200)      # orden invertido
        r = C.comparar(antes.encode("utf-8"), despues.encode("utf-8"))
        self.assertEqual(r["resumen"]["Venta modificada"], 0)                                          # sin falsos cruces
        self.assertEqual({x["Voucher"]: x["Costo después"] for x in r["lineas"]}, {"2,525,150": 110.0, "2,525,151": 90.0})

    def test_si_cambia_el_pax_la_linea_se_compara_igual(self):
        antes = ANTES.encode("utf-8")
        despues = DESPUES.replace(",DSID,Dinner Show,,26-Oct-2026,40,0,1,40", ",DSID,Dinner Show,,26-Oct-2026,39,0,1,39").encode("utf-8")
        r = C.comparar(antes, despues)
        self.assertEqual(r["resumen"]["Solo en uno de los dos"], 0)
        dsid = next(x for x in r["lineas"] if x["Option"] == "DSID")
        self.assertIn("Pax: 40", dsid["Detalle"])

    def test_costo_cambia_y_venta_igual(self):
        despues = DESPUES.replace("81.65,0.00,0.00", "81.65,99.30,99.30", 1)             # la primera línea conserva la venta
        r = C.comparar(ANTES.encode("utf-8"), despues.encode("utf-8"))
        primera = next(x for x in r["lineas"] if x["Reference"] == "EAGP246321")
        self.assertEqual(primera["Resultado"], "Costo cambió")
        self.assertEqual(r["resumen"]["Venta modificada"], 3)
        self.assertEqual(r["lineas"][-1]["Resultado"], "Costo cambió")                    # ordenadas: venta primero, sin cambios al final

    def test_lineas_que_aparecen_en_un_solo_reporte(self):
        despues = "\n".join(DESPUES.splitlines()[:-1]) + "\n"
        r = C.comparar(ANTES.encode("utf-8"), despues.encode("utf-8"))
        self.assertEqual(r["resumen"]["Solo en uno de los dos"], 1)
        self.assertEqual(sum(1 for x in r["lineas"] if x["Resultado"] == "Solo antes"), 1)

    def test_otros_cambios_y_sin_cambios(self):
        import csv
        import io
        coma = io.StringIO()
        csv.writer(coma, lineterminator="\n").writerows(csv.reader(io.StringIO(ANTES), delimiter=";"))   # el mismo reporte, con ','
        igual = C.comparar(ANTES.encode("utf-8"), coma.getvalue().encode("utf-8"))
        self.assertEqual(igual["resumen"]["Sin cambios"], 4)
        cambia_moneda = ANTES.replace("USD;75.97", "EUR;75.97", 1)
        r = C.comparar(ANTES.encode("utf-8"), cambia_moneda.encode("utf-8"))
        self.assertEqual(r["resumen"]["Sin cambios"], 3)
        self.assertEqual(r["resumen"]["Otros cambios"], 1)

    def test_csv_de_salida(self):
        r = C.comparar(ANTES.encode("utf-8"), DESPUES.encode("utf-8"))
        salida = C.a_csv(r["lineas"])
        self.assertTrue(salida.startswith("﻿".encode("utf-8")))
        self.assertEqual(len(salida.decode("utf-8-sig").splitlines()), 5)                 # encabezado + 4 líneas
        self.assertEqual(C.a_csv([]), b"")


if __name__ == "__main__":
    unittest.main()
