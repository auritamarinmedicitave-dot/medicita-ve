from __future__ import annotations

import os
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SQLITE_URI = f"sqlite:///{BASE_DIR / 'instance' / 'medicita.db'}"

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Debes iniciar sesión para continuar."
login_manager.login_message_category = "warning"


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="patient", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    appointments = db.relationship("Appointment", backref="patient", lazy=True, foreign_keys="Appointment.user_id")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class Doctor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    specialty = db.Column(db.String(120), nullable=False, index=True)
    city = db.Column(db.String(80), nullable=False, index=True)
    clinic = db.Column(db.String(120), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    rating = db.Column(db.Float, nullable=False)
    experience = db.Column(db.String(40), nullable=False)
    about = db.Column(db.Text, nullable=False)
    next_slot = db.Column(db.String(40), nullable=False)
    modes = db.Column(db.String(120), nullable=False)
    slots = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    appointments = db.relationship("Appointment", backref="doctor", lazy=True)

    @property
    def mode_list(self) -> list[str]:
        return [m.strip() for m in self.modes.split(",") if m.strip()]

    @property
    def slot_list(self) -> list[str]:
        return [s.strip() for s in self.slots.split(",") if s.strip()]


class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=False, index=True)
    date_label = db.Column(db.String(30), nullable=False)
    time_label = db.Column(db.String(20), nullable=False)
    consultation_type = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="confirmada", index=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-in-production")
    database_url = os.getenv("DATABASE_URL", DEFAULT_SQLITE_URI)
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    login_manager.init_app(app)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    @login_manager.user_loader
    def load_user(user_id: str):
        return db.session.get(User, int(user_id))

    @app.context_processor
    def inject_globals():
        return {"brand": {"primary": "#1677FF", "secondary": "#10B7A6"}}

    def admin_required(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if not current_user.is_admin:
                flash("No tienes permisos para entrar al panel admin.", "danger")
                return redirect(url_for("index"))
            return view_func(*args, **kwargs)

        return wrapped

    @app.route("/")
    def index():
        if not current_user.is_authenticated:
            return redirect(url_for("login"))

        query = request.args.get("q", "").strip()
        city = request.args.get("city", "").strip()
        mode = request.args.get("mode", "Todos").strip() or "Todos"

        doctors_query = Doctor.query
        if query:
            like = f"%{query.lower()}%"
            doctors_query = doctors_query.filter(
                db.or_(
                    func.lower(Doctor.name).like(like),
                    func.lower(Doctor.specialty).like(like),
                )
            )
        if city:
            doctors_query = doctors_query.filter(func.lower(Doctor.city).like(f"%{city.lower()}%"))

        doctors = doctors_query.order_by(Doctor.rating.desc(), Doctor.name.asc()).all()
        if mode != "Todos":
            doctors = [d for d in doctors if mode in d.mode_list]

        featured_doctor = doctors[0] if doctors else Doctor.query.order_by(Doctor.rating.desc()).first()
        return render_template(
            "index.html",
            doctors=doctors,
            featured_doctor=featured_doctor,
            filters={"q": query, "city": city, "mode": mode},
            quick_specialties=["Psicología", "Pediatría", "Ginecología", "Medicina interna", "Dermatología", "Cardiología"],
        )

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("index"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")

            if not name or not email or not password:
                flash("Completa todos los campos.", "danger")
                return render_template("auth.html", mode="register")
            if User.query.filter_by(email=email).first():
                flash("Ese correo ya está registrado.", "danger")
                return render_template("auth.html", mode="register")

            user = User(name=name, email=email, role="patient")
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash("Cuenta creada correctamente.", "success")
            return redirect(url_for("index"))
        return render_template("auth.html", mode="register")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("index"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter_by(email=email).first()
            if not user or not user.check_password(password):
                flash("Correo o contraseña incorrectos.", "danger")
                return render_template("auth.html", mode="login")
            login_user(user)
            flash("Bienvenida a MediCita VE.", "success")
            return redirect(url_for("index"))
        return render_template("auth.html", mode="login")

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        logout_user()
        flash("Sesión cerrada.", "info")
        return redirect(url_for("login"))

    @app.route("/book/<int:doctor_id>", methods=["POST"])
    @login_required
    def book(doctor_id: int):
        doctor = Doctor.query.get_or_404(doctor_id)
        date_label = request.form.get("date_label", "Hoy")
        time_label = request.form.get("time_label", "").strip()
        consultation_type = request.form.get("consultation_type", "Online").strip()

        if not time_label:
            flash("Selecciona un horario.", "danger")
            return redirect(url_for("index"))
        if consultation_type not in doctor.mode_list:
            consultation_type = doctor.mode_list[0]

        appointment = Appointment(
            user_id=current_user.id,
            doctor_id=doctor.id,
            date_label=date_label,
            time_label=time_label,
            consultation_type=consultation_type,
            status="confirmada",
        )
        db.session.add(appointment)
        db.session.commit()
        flash("Cita reservada con éxito.", "success")
        return redirect(url_for("appointments"))

    @app.route("/appointments")
    @login_required
    def appointments():
        records = Appointment.query.filter_by(user_id=current_user.id).order_by(Appointment.created_at.desc()).all()
        return render_template("appointments.html", appointments=records)

    @app.route("/doctor/dashboard")
    @login_required
    def doctor_dashboard():
        today_schedule = [
            {"name": "María Fernanda Silva", "time": "09:00", "type": "Online"},
            {"name": "José Cabrera", "time": "11:30", "type": "Presencial"},
            {"name": "Paola Méndez", "time": "16:30", "type": "Online"},
        ]
        stats = {
            "consultas_hoy": Appointment.query.count(),
            "pacientes_nuevos": User.query.filter_by(role="patient").count(),
            "ocupacion": "87%",
            "medicos_activos": Doctor.query.count(),
        }
        return render_template("doctor_dashboard.html", stats=stats, today_schedule=today_schedule)

    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        stats = {
            "users": User.query.count(),
            "patients": User.query.filter_by(role="patient").count(),
            "admins": User.query.filter_by(role="admin").count(),
            "doctors": Doctor.query.count(),
            "appointments": Appointment.query.count(),
        }
        doctors = Doctor.query.order_by(Doctor.created_at.desc()).all()
        recent_appointments = Appointment.query.order_by(Appointment.created_at.desc()).limit(10).all()
        return render_template("admin_dashboard.html", stats=stats, doctors=doctors, recent_appointments=recent_appointments)

    @app.route("/admin/doctors/create", methods=["POST"])
    @admin_required
    def admin_create_doctor():
        doctor = Doctor(
            name=request.form.get("name", "").strip(),
            specialty=request.form.get("specialty", "").strip(),
            city=request.form.get("city", "").strip(),
            clinic=request.form.get("clinic", "").strip(),
            price=int(request.form.get("price", "0") or 0),
            rating=float(request.form.get("rating", "0") or 0),
            experience=request.form.get("experience", "").strip(),
            about=request.form.get("about", "").strip(),
            next_slot=request.form.get("next_slot", "").strip(),
            modes=request.form.get("modes", "Online,Presencial").strip(),
            slots=request.form.get("slots", "09:00,10:00,11:00").strip(),
        )
        if not all([doctor.name, doctor.specialty, doctor.city, doctor.clinic, doctor.about, doctor.next_slot, doctor.modes, doctor.slots]):
            flash("Completa todos los campos del médico.", "danger")
            return redirect(url_for("admin_dashboard"))
        db.session.add(doctor)
        db.session.commit()
        flash("Médico agregado correctamente.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/doctors/<int:doctor_id>/delete", methods=["POST"])
    @admin_required
    def admin_delete_doctor(doctor_id: int):
        doctor = Doctor.query.get_or_404(doctor_id)
        if doctor.appointments:
            flash("No puedes eliminar un médico con citas asociadas.", "warning")
            return redirect(url_for("admin_dashboard"))
        db.session.delete(doctor)
        db.session.commit()
        flash("Médico eliminado.", "info")
        return redirect(url_for("admin_dashboard"))

    @app.cli.command("seed")
    def seed_command():
        seed_database()
        print("Base de datos inicializada con datos de ejemplo.")

    def seed_database():
        db.create_all()

        if not User.query.filter_by(email="admin@medicitave.com").first():
            admin = User(name="Admin MediCita", email="admin@medicitave.com", role="admin")
            admin.set_password("Admin123*")
            db.session.add(admin)

        if Doctor.query.count() == 0:
            sample_doctors = [
                Doctor(
                    name="Dra. Valentina Rojas", specialty="Psicología clínica", city="Caracas",
                    clinic="Centro Médico Ávila", price=35, rating=4.9, experience="12 años",
                    about="Atención a adultos y adolescentes. Enfoque en ansiedad, estrés y regulación emocional.",
                    next_slot="Hoy · 16:30", modes="Online,Presencial", slots="09:00,10:30,12:00,16:30,18:00"
                ),
                Doctor(
                    name="Dr. Andrés Zerpa", specialty="Pediatría", city="Valencia",
                    clinic="Clínica La Viña", price=40, rating=4.8, experience="10 años",
                    about="Control pediátrico, infecciones respiratorias y seguimiento infantil.",
                    next_slot="Mañana · 09:00", modes="Presencial", slots="09:00,10:00,11:30,15:00"
                ),
                Doctor(
                    name="Dra. Mariana Bello", specialty="Ginecología", city="Maracaibo",
                    clinic="Policlínica Maracaibo", price=45, rating=4.9, experience="15 años",
                    about="Consulta ginecológica general, control y orientación integral de salud femenina.",
                    next_slot="Hoy · 18:00", modes="Online,Presencial", slots="08:30,11:00,14:30,18:00"
                ),
                Doctor(
                    name="Dr. Luis Herrera", specialty="Medicina interna", city="Barquisimeto",
                    clinic="Consulta privada", price=30, rating=4.7, experience="8 años",
                    about="Valoración general, seguimiento de patologías crónicas y segunda opinión.",
                    next_slot="Hoy · 14:00", modes="Online", slots="14:00,15:00,17:30,19:00"
                ),
            ]
            db.session.add_all(sample_doctors)

        db.session.commit()

    with app.app_context():
        seed_database()

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=True)
  @app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if email == "admin@medicitave.com" and password == "Admin123":
            return redirect("/admin")
        else:
            return "Correo o contraseña incorrectos"

    return render_template("auth.html")

@app.route("/admin")
def admin():
    return "Bienvenida al panel de administrador 🚀"
