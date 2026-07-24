import os
import smtplib
from email.message import EmailMessage
from flask import Flask, jsonify, render_template, request
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# Configuración de la base de datos (PostgreSQL en Render o local)
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL", "sqlite:///scada.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


# Modelo de la base de datos para almacenar registros del SCADA
class RegistroSCADA(db.Model):
  __tablename__ = "registros"
  id = db.Column(db.Integer, primary_key=True)
  temperatura = db.Column(db.Float, nullable=False)
  nivel_combustible = db.Column(db.Float, nullable=False)
  timestamp = db.Column(db.DateTime, server_default=db.func.now())


with app.app_context():
  db.create_all()

# Configuración de Correo para Alertas
EMAIL_ORIGEN = os.getenv("EMAIL_ORIGEN", "classuth@gmail.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "fgzqiyohjeasdvsr")
EMAIL_DESTINO = "classuth@gmail.com"


def enviar_alerta_correo(tipo_alerta, valor):
  try:
    msg = EmailMessage()
    if tipo_alerta == "combustible":
      msg["Subject"] = "🚨 ALERTA CRÍTICA: Nivel Muy Bajo de Combustible"
      cuerpo = (
          "Atención Administrador,\n\nEl sistema SCADA ha detectado un nivel muy"
          f" bajo de combustible en el Generador Principal.\nPorcentaje"
          f" actual: {valor}%\n\nPor favor, proceda a abastecer el depósito"
          " inmediatamente."
      )
    elif tipo_alerta == "temperatura":
      msg["Subject"] = "🔥 ALERTA CRÍTICA: Sobrecalentamiento de Motor"
      cuerpo = (
          "Atención Administrador,\n\nEl sistema SCADA ha detectado una"
          " condición de sobrecalentamiento en el Motor"
          f" Cummins.\nTemperatura actual: {valor}°C\n\nVerifique el sistema"
          " de enfriamiento de inmediato."
      )

    msg["From"] = EMAIL_ORIGEN
    msg["To"] = EMAIL_DESTINO
    msg.set_content(cuerpo)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
      smtp.login(EMAIL_ORIGEN, EMAIL_PASSWORD)
      smtp.send_message(msg)
    print(f"Correo de alerta ({tipo_alerta}) enviado a {EMAIL_DESTINO}.")
  except Exception as e:
    print(f"Error al enviar el correo de alerta: {e}")


# Ruta principal que sirve el panel web (index.html)
@app.route("/")
def index():
  return render_template("index.html")


# Ruta para recibir los datos enviados por monitor.py desde la Raspberry Pi
@app.route("/api/datos", methods=["POST"])
def recibir_datos():
  data = request.json
  if not data:
    return jsonify({"error": "No se recibieron datos JSON"}), 400

  temperatura = data.get("temperatura")
  combustible = data.get("nivel_combustible")

  if temperatura is None or combustible is None:
    return jsonify({"error": "Faltan parámetros de temperatura o combustible"}), 400

  # Validaciones para disparo automático de correos electrónicos
  if combustible <= 20.0:
    enviar_alerta_correo("combustible", combustible)

  if temperatura > 37.0:
    enviar_alerta_correo("temperatura", temperatura)

  # Guardar el registro en la base de datos
  nuevo_registro = RegistroSCADA(
      temperatura=temperatura, nivel_combustible=combustible
  )
  db.session.add(nuevo_registro)
  db.session.commit()

  return jsonify({"status": "success", "mensaje": "Datos guardados"}), 201


# Ruta que consulta el último estado disponible para actualizar la interfaz web en tiempo real
@app.route("/api/estado/<int:id_equipo>", methods=["GET"])
def obtener_estado(id_equipo):
  # Buscamos el registro más reciente en la base de datos
  ultimo_registro = (
      RegistroSCADA.query.order_by(RegistroSCADA.id.desc()).first()
  )

  if not ultimo_registro:
    return jsonify({"error": "No hay registros disponibles"}), 404

  return jsonify({
      "temperatura": ultimo_registro.temperatura,
      "nivel_combustible": ultimo_registro.nivel_combustible,
      "timestamp": ultimo_registro.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
  })


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=5000, debug=True)
