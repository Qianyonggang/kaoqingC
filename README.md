# 企业人员考勤统计网站

这是一个面向公司内部使用的 **多管理员、多团队、共享员工** 考勤与工资统计系统。

- 默认数据库：SQLite（适合你当前 <10 管理员的场景）
- 同时支持：本地调试 + 云服务器部署（阿里云 ECS）

---

## 1. 功能总览

- 公司维度注册/登录（一个公司一个账号空间）
- 公司创建者（老板）自动具备管理员权限，并可继续创建管理员
- 管理员可创建多个团队
- 团队页面支持搜索，并可进入团队详情维护员工（新增或添加已有员工）
- 员工可分配到多个团队（跨团队共享）
- 团队考勤：0 / 0.5 / 1 天，禁止未来日期
- 同员工同一天跨团队总考勤不能超过 1 天
- 借支记录（禁止未来日期）
- 工资统计支持“单月/全部月份”并可导出 Excel
- 公司创建者可查看操作日志

---

## 2. 技术栈

- Python 3.10+
- Flask + Flask-SQLAlchemy + Flask-Login
- SQLite
- Bootstrap 5
- Pandas + OpenPyXL（导出）

---

## 3. 本地调试（开发环境）

### 3.1 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate   # Windows 用 .venv\Scripts\activate
pip install -r requirements.txt
```

### 3.2 启动

```bash
python app.py
```

默认访问：

- http://127.0.0.1:5000

### 3.3 常用环境变量

- `SECRET_KEY`：会话密钥
- `DATABASE_URL`：数据库连接（默认 SQLite）
- `HOST`：默认 `0.0.0.0`
- `PORT`：默认 `5000`
- `FLASK_DEBUG`：`1` 开调试，`0` 关调试

> 默认数据库文件在：`data/attendance.db`。

---

## 4. 云服务器部署（阿里云 ECS，生产环境）

> 推荐系统：Ubuntu 22.04

### 4.1 服务器准备

1. ECS 安全组放行：22、80、443
2. 连接服务器：

```bash
ssh root@你的服务器IP
```

### 4.2 安装系统依赖

```bash
apt update
apt install -y python3 python3-venv python3-pip nginx
```

### 4.3 部署项目

```bash
mkdir -p /opt/kaoqingC
cd /opt/kaoqingC
# 上传代码到这里（git clone / scp 均可）
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
mkdir -p data
```

### 4.4 配置生产环境变量

```bash
cat > /opt/kaoqingC/.env << 'EOF_ENV'
SECRET_KEY=请改成随机长字符串
DATABASE_URL=sqlite:////opt/kaoqingC/data/attendance.db
HOST=127.0.0.1
PORT=5000
FLASK_DEBUG=0
EOF_ENV
```

### 4.5 首次初始化

```bash
cd /opt/kaoqingC
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
python app.py
# 首次创建数据库后 Ctrl+C
```

### 4.6 使用 systemd + Gunicorn 托管

```bash
cat > /etc/systemd/system/kaoqing.service << 'EOF_SVC'
[Unit]
Description=Kaoqing Flask App
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/kaoqingC
EnvironmentFile=/opt/kaoqingC/.env
ExecStart=/opt/kaoqingC/.venv/bin/gunicorn -w 2 -b 127.0.0.1:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF_SVC

chown -R www-data:www-data /opt/kaoqingC
systemctl daemon-reload
systemctl enable kaoqing
systemctl restart kaoqing
systemctl status kaoqing
```

### 4.7 Nginx 反向代理

```bash
cat > /etc/nginx/sites-available/kaoqing << 'EOF_NGX'
server {
    listen 80;
    server_name 你的域名或服务器IP;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF_NGX

ln -sf /etc/nginx/sites-available/kaoqing /etc/nginx/sites-enabled/kaoqing
nginx -t
systemctl restart nginx
```

### 4.8 HTTPS（可选）

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d 你的域名
```

### 4.9 SQLite 备份建议

```bash
mkdir -p /opt/kaoqingC/backup
cp /opt/kaoqingC/data/attendance.db /opt/kaoqingC/backup/attendance_$(date +%F).db
```

可用 crontab 做每日备份。

---

## 5. 常见问题

### Q1：为什么用 SQLite？
你当前管理员规模小（<10），SQLite 更轻量，运维成本低。

### Q2：本地和线上可以共用一套代码吗？
可以，本项目通过环境变量切换调试/生产参数。

### Q3：如果后续并发增大怎么办？
可再切换到 PostgreSQL（需要额外迁移方案）。
