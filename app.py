from datetime import datetime
import os
from flask import Flask, jsonify, render_template, request
import psycopg2

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
