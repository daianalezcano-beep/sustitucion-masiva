import os
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "scripts", "substitute_doble"))
sys.path.insert(0, RAIZ)


class TestEsperaSummary(unittest.TestCase):
    def test_la_espera_crece_con_las_reservas_y_tiene_tope(self):
        import substitute_doble as sd
        self.assertEqual(sd.segundos_espera_summary(0), 30)
        self.assertEqual(sd.segundos_espera_summary(None), 30)
        self.assertEqual(sd.segundos_espera_summary(7), 42)
        self.assertGreater(sd.segundos_espera_summary(47), 45)                 # el caso real que venció con 30 s (x1,5 = 45 s)
        self.assertEqual(sd.segundos_espera_summary(5000), 900)


if __name__ == "__main__":
    unittest.main()
