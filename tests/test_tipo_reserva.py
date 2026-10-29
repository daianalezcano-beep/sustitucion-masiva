"""Pruebas de la clasificación de reservas en FITS / GRUPOS por Department (common/tipo_reserva.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import tipo_reserva as T


class TipoReserva(unittest.TestCase):
    def test_department_de_la_reference(self):
        self.assertEqual([T.departamento_de(r) for r in ("EAGP246283", "TDGR1234", "eafi9", "EA", "", None)], ["GP", "GR", "FI", "", "", ""])

    def test_clasificacion_segun_la_tabla_de_tourplan(self):
        for ref in ("EAFI1", "EAFT1", "EAMI1", "EAFM1", "EAPE1", "EAPI1", "EAPU1", "EAOP1"):
            self.assertEqual(T.tipo_de_reserva(ref), "FITS", ref)
        for ref in ("EAGP1", "EAGR1", "TDGR1", "EAIN1", "EAFG1"):           # FG: se desprende de un grupo, se trabaja desde grupos
            self.assertEqual(T.tipo_de_reserva(ref), "GRUPOS", ref)
        # marcados para ambos (BO CF FL PR SL US) o para ninguno (vacío AC RD): sin clasificar
        for ref in ("EAPR1", "EABO1", "TDSL1", "TDUS1", "EACF1", "EAFL1", "EAAC1", "EARD1", "XX"):
            self.assertIsNone(T.tipo_de_reserva(ref), ref)

    def test_las_tablas_no_se_pisan_ni_se_olvida_ninguno(self):
        self.assertFalse(T.DEPARTAMENTOS_FITS & T.DEPARTAMENTOS_GRUPOS)
        self.assertFalse((T.DEPARTAMENTOS_FITS | T.DEPARTAMENTOS_GRUPOS) & T.DEPARTAMENTOS_SIN_CLASIFICAR)
        self.assertEqual(len(T.DEPARTAMENTOS_FITS | T.DEPARTAMENTOS_GRUPOS | T.DEPARTAMENTOS_SIN_CLASIFICAR), 21)   # los 21 de la lista de Tourplan

    def test_normalizar_el_valor_de_la_columna_tipo(self):
        self.assertEqual([T.normalizar_tipo(v) for v in ("FITS", "fit", " Grupos ", "GROUP", "individual", "", None)],
                         ["FITS", "FITS", "GRUPOS", "GRUPOS", "FITS", "", ""])
        self.assertIsNone(T.normalizar_tipo("RARO"))

    def test_departments_a_tildar_por_tipo_de_fila(self):
        self.assertEqual(T.departamentos_para("FITS"), T.DEPARTAMENTOS_FITS)
        self.assertEqual(T.departamentos_para("GRUPOS"), T.DEPARTAMENTOS_GRUPOS)
        self.assertEqual(T.departamentos_para(""), T.DEPARTAMENTOS_FITS | T.DEPARTAMENTOS_GRUPOS)     # nunca los sin clasificar
        self.assertFalse(T.departamentos_para("") & T.DEPARTAMENTOS_SIN_CLASIFICAR)

    def test_que_filas_procesa_cada_modo(self):
        self.assertEqual([(t, T.fila_corresponde(t, "FITS")) for t in ("FITS", "GRUPOS", "")], [("FITS", True), ("GRUPOS", False), ("", False)])
        self.assertEqual([(t, T.fila_corresponde(t, "GRUPOS")) for t in ("FITS", "GRUPOS", "")], [("FITS", False), ("GRUPOS", True), ("", False)])
        self.assertTrue(all(T.fila_corresponde(t, "AMBOS") for t in ("FITS", "GRUPOS", "")))


if __name__ == "__main__":
    unittest.main()
