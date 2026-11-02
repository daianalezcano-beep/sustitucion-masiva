import unittest

from common import resultado as R

LOG = [
    "[10:00:00] Filas PENDIENTE a procesar: 4\n",
    "──────\n","[10:00:01] Fila 2: BUE/1ACOS1/GU/AEEZID\n",
    "[10:02:00]   ✅ OK: 8 reserva(s) | verificado\n",
    "[10:02:01] Fila 3: BUE/1ACOS1/GU/AEHTID [FITS]\n",
    "[10:03:00]   ⚠ ERROR_REVISAR: [OT] columnas distintas: x: y\n",
    "[10:03:01] Fila 4: A/B/C/D\n",
    "[10:03:02]   ❌ ERROR: provisorio en uso: El provisorio tiene 2 reservas\n",
    "[10:03:03] Fila 5: E/F/G/H\n",
    "[10:03:04]   ❌ ERROR: Traceback (most recent call last)\n",
    "[10:04:00] 📋 Excluidas por costo manualizado (revisar a mano): 3 línea(s)\n",
    "[10:04:01] 🛑 CORRIDA DETENIDA: puede haber reservas\n",
    "[10:04:02] 🏁 Fin. Duración: 4m 05s · filas OK=1 ERROR=3 · reservas leídas=8\n",
]


class TestResultado(unittest.TestCase):
    def test_resumir_corrida(self):
        r = R.resumir_corrida(LOG)
        self.assertEqual([x["clave"] for x in r["combinaciones"]], ["OK", "REVISAR", "PROVISORIO_EN_USO", "OTRO"])
        self.assertEqual(r["combinaciones"][1]["nombre"], "BUE/1ACOS1/GU/AEHTID [FITS]")
        self.assertEqual(r["excluidas"], 3)
        self.assertTrue(r["detenida"])
        self.assertEqual(r["duracion"], "4m 05s")

    def test_lectura(self):
        r = R.resumir_corrida(["Fila 2: A/B/C/D\n", "  ✅ Leída: 12 reserva(s)\n"])
        self.assertEqual((r["combinaciones"][0]["clave"], r["combinaciones"][0]["reservas"]), ("LEIDA", 12))

    def test_a_medio_camino(self):
        self.assertTrue(R.resumir_corrida(["⛔ SUBSTITUTE_DETALLE tiene reservas a mitad de camino (FASE IDA_EN_CURSO)\n"])["a_medio_camino"])

    def test_explicar(self):
        for clave in ("COLGADAS", "REVISAR", "PROVISORIO_EN_USO", "NO_SUSTITUIDA", "EXCLUIDA", "OTRO"):
            self.assertEqual(len(R.explicar(clave)), 3)
        self.assertIsNone(R.explicar("OK"))

    def test_tiempo_estimado(self):
        logs = ["🏁 Fin. Duración: 10m 00s · filas OK=5 ERROR=0 · x", "🏁 Fin. Duración: 0m 10s · filas OK=1 ERROR=0 · x"]   # la segunda es muy corta: no cuenta
        self.assertEqual(R.ritmo_por_combinacion(logs), 120)
        self.assertEqual(R.tiempo_estimado(logs, 3), 360)
        self.assertIsNone(R.tiempo_estimado([], 3))


if __name__ == "__main__":
    unittest.main()
