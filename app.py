from datetime import datetime
import os
from flask import Flask, jsonify, render_template, request
import psycopg2
import smtplib
from email.message import EmailMessage

# Configura aquí tu cuenta de correo remitente (ej. una cuenta de Gmail con Contraseña de Aplicación)
EMAIL_ORIGEN = "tucorreo@gmail.com"
EMAIL_PASSWORD = "tu_contraseña_de_aplicacion"
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

    # Conexión al servidor SMTP de Gmail
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
      smtp.login(EMAIL_ORIGEN, EMAIL_PASSWORD)
      smtp.send_message(msg)
    print(f"Correo de alerta ({tipo_alerta}) enviado exitosamente.")
  except Exception as e:
    print(f"Error al enviar el correo de alerta: {e}")

app = Flask(__name__)


def get_db_connection():
  # Render provee la URL de la base de datos en la variable de entorno DATABASE_URL
  conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
  return conn


@app.route("/")
def index():
  return render_template("index.html")


@app.route("/api/telemetria", methods=["POST"])
def recibir_telemetria():
  data = request.json
  conn = get_db_connection()
  cur = conn.cursor()
  cur.execute(
      """
        INSERT INTO registros (generador_id, temperatura, nivel_combustible, fecha, hora)
        VALUES (%s, %s, %s, CURRENT_DATE, CURRENT_TIME)
    """,
      (
          data.get("generador_id"),
          data.get("temperatura"),
          data.get("nivel_combustible"),
      ),
  )
  conn.commit()
  cur.close()
  conn.close()
  return jsonify({"status": "success"}), 201


@app.route("/api/error", methods=["POST"])
def recibir_error():
  data = request.json
  conn = get_db_connection()
  cur = conn.cursor()
  cur.execute(
      """
        INSERT INTO registro_err (generador_id, evento, tipo_error, fecha, hora)
        VALUES (%s, %s, %s, CURRENT_DATE, CURRENT_TIME)
    """,
      (data.get("generador_id"), data.get("evento"), data.get("tipo_error")),
  )
  conn.commit()
  cur.close()
  conn.close()
  return jsonify({"status": "success"}), 201


@app.route("/api/estado/<int:gen_id>", methods=["GET"])
def obtener_estado(gen_id):
  conn = get_db_connection()
  cur = conn.cursor()
  cur.execute(
      """
        SELECT temperatura, nivel_combustible, fecha, hora 
        FROM registros WHERE generador_id = %s 
        ORDER BY id DESC LIMIT 1
    """,
      (gen_id,),
  )
  row = cur.fetchone()
  cur.close()
  conn.close()

  if row:
    return jsonify({
        "temperatura": float(row[0]) if row[0] else 0,
        "nivel_combustible": float(row[1]) if row[1] else 0,
        "fecha": str(row[2]),
        "hora": str(row[3]),
    })
  return jsonify({"error": "Sin datos"}), 404


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
