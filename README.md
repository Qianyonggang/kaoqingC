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

> 推荐系统：Ubuntu 22.04。
>
> 下方命令是“从零开始可直接复制执行”的完整流程，已包含你之前遇到的两个高频坑：
> 1）`kaoqing.service` 启动报 `203/EXEC Permission denied`；
> 2）Nginx `server_name` 分号遗漏导致配置失败。

### 4.1 前置检查（控制台）

1. 阿里云安全组放行端口：`22`、`80`、`443`。
2. 域名解析（如 `mrkaoqing.top`）添加 A 记录：
   - `@` -> 服务器公网 IP
   - `www` -> 服务器公网 IP
3. 登录服务器：

```bash
ssh root@你的公网IP
```

### 4.2 安装系统依赖

```bash
apt update
apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx
```

### 4.3 上传代码并创建目录

```bash
mkdir -p /opt/kaoqingC
cd /opt/kaoqingC
# 方式A：git clone 代码到当前目录
# 方式B：用 WinSCP/Xftp 上传代码到当前目录
mkdir -p /opt/kaoqingC/data
```

### 4.4 关键步骤：创建虚拟环境（避免 203/EXEC）

> 如果你服务器登录后提示 `(base)`，说明 conda 正在生效。
> 请先退出 conda，再用系统 Python 创建 venv，并使用 `--copies`。

```bash
conda deactivate 2>/dev/null || true
cd /opt/kaoqingC
/usr/bin/python3 -m venv --copies .venv
/opt/kaoqingC/.venv/bin/pip install -U pip
/opt/kaoqingC/.venv/bin/pip install -r requirements.txt
```

### 4.5 创建生产环境变量

```bash
cat > /opt/kaoqingC/.env << 'EOF'
SECRET_KEY=请替换为随机长字符串
DATABASE_URL=sqlite:////opt/kaoqingC/data/attendance.db
HOST=127.0.0.1
PORT=5000
FLASK_DEBUG=0
EOF
```

### 4.6 首次初始化数据库

```bash
cd /opt/kaoqingC
set -a
source /opt/kaoqingC/.env
set +a
/opt/kaoqingC/.venv/bin/python app.py
# 首次运行会创建数据库，看到启动后 Ctrl+C 停止
```

### 4.7 配置 systemd 托管（开机自启）

```bash
cat > /etc/systemd/system/kaoqing.service << 'EOF'
[Unit]
Description=Kaoqing Flask App
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/kaoqingC
EnvironmentFile=/opt/kaoqingC/.env
ExecStart=/opt/kaoqingC/.venv/bin/python -m gunicorn -w 2 -b 127.0.0.1:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# 权限修复（必须执行）
chown -R www-data:www-data /opt/kaoqingC
chmod 755 /opt /opt/kaoqingC /opt/kaoqingC/.venv /opt/kaoqingC/.venv/bin
chmod 755 /opt/kaoqingC/.venv/bin/python /opt/kaoqingC/.venv/bin/gunicorn

systemctl daemon-reload
systemctl reset-failed kaoqing
systemctl enable kaoqing
systemctl restart kaoqing
systemctl status kaoqing --no-pager -l
```

### 4.8 配置 Nginx 反向代理

```bash
cat > /etc/nginx/sites-available/kaoqing << 'EOF'
server {
    listen 80;
    server_name 你的域名 www.你的域名;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/kaoqing /etc/nginx/sites-enabled/kaoqing
nginx -t
systemctl enable nginx
systemctl restart nginx
systemctl status nginx --no-pager -l
```

### 4.9 联通性验证（逐层排查）

```bash
# 1) Gunicorn
curl -I http://127.0.0.1:5000

# 2) Nginx 本机入口
curl -I http://127.0.0.1

# 3) 强制 Host 命中你的站点
curl -I -H "Host: 你的域名" http://127.0.0.1

# 4) DNS 是否生效
dig +short 你的域名
```

- 第1步通，说明后端正常。
- 第2/3步通，说明 Nginx 反代正常。
- 第4步无返回，说明是 DNS 未生效，不是程序问题。

### 4.10 申请 HTTPS 证书

```bash
certbot --nginx -d 你的域名 -d www.你的域名
```

申请成功后访问：`https://你的域名`。

### 4.11 SQLite 自动备份（每日 2 点）

> 仓库已提供脚本：`scripts/backup_sqlite.sh`，默认会备份到 `/opt/kaoqingC/backup`，并保留 14 天。

```bash
cd /opt/kaoqingC
chmod +x /opt/kaoqingC/scripts/backup_sqlite.sh
(crontab -l 2>/dev/null; echo '0 2 * * * /opt/kaoqingC/scripts/backup_sqlite.sh') | crontab -
crontab -l
```

### 4.12 常见故障快速命令

```bash
systemctl status kaoqing --no-pager -l
journalctl -u kaoqing -n 120 --no-pager
systemctl status nginx --no-pager -l
nginx -t
namei -om /opt/kaoqingC/.venv/bin/python
```

如果看到 `.venv/bin/python -> /root/miniconda3/...`，请删除 `.venv` 后按 4.4 重建。

---


## 5. 常见问题

### Q1：为什么用 SQLite？
你当前管理员规模小（<10），SQLite 更轻量，运维成本低。

### Q2：本地和线上可以共用一套代码吗？
可以，本项目通过环境变量切换调试/生产参数。

### Q3：如果后续并发增大怎么办？
可再切换到 PostgreSQL（需要额外迁移方案）。


### Q4：`scripts/update_version.sh` 怎么知道从哪个 GitHub 仓库拉代码？

- 如果 `/opt/kaoqingC` 本身就是用 `git clone` 部署的，脚本会直接使用当前仓库的 `origin`，无需你每次手动输入。
- 你也可以在执行时显式指定仓库地址（第二个参数）：

```bash
./scripts/update_version.sh work https://github.com/Qianyonggang/kaoqingC.git
```

- 私有仓库推荐两种方式：
  1) SSH Key（推荐）：把服务器公钥加到 GitHub，再用 `git@github.com:Qianyonggang/kaoqingC.git`。
  2) HTTPS + Token：使用 PAT（不要用账号密码）。

### Q5：自动备份保存在哪里？能每天覆盖吗？

- 备份目录默认是：`/opt/kaoqingC/backup`。
- 默认模式是 `rotate`，每次生成一个带时间戳的新文件（便于回滚）。
- 如果你希望每天覆盖同一个文件，执行更新脚本时加：

```bash
BACKUP_MODE=overwrite ./scripts/update_version.sh
```

覆盖模式会写入：`/opt/kaoqingC/backup/attendance_latest.db`。

