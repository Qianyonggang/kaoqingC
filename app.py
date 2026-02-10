import os
from datetime import date, datetime
from io import BytesIO

import pandas as pd
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, UniqueConstraint, and_, func
from werkzeug.security import check_password_hash, generate_password_hash

# =====================
# 基础配置
# =====================
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "attendance-dev-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///attendance.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# 员工与团队为多对多关系的中间表
team_members = db.Table(
    "team_members",
    db.Column("team_id", db.Integer, db.ForeignKey("team.id"), primary_key=True),
    db.Column("employee_id", db.Integer, db.ForeignKey("employee.id"), primary_key=True),
)


# =====================
# 数据模型
# =====================
class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    username = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_owner = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("company_id", "username", name="uq_company_username"),)

    company = db.relationship("Company", backref=db.backref("users", lazy=True))

    def set_password(self, raw_password: str):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)


class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    manager_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_company_team_name"),)

    company = db.relationship("Company", backref=db.backref("teams", lazy=True))
    manager = db.relationship("User", backref=db.backref("managed_teams", lazy=True))


class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(30), nullable=False)
    bank_account = db.Column(db.String(64), nullable=False)
    daily_salary = db.Column(db.Float, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    teams = db.relationship("Team", secondary=team_members, backref="members")
    company = db.relationship("Company", backref=db.backref("employees", lazy=True))


class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False)
    day_count = db.Column(db.Float, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("employee_id", "team_id", "work_date", name="uq_attendance_employee_team_date"),
        CheckConstraint("day_count IN (0, 0.5, 1)", name="check_day_count"),
    )

    employee = db.relationship("Employee", backref=db.backref("attendance_logs", lazy=True))
    team = db.relationship("Team", backref=db.backref("attendance_logs", lazy=True))


class Advance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    advance_date = db.Column(db.Date, nullable=False)
    note = db.Column(db.String(255), default="")
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship("Employee", backref=db.backref("advances", lazy=True))


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    operator_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    action = db.Column(db.String(80), nullable=False)
    detail = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    operator = db.relationship("User", backref=db.backref("logs", lazy=True))


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# =====================
# 权限与公共函数
# =====================
def log_action(action: str, detail: str):
    """记录操作日志，便于公司创建者审计。"""
    item = AuditLog(
        company_id=current_user.company_id,
        operator_id=current_user.id,
        action=action,
        detail=detail,
    )
    db.session.add(item)


def ensure_company_scope(obj):
    """确保所有数据操作都在当前公司范围内，防止跨公司访问。"""
    if obj.company_id != current_user.company_id:
        flash("无权访问该数据。", "danger")
        return False
    return True


def calculate_month_stat(employee_id: int, year: int, month: int):
    """计算某员工某月的工资统计。"""
    att_q = (
        db.session.query(func.coalesce(func.sum(Attendance.day_count), 0.0))
        .filter(
            Attendance.employee_id == employee_id,
            func.strftime("%Y", Attendance.work_date) == str(year),
            func.strftime("%m", Attendance.work_date) == f"{month:02d}",
        )
        .scalar()
    )
    employee = Employee.query.get(employee_id)
    advances = (
        db.session.query(func.coalesce(func.sum(Advance.amount), 0.0))
        .filter(
            Advance.employee_id == employee_id,
            func.strftime("%Y", Advance.advance_date) == str(year),
            func.strftime("%m", Advance.advance_date) == f"{month:02d}",
        )
        .scalar()
    )
    gross = round(att_q * employee.daily_salary, 2)
    remaining = round(gross - advances, 2)
    return round(att_q, 2), round(advances, 2), gross, remaining


# =====================
# 登录注册
# =====================
@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        company_name = request.form["company_name"].strip()
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        if Company.query.filter_by(name=company_name).first():
            flash("公司名已存在。", "danger")
            return redirect(url_for("register"))

        company = Company(name=company_name)
        db.session.add(company)
        db.session.flush()

        owner = User(company_id=company.id, username=username, is_owner=True, is_admin=True)
        owner.set_password(password)
        db.session.add(owner)
        db.session.commit()

        flash("注册成功，请登录。", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        company_name = request.form["company_name"].strip()
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        company = Company.query.filter_by(name=company_name).first()
        if not company:
            flash("公司不存在。", "danger")
            return redirect(url_for("login"))

        user = User.query.filter_by(company_id=company.id, username=username).first()
        if not user or not user.check_password(password):
            flash("账号或密码错误。", "danger")
            return redirect(url_for("login"))

        login_user(user)
        flash("登录成功。", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("已退出登录。", "info")
    return redirect(url_for("index"))


# =====================
# 业务页面
# =====================
@app.route("/dashboard")
@login_required
def dashboard():
    teams = Team.query.filter_by(company_id=current_user.company_id).all()
    employees = Employee.query.filter_by(company_id=current_user.company_id).all()
    return render_template("dashboard.html", teams=teams, employees=employees)


@app.route("/admins", methods=["GET", "POST"])
@login_required
def admins():
    if not current_user.is_owner:
        flash("只有公司创建者可以管理管理员。", "danger")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        if User.query.filter_by(company_id=current_user.company_id, username=username).first():
            flash("用户名已存在。", "danger")
            return redirect(url_for("admins"))

        admin = User(
            company_id=current_user.company_id,
            username=username,
            is_owner=False,
            is_admin=True,
        )
        admin.set_password(password)
        db.session.add(admin)
        log_action("create_admin", f"新增管理员：{username}")
        db.session.commit()
        flash("管理员创建成功。", "success")
        return redirect(url_for("admins"))

    data = User.query.filter_by(company_id=current_user.company_id, is_admin=True).all()
    return render_template("admins.html", admins=data)


@app.route("/teams", methods=["GET", "POST"])
@login_required
def teams():
    if not current_user.is_admin:
        flash("仅管理员可操作。", "danger")
        return redirect(url_for("dashboard"))

    admins_data = User.query.filter_by(company_id=current_user.company_id, is_admin=True).all()
    if request.method == "POST":
        name = request.form["name"].strip()
        manager_id = int(request.form["manager_id"])
        team = Team(company_id=current_user.company_id, name=name, manager_id=manager_id)
        db.session.add(team)
        log_action("create_team", f"新增团队：{name}")
        db.session.commit()
        flash("团队创建成功。", "success")
        return redirect(url_for("teams"))

    items = Team.query.filter_by(company_id=current_user.company_id).all()
    return render_template("teams.html", items=items, admins=admins_data)


@app.route("/employees", methods=["GET", "POST"])
@login_required
def employees():
    if not current_user.is_admin:
        flash("仅管理员可操作。", "danger")
        return redirect(url_for("dashboard"))

    company_teams = Team.query.filter_by(company_id=current_user.company_id).all()
    if request.method == "POST":
        name = request.form["name"].strip()
        phone = request.form["phone"].strip()
        bank_account = request.form["bank_account"].strip()
        daily_salary = float(request.form["daily_salary"])
        team_ids = request.form.getlist("team_ids")

        employee = Employee(
            company_id=current_user.company_id,
            name=name,
            phone=phone,
            bank_account=bank_account,
            daily_salary=daily_salary,
            created_by=current_user.id,
        )

        for tid in team_ids:
            team = Team.query.get(int(tid))
            if team and team.company_id == current_user.company_id:
                employee.teams.append(team)

        db.session.add(employee)
        log_action("create_employee", f"新增员工：{name}")
        db.session.commit()
        flash("员工新增成功。", "success")
        return redirect(url_for("employees"))

    items = Employee.query.filter_by(company_id=current_user.company_id).all()
    return render_template("employees.html", items=items, teams=company_teams)


@app.route("/employee/<int:employee_id>/assign_teams", methods=["POST"])
@login_required
def assign_teams(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    if not ensure_company_scope(employee):
        return redirect(url_for("employees"))

    employee.teams.clear()
    team_ids = request.form.getlist("team_ids")
    for tid in team_ids:
        team = Team.query.get(int(tid))
        if team and team.company_id == current_user.company_id:
            employee.teams.append(team)

    log_action("assign_employee_teams", f"员工 {employee.name} 更新团队归属")
    db.session.commit()
    flash("员工团队更新成功。", "success")
    return redirect(url_for("employees"))


@app.route("/attendance", methods=["GET", "POST"])
@login_required
def attendance():
    if not current_user.is_admin:
        flash("仅管理员可操作。", "danger")
        return redirect(url_for("dashboard"))

    teams_data = Team.query.filter_by(company_id=current_user.company_id).all()
    employees_data = Employee.query.filter_by(company_id=current_user.company_id).all()

    if request.method == "POST":
        employee_id = int(request.form["employee_id"])
        team_id = int(request.form["team_id"])
        work_date = datetime.strptime(request.form["work_date"], "%Y-%m-%d").date()
        day_count = float(request.form["day_count"])

        if work_date > date.today():
            flash("不能记录未来日期的考勤。", "danger")
            return redirect(url_for("attendance"))

        employee = Employee.query.get_or_404(employee_id)
        team = Team.query.get_or_404(team_id)
        if employee.company_id != current_user.company_id or team.company_id != current_user.company_id:
            flash("数据越权。", "danger")
            return redirect(url_for("attendance"))

        record = Attendance.query.filter_by(employee_id=employee_id, team_id=team_id, work_date=work_date).first()

        # 先计算该员工当天其它团队已记录工时，保证总和不超过1天
        other_sum = (
            db.session.query(func.coalesce(func.sum(Attendance.day_count), 0.0))
            .filter(
                Attendance.employee_id == employee_id,
                Attendance.work_date == work_date,
                Attendance.team_id != team_id,
            )
            .scalar()
        )

        if other_sum + day_count > 1.0:
            flash("该员工当天跨团队考勤总和不能超过1天。", "danger")
            return redirect(url_for("attendance"))

        if record:
            record.day_count = day_count
            record.created_by = current_user.id
            log_action(
                "update_attendance",
                f"更新考勤：{employee.name} {work_date} {day_count}天（{team.name}）",
            )
        else:
            record = Attendance(
                company_id=current_user.company_id,
                employee_id=employee_id,
                team_id=team_id,
                work_date=work_date,
                day_count=day_count,
                created_by=current_user.id,
            )
            db.session.add(record)
            log_action(
                "create_attendance",
                f"新增考勤：{employee.name} {work_date} {day_count}天（{team.name}）",
            )

        db.session.commit()
        flash("考勤记录已保存。", "success")
        return redirect(url_for("attendance"))

    logs = (
        Attendance.query.filter_by(company_id=current_user.company_id)
        .order_by(Attendance.work_date.desc())
        .limit(80)
        .all()
    )
    return render_template("attendance.html", teams=teams_data, employees=employees_data, logs=logs, today=date.today())


@app.route("/advances", methods=["GET", "POST"])
@login_required
def advances():
    if not current_user.is_admin:
        flash("仅管理员可操作。", "danger")
        return redirect(url_for("dashboard"))

    employees_data = Employee.query.filter_by(company_id=current_user.company_id).all()
    if request.method == "POST":
        employee_id = int(request.form["employee_id"])
        amount = float(request.form["amount"])
        advance_date = datetime.strptime(request.form["advance_date"], "%Y-%m-%d").date()
        note = request.form.get("note", "").strip()

        if advance_date > date.today():
            flash("不能记录未来日期的借支。", "danger")
            return redirect(url_for("advances"))

        employee = Employee.query.get_or_404(employee_id)
        if employee.company_id != current_user.company_id:
            flash("数据越权。", "danger")
            return redirect(url_for("advances"))

        item = Advance(
            company_id=current_user.company_id,
            employee_id=employee_id,
            amount=amount,
            advance_date=advance_date,
            note=note,
            created_by=current_user.id,
        )
        db.session.add(item)
        log_action("create_advance", f"借支：{employee.name} {amount}元")
        db.session.commit()
        flash("借支记录已保存。", "success")
        return redirect(url_for("advances"))

    items = (
        Advance.query.filter_by(company_id=current_user.company_id)
        .order_by(Advance.advance_date.desc())
        .limit(100)
        .all()
    )
    return render_template("advances.html", employees=employees_data, items=items, today=date.today())


@app.route("/payroll")
@login_required
def payroll():
    year = int(request.args.get("year", date.today().year))
    month = int(request.args.get("month", date.today().month))

    result = []
    employees_data = Employee.query.filter_by(company_id=current_user.company_id).all()
    for emp in employees_data:
        days, advances_amt, gross, remain = calculate_month_stat(emp.id, year, month)
        result.append(
            {
                "name": emp.name,
                "daily_salary": emp.daily_salary,
                "days": days,
                "advances": advances_amt,
                "gross": gross,
                "remain": remain,
            }
        )

    return render_template("payroll.html", rows=result, year=year, month=month)


@app.route("/export")
@login_required
def export_excel():
    if not current_user.is_owner:
        flash("仅公司创建者可导出。", "danger")
        return redirect(url_for("dashboard"))

    year = int(request.args.get("year", date.today().year))
    month = int(request.args.get("month", date.today().month))

    rows = []
    for team in Team.query.filter_by(company_id=current_user.company_id).all():
        for emp in team.members:
            days, advances_amt, gross, remain = calculate_month_stat(emp.id, year, month)
            rows.append(
                {
                    "团队": team.name,
                    "员工姓名": emp.name,
                    "联系方式": emp.phone,
                    "银行卡号": emp.bank_account,
                    "年份": year,
                    "月份": month,
                    "当月考勤天数": days,
                    "单日工资": emp.daily_salary,
                    "借支": advances_amt,
                    "总工资": gross,
                    "剩余工资": remain,
                }
            )

    df = pd.DataFrame(rows)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="工资统计")
    output.seek(0)

    log_action("export_excel", f"导出工资表：{year}-{month:02d}")
    db.session.commit()

    return send_file(
        output,
        as_attachment=True,
        download_name=f"工资统计_{year}_{month:02d}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/logs")
@login_required
def logs():
    if not current_user.is_owner:
        flash("仅公司创建者可查看日志。", "danger")
        return redirect(url_for("dashboard"))

    items = (
        AuditLog.query.filter_by(company_id=current_user.company_id)
        .order_by(AuditLog.created_at.desc())
        .limit(200)
        .all()
    )
    return render_template("logs.html", items=items)


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)
