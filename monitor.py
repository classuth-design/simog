import glob
from datetime import datetime
import time
from gpiozero import DistanceSensor
import cv2
import requests

# Configuración de conexión con Render (Reemplaza con tu URL real)
API_URL = "https://tu-app-en-render.onrender.com/api/telemetria"
API_ERROR_URL = "https://tu-app-en-render.onrender.com/api/error"
GENERADOR_ID = 1  # ID del generador registrado en tu base de datos

# --- Configuración DS18B20 (Temperatura) ---
base_dir = "/sys/bus/w1/devices/"
device_folder = glob.glob(base_dir + "28*")[0]
device_file = device_folder + "/w1_slave"


def leer_temperatura():
  with open(device_file, "r") as f:
    lineas = f.readlines()
  if lineas[0].strip()[-3:] != "YES":
    return None
  pos = lineas[1].find("t=")
  if pos != -1:
    return float(lineas[1][pos + 2 :]) / 1000.0
  return None


# --- Configuración HC-SR04 (Nivel de Combustible via Distancia) ---
sensor = DistanceSensor(echo=18, trigger=23, max_distance=4)


def leer_combustible():
  dist_m = sensor.distance
  if dist_m >= sensor.max_distance - 0.01:
    return None
  # Asumiendo una altura total del tanque de 100 cm
  altura_tanque_cm = 100.0
  nivel_cm = dist_m * 100
  porcentaje = max(
      0.0, min(100.0, ((altura_tanque_cm - nivel_cm) / altura_tanque_cm) * 100)
  )
  return porcentaje


# --- Captura de Cámara USB (Controladora) ---
def capturar_controladora():
  cap = cv2.VideoCapture(0)
  if not cap.isOpened():
    return None

  ret, frame = cap.read()
  cap.release()

  if not ret:
    return None

  # Aquí puedes integrar la lógica de análisis de imagen u OCR (ej. detectar códigos "Err")
  # Ejemplo simulado:
  error_detectado = None
  return error_detectado


# --- Loop Principal de Monitoreo ---
try:
  print("Iniciando envío de telemetría hacia Render...")
  while True:
    temp = leer_temperatura()
    combustible = leer_combustible()
    error = capturar_controladora()

    payload = {
        "generador_id": GENERADOR_ID,
        "temperatura": temp,
        "nivel_combustible": combustible,
    }

    try:
      # Enviar datos al backend
      response = requests.post(API_URL, json=payload, timeout=5)
      print(
          f"[{datetime.now().strftime('%H:%M:%S')}] Telemetría enviada -"
          f" Temp: {temp} °C | Combustible: {combustible}% | Status:"
          f" {response.status_code}"
      )

      # Si la cámara detecta un fallo en la controladora
      if error:
        error_payload = {
            "generador_id": GENERADOR_ID,
            "evento": error[0],
            "tipo_error": error[1],
        }
        requests.post(API_ERROR_URL, json=error_payload, timeout=5)
        print(f"¡Alerta de error enviada!: {error[0]}")

    except Exception as e:
      print(f"Error de comunicación con Render: {e}")

    time.sleep(5)

except KeyboardInterrupt:
  print("\nMonitoreo detenido por el usuario.")
  sensor.close()
 
