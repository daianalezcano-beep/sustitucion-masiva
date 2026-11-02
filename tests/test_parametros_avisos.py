"""Pruebas de las fechas de los servicios en el Sheet (PARAMETROS), los avisos de fin de corrida, el tiempo restante y el glosario."""
import os
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import avisos, glosario
from common import ejecucion as E
from common import parametros_sheet as P


class Hoja:
    def __init__(self, titulo, valores):
        self.title, self.valores, self.escrituras = titulo, valores, []

    def get_all_values(self):
        return self.valores

    def update(self, range_name=None, values=None, value_input_option=None):
        self.escrituras.append((range_name, values, value_input_option))


class Libro:
    def __init__(self, hojas):
        self.hojas = hojas

    def worksheets(self):
        return self.hojas

    def add_worksheet(self, title, rows, cols):
        h = Hoja(title, [])
        self.hojas.append(h)
        return h


class WS:
    def __init__(self, libro):
        self.spreadsheet = libro


class Parametros(unittest.TestCase):
    def test_lee_las_dos_fechas_de_la_hoja(self):
        d, h = P.leer_rango([["DESDE", "1/12/2026"], ["HASTA", "31/12/2026"], ["", "nota"]])
        self.assertEqual((d, h), (datetime(2026, 12, 1), datetime(2026, 12, 31)))
        self.assertEqual(P.leer_rango([["DESDE", ""], ["HASTA", "algo"]]), (None, None))
        self.assertEqual(P.leer_rango([]), (None, None))

    def test_rango_del_plan_es_el_primer_y_el_ultimo_servicio(self):
        plan = [{"DESDE": "05/12/2026", "HASTA": "10/12/2026"}, {"DESDE": "01/12/2026", "HASTA": "28/12/2026"}, {"DESDE": "", "HASTA": ""}]
        self.assertEqual(P.rango_del_plan(plan), (datetime(2026, 12, 1), datetime(2026, 12, 28)))
        self.assertEqual(P.rango_del_plan([]), (None, None))

    def test_sin_pendientes_el_reporte_reemplaza_y_con_pendientes_se_amplia(self):
        viejo = (datetime(2026, 12, 1), datetime(2026, 12, 31))
        nuevo = (datetime(2026, 11, 20), datetime(2026, 12, 15))
        self.assertEqual(P.fusionar(viejo, nuevo, hay_pendientes=False), nuevo)
        self.assertEqual(P.fusionar(viejo, nuevo, hay_pendientes=True), (datetime(2026, 11, 20), datetime(2026, 12, 31)))
        self.assertEqual(P.fusionar((None, None), nuevo, hay_pendientes=True), nuevo)

    def test_por_defecto_es_de_manana_en_adelante(self):
        self.assertEqual(P.rango_por_defecto(datetime(2026, 11, 2, 15, 30)), (datetime(2026, 11, 3), datetime(2049, 12, 31)))

    def test_leer_y_escribir_en_el_sheet(self):
        libro = Libro([Hoja("SUBSTITUTE", [])])
        ws = WS(libro)
        self.assertEqual(P.leer_del_sheet(ws), (None, None))                       # sin pestaña PARAMETROS
        P.escribir_en_el_sheet(ws, datetime(2026, 12, 1), datetime(2026, 12, 31))   # la crea
        hoja = next(h for h in libro.hojas if h.title == "PARAMETROS")
        self.assertEqual(hoja.escrituras[-1], ("B1:B2", [["01/12/2026"], ["31/12/2026"]], "RAW"))   # como texto: el idioma del Sheet no las toca
        hoja.valores = [["DESDE", "01/12/2026"], ["HASTA", "31/12/2026"]]
        self.assertEqual(P.leer_del_sheet(ws), (datetime(2026, 12, 1), datetime(2026, 12, 31)))
        self.assertEqual(P.leer_del_sheet(object()), (None, None))                  # un Sheet que falla no rompe nada


class Avisos(unittest.TestCase):
    def test_nunca_lanza_y_dice_que_pudo(self):
        with mock.patch.object(avisos, "_notificar_escritorio", side_effect=RuntimeError("sin notificaciones")), \
             mock.patch.object(avisos, "_sonar", side_effect=OSError("sin sonido")):
            self.assertEqual(avisos.avisar("t", "m"), {"escritorio": False, "sonido": False})
        with mock.patch.object(avisos, "_notificar_escritorio") as n, mock.patch.object(avisos, "_sonar") as s:
            self.assertEqual(avisos.avisar("t", "m"), {"escritorio": True, "sonido": True})
            n.assert_called_once()
            s.assert_called_once()
            self.assertEqual(avisos.avisar("t", "m", escritorio=False, sonido=False), {"escritorio": False, "sonido": False})


class TiempoRestante(unittest.TestCase):
    def test_estimacion_por_promedio_y_minimo_de_combinaciones(self):
        self.assertIsNone(E.estimar_restante(1000, 2, 10, 1100))                    # con menos de 3 terminadas todavía no se estima
        self.assertIsNone(E.estimar_restante(None, 5, 10, 1100))
        self.assertIsNone(E.estimar_restante(1000, 5, 0, 1100))
        self.assertEqual(E.estimar_restante(1000, 4, 10, 1120), 180)                # 30 s por combinación, faltan 6
        self.assertEqual(E.estimar_restante(1000, 10, 10, 1300), 0)


class Glosario(unittest.TestCase):
    def test_explica_cada_contador_y_los_estados_que_detienen_la_corrida(self):
        self.assertEqual(list(glosario.COLA), ["Pendiente", "OK", "Error", "Duplicado", "Otro"])
        tabla = glosario.markdown_estados()
        for estado in ("ERROR_COLGADAS", "ERROR_REVISAR", "DUPLICADO", "DESCARTADA", "ERROR: no sustituida"):
            self.assertIn(estado, tabla)
        self.assertTrue(all(m and h for _, m, h in glosario.ESTADOS_SHEET))          # todos con significado y qué hacer
        self.assertIn("Venta modificada", glosario.markdown_comparacion())


if __name__ == "__main__":
    unittest.main()
