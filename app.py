from email.message import EmailMessage
import os
import smtplib
import threading
import time
from flask import Flask, jsonify, render_template, request
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# Configuración de la base de datos (PostgreSQL en Render u otra URI)
database_url = os.environ.get("DATABASE_URL", "sqlite:///telemetria.db")
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# Configuración de credenciales de correo
EMAIL_ORIGEN = os.environ.get("EMAIL_ORIGEN", "classuth@gmail.com")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "fgzqiyohjeasdvsr")
EMAIL_DESTINO = os.environ.get("EMAIL_DESTINO", "classuth@gmail.com")

# Diccionarios para registrar el último momento en que se envió un correo por categoría
ultimo_envio_correo = {"combustible": 0, "temperatura": 0}
TIEMPO_ESPERA_CORREO = 60  # 60 segundos de espera entre cada correo idéntico


# Modelo de la Base de Datos
class Telemetria(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    temperatura = db.Column(db.Float, nullable=False)
    combustible = db.Column(db.Float, nullable=False)
    conexion = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())


with app.app_context():
    db.create_all()


def _ejecutar_envio_correo(tipo_alerta, valor, origen, password, destino):
    """Función interna que se ejecuta en segundo plano para no congelar Flask"""
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

        msg["From"] = origen
        msg["To"] = destino
        msg.set_content(cuerpo)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(origen, password)
            smtp.send_message(msg)
        print(f"Correo de alerta ({tipo_alerta}) enviado exitosamente en segundo plano.")
    except Exception as e:
        print(f"Error al enviar el correo en segundo plano: {e}")


def enviar_alerta_correo(tipo_alerta, valor):
    tiempo_actual = time.time()
    if (
        tiempo_actual - ultimo_envio_correo[tipo_alerta]
        < TIEMPO_ESPERA_CORREO
    ):
        return

    # Actualizar la hora inmediatamente para respetar el cooldown
    ultimo_envio_correo[tipo_alerta] = tiempo_actual

    # Lanzar el envío de correo en un hilo separado (Evita el bloqueo de Flask y el Timeout)
    hilo = threading.Thread(
        target=_ejecutar_envio_correo,
        args=(
            tipo_alerta,
            valor,
            EMAIL_ORIGEN,
            EMAIL_PASSWORD,
            EMAIL_DESTINO,
        ),
    )
    hilo.daemon = True
    hilo.start()


@app.route("/api/datos", methods=["POST"])
def recibir_datos():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No se recibieron datos JSON"}), 400

    temperatura = data.get("temperatura")
    combustible = data.get("nivel_combustible")
    conexion = data.get("conexion", "Estable")

    if temperatura is None or combustible is None:
        return (
            jsonify({"error": "Faltan parámetros de temperatura o combustible"}),
            400,
        )

    try:
        nuevo_registro = Telemetria(
            temperatura=float(temperatura),
            combustible=float(combustible),
            conexion=str(conexion),
        )
        db.session.add(nuevo_registro)
        db.session.commit()

        # Evaluar umbrales y disparar los correos en segundo plano de forma independiente
        if float(combustible) <= 0.0:  # O ajusta a <= 20.0 si deseas que avise también en bajo nivel
            enviar_alerta_correo("combustible", combustible)

        if float(temperatura) > 37.0:
            enviar_alerta_correo("temperatura", temperatura)

        return (
            jsonify({
                "mensaje": "Datos recibidos y procesados correctamente",
                "status": 201,
            }),
            201,
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/error", methods=["POST"])
def recibir_error():
    # Endpoint auxiliar para evitar errores 404/500 si la Raspberry envía alertas
    data = request.get_json()
    return jsonify({"mensaje": "Error registrado correctamente"}), 200


@app.route("/api/estado/<int:generador_id>")
def obtener_estado(generador_id):
    ultimo = Telemetria.query.order_by(Telemetria.id.desc()).first()
    if not ultimo:
        return jsonify({"error": "No hay datos disponibles"}), 404

    return jsonify({
        "temperatura": ultimo.temperatura,
        "nivel_combustible": ultimo.combustible,
        "conexion": ultimo.conexion,
    })


@app.route("/")
def index():
    ultimo = Telemetria.query.order_by(Telemetria.id.desc()).first()
    return render_template("index.html", telemetria=ultimo)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
