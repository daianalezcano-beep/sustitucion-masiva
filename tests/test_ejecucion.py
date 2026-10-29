"""Pruebas de common/ejecucion.py: validación de parámetros, entorno del subproceso, conteo de la cola y
protocolo de confirmación por archivos (con un hilo que hace de "la app")."""
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import ejecucion as E
from common import logica as L


def params(**kw):
    p = {"usuario": "u", "password": "p", "sheet_url": "https://docs.google.com/x", "base_url": "https://t.example/tp",
         "modo": "lectura", "desde": date(2026, 11, 1), "hasta": date(2049, 12, 31), "fila": 0,
         "confirmar_ok": True, "pudo_service_types": ""}
    p.update(kw)
    return p


class Parametros(unittest.TestCase):
    def test_validos(self):
        self.assertEqual(E.validar_parametros(params()), [])
        self.assertEqual(E.validar_parametros(params(modo="aplicar", pre_contenido=b"x")), [])

    def test_aplicar_exige_el_reporte_pre(self):
        errores = E.validar_parametros(params(modo="aplicar"))
        self.assertEqual(len(errores), 1)
        self.assertIn("PRE", errores[0])
        self.assertEqual(E.validar_parametros(params(modo="lectura")), [])        # en lectura no hace falta

    def test_faltantes_y_rango(self):
        self.assertEqual(len(E.validar_parametros(params(usuario="", password=" ", sheet_url=""))), 3)
        self.assertTrue(any("anterior" in e for e in E.validar_parametros(params(hasta=date(2026, 10, 1)))))
        self.assertTrue(any("Desde" in e for e in E.validar_parametros(params(desde=None))))
        self.assertTrue(any("2000" in e for e in E.validar_parametros(params(hasta=date(2100, 1, 1)))))

    def test_advertencia_fecha_pasada(self):
        self.assertEqual(E.advertencias(params(desde=date(2026, 10, 1)), hoy=date(2026, 10, 19)),
                         ["La fecha Desde es anterior a hoy: se incluyen servicios pasados."])
        self.assertEqual(E.advertencias(params(), hoy=date(2026, 10, 19)), [])

    def test_entorno(self):
        env = E.armar_entorno(params(modo="aplicar", fila=7, pudo_service_types=" co, tr;ch "), {"PATH": "x"},
                              Path("/repo"), Path("/run"), Path("/repo/salida"), Path("/run/ABORTAR.flag"),
                              "/cfg/credentials.json", "/cfg/token.json")
        self.assertEqual((env["TOURPLAN_FECHA_DESDE"], env["TOURPLAN_FECHA_HASTA"]), ("01/11/26", "31/12/49"))
        self.assertEqual((env["TOURPLAN_MODO"], env["TOURPLAN_FILA"], env["TOURPLAN_PUDO_SERVICE_TYPES"]), ("aplicar", "7", "CO,TR,CH"))
        self.assertEqual(env["TOURPLAN_CONFIRMAR_OK"], "1")
        self.assertEqual(env["TOURPLAN_CONFIRM_DIR"], str(Path("/run")))
        self.assertEqual(env["PATH"], "x")
        sin_fila = E.armar_entorno(params(), {}, Path("/r"), Path("/d"), Path("/s"), Path("/f"), "c", "t")
        self.assertEqual(sin_fila["TOURPLAN_FILA"], "")
        # 31/12/49 es la fecha que Tourplan interpreta como 2049
        self.assertEqual(L.parsear_fecha(env["TOURPLAN_FECHA_HASTA"]).year, 2049)

    def test_estados(self):
        filas = [{"ESTADO": "PENDIENTE"}, {"ESTADO": " pendiente "}, {"ESTADO": "OK"}, {"ESTADO": "ERROR_COLGADAS"},
                 {"ESTADO": "ERROR: no soportado"}, {"ESTADO": "ERROR_REVISAR"}, {"ESTADO": "DUPLICADO"}, {"ESTADO": ""}]
        self.assertEqual(E.contar_estados(filas), {"Pendiente": 2, "OK": 1, "Error": 3, "Duplicado": 1, "Otro": 1})


class Confirmacion(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def responder_en_hilo(self, si, demora=0.3):
        def hacer():
            for _ in range(100):
                texto = E.confirmacion_pendiente(self.dir)
                if texto is not None:
                    self.visto = texto
                    time.sleep(demora)
                    E.responder_confirmacion(self.dir, si)
                    return
                time.sleep(0.05)
        h = threading.Thread(target=hacer)
        h.start()
        return h

    def test_si(self):
        h = self.responder_en_hilo(True)
        self.assertTrue(E.pedir_confirmacion(self.dir, "IDA: mover 2 reservas", intervalo=0.05))
        h.join()
        self.assertEqual(self.visto, "IDA: mover 2 reservas")
        self.assertEqual(os.listdir(self.dir), [])            # no quedan archivos

    def test_no(self):
        h = self.responder_en_hilo(False)
        self.assertFalse(E.pedir_confirmacion(self.dir, "x", intervalo=0.05))
        h.join()

    def test_timeout_es_no(self):
        self.assertFalse(E.pedir_confirmacion(self.dir, "x", timeout=0.3, intervalo=0.05))
        self.assertIsNone(E.confirmacion_pendiente(self.dir))

    def test_abort_cancela(self):
        self.assertFalse(E.pedir_confirmacion(self.dir, "x", timeout=5, intervalo=0.05, cancelar=lambda: True))

    def test_una_respuesta_vieja_no_cuenta(self):
        E.responder_confirmacion(self.dir, True)               # respuesta de una pregunta anterior
        self.assertFalse(E.pedir_confirmacion(self.dir, "x", timeout=0.3, intervalo=0.05))

    def test_respuesta_invalida_se_ignora(self):
        def escribir_basura_y_despues_si():
            time.sleep(0.2)
            (Path(self.dir) / E.ARCHIVO_RESPUESTA).write_text("tal vez", encoding="utf-8")
            time.sleep(0.2)
            E.responder_confirmacion(self.dir, True)
        h = threading.Thread(target=escribir_basura_y_despues_si)
        h.start()
        self.assertTrue(E.pedir_confirmacion(self.dir, "x", timeout=5, intervalo=0.05))
        h.join()


if __name__ == "__main__":
    unittest.main()
