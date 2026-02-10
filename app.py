import os
from datetime import date, datetime
from io import BytesIO

import pandas as pd
from flask import Flask, flash, redirect, render_template, request, send_file, url_for
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, UniqueConstraint, func
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

# 员工与团队为多对多关系的中间表（用于实现员工跨团队共享）
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
    # 为兼容已有 SQLite 表结构，保留电话/银行卡字段并给默认值
    phone = db.Column(db.String(30), nullable=False, default="")
    bank_account = db.Column(db.String(64), nullable=False, default="")
    daily_salary = db.Column(db.Float, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_company_employee_name"),)

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

    query_text = request.args.get("q", "").strip()
    admins_data = User.query.filter_by(company_id=current_user.company_id, is_admin=True).all()

    if request.method == "POST":
        name = request.form["name"].strip()
        manager_id = int(request.form["manager_id"])
        if Team.query.filter_by(company_id=current_user.company_id, name=name).first():
            flash("团队名称已存在，不允许重复。", "danger")
            return redirect(url_for("teams"))
        team = Team(company_id=current_user.company_id, name=name, manager_id=manager_id)
        db.session.add(team)
        log_action("create_team", f"新增团队：{name}")
        db.session.commit()
        flash("团队创建成功。", "success")
        return redirect(url_for("teams"))

    team_query = Team.query.filter_by(company_id=current_user.company_id)
    if query_text:
        team_query = team_query.filter(Team.name.like(f"%{query_text}%"))
    items = team_query.order_by(Team.created_at.desc()).all()
    return render_template("teams.html", items=items, admins=admins_data, query_text=query_text)


@app.route("/teams/<int:team_id>", methods=["GET", "POST"])
@login_required
def team_detail(team_id):
    """团队详情页：可在团队内直接新增员工，或把原有员工加入团队。"""
    if not current_user.is_admin:
        flash("仅管理员可操作。", "danger")
        return redirect(url_for("dashboard"))

    team = Team.query.get_or_404(team_id)
    if not ensure_company_scope(team):
        return redirect(url_for("teams"))

    query_text = request.args.get("q", "").strip()
    existing_q = request.args.get("existing_q", "").strip()

    if request.method == "POST":
        add_mode = request.form.get("add_mode", "new_employee")

        if add_mode == "existing":
            employee_id = request.form.get("existing_employee_id")
            if not employee_id:
                flash("请选择要加入团队的员工。", "warning")
                return redirect(url_for("team_detail", team_id=team.id))
            employee = Employee.query.get_or_404(int(employee_id))
            if not ensure_company_scope(employee):
                return redirect(url_for("teams"))
            if team in employee.teams:
                flash("该员工已在当前团队中。", "info")
                return redirect(url_for("team_detail", team_id=team.id))
            employee.teams.append(team)
            log_action("add_existing_employee_to_team", f"员工 {employee.name} 加入团队 {team.name}")
            db.session.commit()
            flash("已成功将原有员工加入当前团队。", "success")
            return redirect(url_for("team_detail", team_id=team.id))

        if add_mode == "new_employee":
            raw_name = request.form["name"].strip()
            daily_salary = float(request.form["daily_salary"])
            is_temp_worker = request.form.get("is_temp_worker", "0") == "1"

            # 若选择临时用工，自动命名为“姓名-临-团队”
            name = f"{raw_name}-临-{team.name}" if is_temp_worker else raw_name

            if Employee.query.filter_by(company_id=current_user.company_id, name=name).first():
                flash("员工姓名已存在，不允许重复。", "danger")
                return redirect(url_for("team_detail", team_id=team.id))

            employee = Employee(
                company_id=current_user.company_id,
                name=name,
                phone="",
                bank_account="",
                daily_salary=daily_salary,
                created_by=current_user.id,
            )
            employee.teams.append(team)
            db.session.add(employee)
            log_action("create_employee_in_team", f"团队 {team.name} 新增员工：{name}")
            db.session.commit()
            flash("员工新增成功，并已加入当前团队。", "success")
            return redirect(url_for("team_detail", team_id=team.id))

        flash("未知的新增模式。", "danger")
        return redirect(url_for("team_detail", team_id=team.id))

    members = team.members
    if query_text:
        members = [m for m in members if query_text.lower() in m.name.lower()]

    # “原有员工”这里展示公司内全部员工（含已在本团队的员工），
    # 若重复添加会在提交时提示“已在当前团队中”。
    available_query = Employee.query.filter(Employee.company_id == current_user.company_id)
    if existing_q:
        available_query = available_query.filter(Employee.name.like(f"%{existing_q}%"))
    available_employees = available_query.order_by(Employee.name.asc()).all()

    return render_template(
        "team_detail.html",
        team=team,
        members=members,
        query_text=query_text,
        existing_q=existing_q,
        available_employees=available_employees,
    )


@app.route("/teams/<int:team_id>/employees/<int:employee_id>/update", methods=["POST"])
@login_required
def team_employee_update(team_id, employee_id):
    if not current_user.is_admin:
        flash("仅管理员可操作。", "danger")
        return redirect(url_for("dashboard"))

    team = Team.query.get_or_404(team_id)
    employee = Employee.query.get_or_404(employee_id)
    if not ensure_company_scope(team) or not ensure_company_scope(employee):
        return redirect(url_for("teams"))

    new_name = request.form["name"].strip()
    duplicated = Employee.query.filter(
        Employee.company_id == current_user.company_id,
        Employee.name == new_name,
        Employee.id != employee.id,
    ).first()
    if duplicated:
        flash("员工姓名已存在，不允许重复。", "danger")
        return redirect(url_for("team_detail", team_id=team.id))

    employee.name = new_name
    employee.daily_salary = float(request.form["daily_salary"])

    if team not in employee.teams:
        employee.teams.append(team)

    log_action("update_employee", f"更新员工：{employee.name}（团队 {team.name}）")
    db.session.commit()
    flash("员工信息更新成功。", "success")
    return redirect(url_for("team_detail", team_id=team.id))


@app.route("/teams/<int:team_id>/employees/<int:employee_id>/delete", methods=["POST"])
@login_required
def team_employee_delete(team_id, employee_id):
    """从团队移除员工；若该员工不在任何团队中则删除员工主档。"""
    if not current_user.is_admin:
        flash("仅管理员可操作。", "danger")
        return redirect(url_for("dashboard"))

    team = Team.query.get_or_404(team_id)
    employee = Employee.query.get_or_404(employee_id)
    if not ensure_company_scope(team) or not ensure_company_scope(employee):
        return redirect(url_for("teams"))

    if team in employee.teams:
        employee.teams.remove(team)

    # 如果员工已不属于任何团队，可按业务需要直接删除
    if len(employee.teams) == 0:
        db.session.delete(employee)
        log_action("delete_employee", f"删除员工：{employee.name}")
    else:
        log_action("remove_employee_from_team", f"员工 {employee.name} 从团队 {team.name} 移除")

    db.session.commit()
    flash("员工维护操作已完成。", "success")
    return redirect(url_for("team_detail", team_id=team.id))


@app.route("/teams/<int:team_id>/attendance", methods=["GET", "POST"])
@login_required
def team_attendance(team_id):
    """团队考勤页：显示该团队全部员工，单选按钮录入考勤。"""
    if not current_user.is_admin:
        flash("仅管理员可操作。", "danger")
        return redirect(url_for("dashboard"))

    team = Team.query.get_or_404(team_id)
    if not ensure_company_scope(team):
        return redirect(url_for("teams"))

    query_text = request.args.get("q", "").strip()

    if request.method == "POST":
        work_date = datetime.strptime(request.form["work_date"], "%Y-%m-%d").date()
        if work_date > date.today():
            flash("不能记录未来日期的考勤。", "danger")
            return redirect(url_for("team_attendance", team_id=team.id, work_date=work_date.isoformat(), q=query_text))

        error_messages = []
        updated_count = 0
        # 遍历团队下所有员工，批量写入当天考勤
        for emp in team.members:
            raw_value = request.form.get(f"attendance_{emp.id}")
            if raw_value is None:
                continue

            day_count = float(raw_value)
            record = Attendance.query.filter_by(employee_id=emp.id, team_id=team.id, work_date=work_date).first()

            # 计算该员工在其它团队的当天工时，保证总和不超过 1
            other_sum = (
                db.session.query(func.coalesce(func.sum(Attendance.day_count), 0.0))
                .filter(
                    Attendance.employee_id == emp.id,
                    Attendance.work_date == work_date,
                    Attendance.team_id != team.id,
                )
                .scalar()
            )

            if other_sum + day_count > 1.0:
                error_messages.append(f"{emp.name} 超过1天（其它团队已记录 {other_sum} 天）")
                continue

            if record:
                record.day_count = day_count
                record.created_by = current_user.id
            else:
                record = Attendance(
                    company_id=current_user.company_id,
                    employee_id=emp.id,
                    team_id=team.id,
                    work_date=work_date,
                    day_count=day_count,
                    created_by=current_user.id,
                )
                db.session.add(record)
            updated_count += 1

        if updated_count:
            log_action("batch_attendance", f"团队 {team.name} 批量考勤：{work_date}，更新 {updated_count} 人")
            db.session.commit()
            flash(f"考勤保存成功，已更新 {updated_count} 人。", "success")

        if error_messages:
            flash("；".join(error_messages), "danger")

        if not updated_count and not error_messages:
            flash("未选择任何员工的考勤数据。", "warning")

        return redirect(url_for("team_attendance", team_id=team.id, work_date=work_date.isoformat(), q=query_text))

    members = team.members
    if query_text:
        members = [m for m in members if query_text.lower() in m.name.lower()]

    selected_date_str = request.args.get("work_date", date.today().isoformat())
    selected_date = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
    if selected_date > date.today():
        selected_date = date.today()
        selected_date_str = selected_date.isoformat()
        flash("考勤日期不能超过今天，已自动切换为今天。", "warning")

    attendance_map = {}
    for emp in members:
        record = Attendance.query.filter_by(employee_id=emp.id, team_id=team.id, work_date=selected_date).first()
        attendance_map[emp.id] = record.day_count if record else 0

    return render_template(
        "team_attendance.html",
        team=team,
        members=members,
        attendance_map=attendance_map,
        selected_date_str=selected_date_str,
        query_text=query_text,
        today=date.today(),
    )


@app.route("/employees")
@login_required
def employees():
    """全局员工查询页（保留快速搜索能力）。"""
    if not current_user.is_admin:
        flash("仅管理员可操作。", "danger")
        return redirect(url_for("dashboard"))

    query_text = request.args.get("q", "").strip()
    items_query = Employee.query.filter_by(company_id=current_user.company_id)
    if query_text:
        items_query = items_query.filter(Employee.name.like(f"%{query_text}%"))
    items = items_query.order_by(Employee.created_at.desc()).all()
    return render_template("employees.html", items=items, query_text=query_text)


@app.route("/attendance")
@login_required
def attendance_redirect():
    """旧入口统一引导到团队页面中的团队考勤入口。"""
    flash("请先选择团队后再进行考勤。", "info")
    return redirect(url_for("teams"))


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
    scope = request.args.get("scope", "month")  # month / all
    employee_q = request.args.get("employee_q", "").strip()

    result = []
    employees_query = Employee.query.filter_by(company_id=current_user.company_id)
    if employee_q:
        employees_query = employees_query.filter(Employee.name.like(f"%{employee_q}%"))
    employees_data = employees_query.all()

    # 统计所有出现过的月份（考勤或借支）
    month_keys = set()
    if scope == "all":
        att_dates = db.session.query(Attendance.work_date).filter_by(company_id=current_user.company_id).all()
        adv_dates = db.session.query(Advance.advance_date).filter_by(company_id=current_user.company_id).all()
        for (d,) in att_dates:
            month_keys.add((d.year, d.month))
        for (d,) in adv_dates:
            month_keys.add((d.year, d.month))
        if not month_keys:
            month_keys.add((year, month))
    else:
        month_keys.add((year, month))

    ordered_months = sorted(month_keys)

    for emp in employees_data:
        row = {
            "name": emp.name,
            "daily_salary": emp.daily_salary,
            "total_days": 0.0,
            "total_advances": 0.0,
            "total_gross": 0.0,
            "total_remain": 0.0,
            "month_days": {},
        }
        for y, m in ordered_months:
            days, advances_amt, gross, remain = calculate_month_stat(emp.id, y, m)
            row["month_days"][(y, m)] = days
            row["total_days"] += days
            row["total_advances"] += advances_amt
            row["total_gross"] += gross
            row["total_remain"] += remain

        row["total_days"] = round(row["total_days"], 2)
        row["total_advances"] = round(row["total_advances"], 2)
        row["total_gross"] = round(row["total_gross"], 2)
        row["total_remain"] = round(row["total_remain"], 2)
        result.append(row)

    return render_template(
        "payroll.html",
        rows=result,
        year=year,
        month=month,
        scope=scope,
        ordered_months=ordered_months,
        employee_q=employee_q,
    )


@app.route("/export")
@login_required
def export_excel():
    if not current_user.is_owner:
        flash("仅公司创建者可导出。", "danger")
        return redirect(url_for("dashboard"))

    year = int(request.args.get("year", date.today().year))
    month = int(request.args.get("month", date.today().month))
    scope = request.args.get("scope", "month")
    employee_q = request.args.get("employee_q", "").strip()

    employees_query = Employee.query.filter_by(company_id=current_user.company_id)
    if employee_q:
        employees_query = employees_query.filter(Employee.name.like(f"%{employee_q}%"))
    employees_data = employees_query.all()

    # 统计需导出的月份
    month_keys = set()
    if scope == "all":
        att_dates = db.session.query(Attendance.work_date).filter_by(company_id=current_user.company_id).all()
        adv_dates = db.session.query(Advance.advance_date).filter_by(company_id=current_user.company_id).all()
        for (d,) in att_dates:
            month_keys.add((d.year, d.month))
        for (d,) in adv_dates:
            month_keys.add((d.year, d.month))
        if not month_keys:
            month_keys.add((year, month))
    else:
        month_keys.add((year, month))

    ordered_months = sorted(month_keys)

    rows = []
    for emp in employees_data:
        row = {
            "员工姓名": emp.name,
            "单日工资": emp.daily_salary,
        }
        total_days = 0.0
        total_advances = 0.0
        total_gross = 0.0

        for y, m in ordered_months:
            days, advances_amt, gross, _remain = calculate_month_stat(emp.id, y, m)
            ym_label = f"{y}年{m}月"
            row[f"{ym_label}考勤天数"] = days
            total_days += days
            total_advances += advances_amt
            total_gross += gross

        row["总考勤天数"] = round(total_days, 2)
        row["总借支"] = round(total_advances, 2)
        row["总工资"] = round(total_gross, 2)
        row["剩余工资"] = round(total_gross - total_advances, 2)
        rows.append(row)

    df = pd.DataFrame(rows)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="工资统计")
    output.seek(0)

    log_action("export_excel", f"导出工资表：scope={scope}，关键字={employee_q or '全部'}，基准={year}-{month:02d}")
    db.session.commit()

    suffix = "全部月份" if scope == "all" else f"{year}_{month:02d}"
    return send_file(
        output,
        as_attachment=True,
        download_name=f"工资统计_{suffix}.xlsx",
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
