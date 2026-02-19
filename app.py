from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from datetime import datetime, timedelta
import json, os, hashlib, sqlite3, statistics

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "goldbook_secret_2024")
API_KEY = os.environ.get("API_KEY", "goldbook_api_key")

DB = "goldbook.db"

# ── Database Setup ──────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_number TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            broker TEXT,
            currency TEXT DEFAULT 'USD',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            ticket INTEGER,
            pair TEXT,
            trade_type TEXT,
            open_time TEXT,
            close_time TEXT,
            open_price REAL,
            close_price REAL,
            sl REAL,
            tp REAL,
            lots REAL,
            profit REAL,
            commission REAL DEFAULT 0,
            swap REAL DEFAULT 0,
            duration_minutes INTEGER,
            FOREIGN KEY(account_id) REFERENCES accounts(id),
            UNIQUE(account_id, ticket)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            balance REAL,
            equity REAL,
            snapshot_time TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
    """)
    conn.commit()
    conn.close()

init_db()

def hash_password(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

def get_account(account_number):
    conn = get_db()
    acc = conn.execute("SELECT * FROM accounts WHERE account_number=?", (account_number,)).fetchone()
    conn.close()
    return acc

def compute_stats(trades):
    if not trades:
        return {}
    profits    = [t["profit"] for t in trades]
    wins       = [p for p in profits if p > 0]
    losses     = [p for p in profits if p < 0]
    total      = len(profits)
    win_count  = len(wins)
    loss_count = len(losses)
    win_rate   = (win_count / total * 100) if total > 0 else 0
    avg_win    = (sum(wins)   / len(wins))   if wins   else 0
    avg_loss   = (sum(losses) / len(losses)) if losses else 0
    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0
    expectancy    = (win_rate/100 * avg_win) + ((1 - win_rate/100) * avg_loss)

    # Sharpe ratio (simplified)
    if len(profits) > 1:
        avg_ret = sum(profits) / len(profits)
        std_ret = statistics.stdev(profits)
        sharpe  = (avg_ret / std_ret) if std_ret > 0 else 0
    else:
        sharpe = 0

    # Consecutive wins/losses
    max_consec_wins = max_consec_losses = cur_w = cur_l = 0
    for p in profits:
        if p > 0:
            cur_w += 1; cur_l = 0
            max_consec_wins = max(max_consec_wins, cur_w)
        else:
            cur_l += 1; cur_w = 0
            max_consec_losses = max(max_consec_losses, cur_l)

    # Drawdown
    peak = 0; max_dd = 0; running = 0
    for p in profits:
        running += p
        if running > peak: peak = running
        dd = peak - running
        if dd > max_dd: max_dd = dd

    return {
        "total_trades":       total,
        "win_count":          win_count,
        "loss_count":         loss_count,
        "win_rate":           round(win_rate, 1),
        "avg_win":            round(avg_win, 2),
        "avg_loss":           round(avg_loss, 2),
        "profit_factor":      round(profit_factor, 2),
        "expectancy":         round(expectancy, 2),
        "sharpe_ratio":       round(sharpe, 2),
        "max_consec_wins":    max_consec_wins,
        "max_consec_losses":  max_consec_losses,
        "total_profit":       round(sum(profits), 2),
        "max_drawdown":       round(max_dd, 2),
        "gross_profit":       round(gross_profit, 2),
        "gross_loss":         round(gross_loss, 2),
    }

# ── AUTH ROUTES ──────────────────────────────────────────
@app.route("/")
def index():
    if not session.get("account_id"):
        return redirect(url_for("login"))
    return redirect(url_for("dashboard"))

@app.route("/login", methods=["GET","POST"])
def login():
    error = None
    if request.method == "POST":
        acc_num = request.form.get("account_number","").strip()
        pwd     = request.form.get("password","").strip()
        acc     = get_account(acc_num)
        if acc and acc["password_hash"] == hash_password(pwd):
            session["account_id"]     = acc["id"]
            session["account_number"] = acc["account_number"]
            session["broker"]         = acc["broker"] or "MT5"
            return redirect(url_for("dashboard"))
        error = "Invalid account number or password."
    return render_template("login.html", error=error)

@app.route("/register", methods=["GET","POST"])
def register():
    error = None
    if request.method == "POST":
        acc_num  = request.form.get("account_number","").strip()
        pwd      = request.form.get("password","").strip()
        broker   = request.form.get("broker","MT5").strip()
        currency = request.form.get("currency","USD").strip()
        if not acc_num or not pwd:
            error = "Please fill all fields."
        else:
            try:
                conn = get_db()
                conn.execute(
                    "INSERT INTO accounts (account_number,password_hash,broker,currency) VALUES (?,?,?,?)",
                    (acc_num, hash_password(pwd), broker, currency)
                )
                conn.commit()
                conn.close()
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                error = "Account number already registered."
    return render_template("register.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── DASHBOARD PAGES ──────────────────────────────────────
@app.route("/dashboard")
def dashboard():
    if not session.get("account_id"):
        return redirect(url_for("login"))
    return render_template("dashboard.html",
                           account_number=session["account_number"],
                           broker=session["broker"])

@app.route("/trades")
def trades_page():
    if not session.get("account_id"):
        return redirect(url_for("login"))
    return render_template("trades.html",
                           account_number=session["account_number"])

@app.route("/analytics")
def analytics_page():
    if not session.get("account_id"):
        return redirect(url_for("login"))
    return render_template("analytics.html",
                           account_number=session["account_number"])

# ── DATA API ─────────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    if not session.get("account_id"):
        return jsonify({"error":"unauthorized"}), 401
    conn   = get_db()
    rows   = conn.execute("SELECT * FROM trades WHERE account_id=? ORDER BY close_time DESC",
                          (session["account_id"],)).fetchall()
    snaps  = conn.execute("SELECT * FROM equity_snapshots WHERE account_id=? ORDER BY snapshot_time ASC",
                          (session["account_id"],)).fetchall()
    conn.close()
    trades = [dict(r) for r in rows]
    stats  = compute_stats(trades)

    # Equity curve
    equity_curve = [{"time": s["snapshot_time"], "balance": s["balance"], "equity": s["equity"]}
                    for s in snaps]

    # P&L calendar
    cal = {}
    for t in trades:
        day = t["close_time"][:10] if t["close_time"] else None
        if day:
            cal[day] = round(cal.get(day, 0) + t["profit"], 2)

    # Hours heatmap
    hours = {str(h): 0 for h in range(24)}
    for t in trades:
        if t["open_time"]:
            try:
                h = str(datetime.strptime(t["open_time"][:19], "%Y-%m-%d %H:%M:%S").hour)
                hours[h] = round(hours[h] + t["profit"], 2)
            except: pass

    # Day of week
    days = {"Mon":0,"Tue":0,"Wed":0,"Thu":0,"Fri":0,"Sat":0,"Sun":0}
    day_names = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    for t in trades:
        if t["open_time"]:
            try:
                d = datetime.strptime(t["open_time"][:19], "%Y-%m-%d %H:%M:%S").weekday()
                days[day_names[d]] = round(days[day_names[d]] + t["profit"], 2)
            except: pass

    # By pair
    pairs = {}
    for t in trades:
        p = t["pair"] or "Unknown"
        if p not in pairs:
            pairs[p] = {"wins":0,"losses":0,"profit":0}
        if t["profit"] > 0: pairs[p]["wins"] += 1
        else: pairs[p]["losses"] += 1
        pairs[p]["profit"] = round(pairs[p]["profit"] + t["profit"], 2)

    # Monthly P&L
    monthly = {}
    for t in trades:
        if t["close_time"]:
            mon = t["close_time"][:7]
            monthly[mon] = round(monthly.get(mon, 0) + t["profit"], 2)

    return jsonify({
        "stats":        stats,
        "equity_curve": equity_curve,
        "calendar":     cal,
        "hours":        hours,
        "days":         days,
        "pairs":        pairs,
        "monthly":      monthly,
        "trade_count":  len(trades)
    })

@app.route("/api/trades")
def api_trades():
    if not session.get("account_id"):
        return jsonify({"error":"unauthorized"}), 401
    conn   = get_db()
    rows   = conn.execute("SELECT * FROM trades WHERE account_id=? ORDER BY close_time DESC",
                          (session["account_id"],)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

# ── MT5 SYNC ENDPOINT ────────────────────────────────────
@app.route("/api/trades/sync", methods=["POST"])
def sync_trades():
    if request.headers.get("X-API-Key","") != API_KEY:
        return jsonify({"error":"invalid key"}), 403
    try:
        payload    = request.get_json(force=True)
        acc_num    = payload.get("account_number","")
        trades_in  = payload.get("trades", [])
        balance    = payload.get("balance", 0)
        equity     = payload.get("equity", 0)

        acc = get_account(acc_num)
        if not acc:
            return jsonify({"error":"account not found"}), 404

        conn = get_db()
        inserted = 0
        for t in trades_in:
            try:
                open_t  = t.get("open_time","")
                close_t = t.get("close_time","")
                dur = 0
                if open_t and close_t:
                    try:
                        dt1 = datetime.strptime(open_t[:19],  "%Y-%m-%d %H:%M:%S")
                        dt2 = datetime.strptime(close_t[:19], "%Y-%m-%d %H:%M:%S")
                        dur = int((dt2 - dt1).total_seconds() / 60)
                    except: pass

                conn.execute("""
                    INSERT OR IGNORE INTO trades
                    (account_id,ticket,pair,trade_type,open_time,close_time,
                     open_price,close_price,sl,tp,lots,profit,commission,swap,duration_minutes)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (acc["id"], t.get("ticket"), t.get("pair"), t.get("type"),
                      open_t, close_t,
                      t.get("open_price",0), t.get("close_price",0),
                      t.get("sl",0), t.get("tp",0), t.get("lots",0),
                      t.get("profit",0), t.get("commission",0),
                      t.get("swap",0), dur))
                inserted += 1
            except: pass

        # Save equity snapshot
        conn.execute("INSERT INTO equity_snapshots (account_id,balance,equity) VALUES (?,?,?)",
                     (acc["id"], balance, equity))
        conn.commit()
        conn.close()
        return jsonify({"status":"ok","inserted":inserted})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/api/register", methods=["POST"])
def api_register():
    if request.headers.get("X-API-Key","") != API_KEY:
        return jsonify({"error":"invalid key"}), 403
    data    = request.get_json(force=True)
    acc_num = data.get("account_number","")
    pwd     = data.get("password","")
    broker  = data.get("broker","MT5")
    try:
        conn = get_db()
        conn.execute("INSERT INTO accounts (account_number,password_hash,broker) VALUES (?,?,?)",
                     (acc_num, hash_password(pwd), broker))
        conn.commit()
        conn.close()
        return jsonify({"status":"registered"})
    except sqlite3.IntegrityError:
        return jsonify({"status":"already exists"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
