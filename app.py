
import os, json, datetime
import psycopg
from flask import Flask, render_template, request, redirect, url_for, jsonify

app = Flask(__name__)

# Pricing
PRICE_TABLE = {"4x6": 8.0, "5x7": 15.0, "8x10": 20.0}
FOUR_BY_SIX_DEAL = {"size": "4x6", "bundle_qty": 3, "bundle_price": 20.0}

def get_db_conn():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set. In Render, add it under Environment.")
    if "sslmode=" not in db_url:
        db_url += ("&" if "?" in db_url else "?") + "sslmode=require"
    return psycopg.connect(db_url)

def init_db():
    with get_db_conn() as con:
        with con.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    customer_name TEXT,
                    address TEXT,
                    phone TEXT,
                    email TEXT,
                    items_json TEXT,
                    subtotal NUMERIC,
                    discount NUMERIC,
                    total NUMERIC,
                    status TEXT,
                    created_at TIMESTAMPTZ
                )
            """)
        con.commit()

try:
    init_db()
except Exception as e:
    print("[DB INIT ERROR]", e)

def price_items(items):
    subtotal = 0.0
    discount = 0.0
    four_by_six_count = 0
    four_by_six_unit_total = 0.0
    for it in items:
        size = it.get("size", "")
        qty = int(it.get("qty", 0) or 0)
        unit = PRICE_TABLE.get(size, 0.0)
        cost = unit * qty
        subtotal += cost
        if size == "4x6":
            four_by_six_count += qty
            four_by_six_unit_total += cost
    if four_by_six_count >= FOUR_BY_SIX_DEAL["bundle_qty"]:
        bundles = four_by_six_count // FOUR_BY_SIX_DEAL["bundle_qty"]
        bundle_qty = bundles * FOUR_BY_SIX_DEAL["bundle_qty"]
        normal_price = PRICE_TABLE["4x6"] * bundle_qty
        deal_price = bundles * FOUR_BY_SIX_DEAL["bundle_price"]
        discount = min(normal_price - deal_price, four_by_six_unit_total)
    total = round(subtotal - discount, 2)
    return round(subtotal, 2), round(discount, 2), total

from flask import render_template_string

@app.route("/")
def index():
    return render_template("customer.html", price_table=PRICE_TABLE, deal=FOUR_BY_SIX_DEAL)

@app.route("/calc", methods=["POST"])
def calc():
    items = request.json.get("items", [])
    subtotal, discount, total = price_items(items)
    return jsonify({"subtotal": subtotal, "discount": discount, "total": total})

@app.route("/submit_order", methods=["POST"])
def submit_order():
    data = request.form
    items = json.loads(data.get("items_json", "[]"))
    subtotal, discount, total = price_items(items)
    with get_db_conn() as con:
        with con.cursor() as cur:
            cur.execute("""
                INSERT INTO orders
                (customer_name, address, phone, email, items_json, subtotal, discount, total, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
                RETURNING id
            """, (
                data.get("customer_name","").strip(),
                data.get("address","").strip(),
                data.get("phone","").strip(),
                data.get("email","").strip(),
                json.dumps(items),
                subtotal, discount, total,
                datetime.datetime.utcnow()
            ))
            order_id = cur.fetchone()[0]
        con.commit()
    return redirect(url_for("thank_you", order_id=order_id, total=total))

@app.route("/thank_you/<int:order_id>")
def thank_you(order_id):
    total = request.args.get("total", type=float)
    return render_template("thank_you.html", order_id=order_id, total=total)

@app.route("/admin")
def admin():
    with get_db_conn() as con:
        with con.cursor() as cur:
            cur.execute("SELECT id, customer_name, total, status, created_at FROM orders ORDER BY id DESC")
            rows = cur.fetchall()
    return render_template("admin.html", orders=rows)

@app.route("/order/<int:order_id>")
def order_detail(order_id):
    with get_db_conn() as con:
        with con.cursor() as cur:
            cur.execute("""
                SELECT id, customer_name, address, phone, email, items_json, subtotal, discount, total, status, created_at
                FROM orders WHERE id=%s
            """, (order_id,))
            row = cur.fetchone()
    if not row:
        return "Order not found", 404
    order = {
        "id": row[0],
        "customer_name": row[1],
        "address": row[2],
        "phone": row[3],
        "email": row[4],
        "items": json.loads(row[5] or "[]"),
        "subtotal": float(row[6]) if row[6] is not None else 0.0,
        "discount": float(row[7]) if row[7] is not None else 0.0,
        "total": float(row[8]) if row[8] is not None else 0.0,
        "status": row[9],
        "created_at": row[10].isoformat() if row[10] else "",
    }
    return render_template("order_detail.html", order=order)

@app.route("/mark_paid/<int:order_id>", methods=["POST"])
def mark_paid(order_id):
    with get_db_conn() as con:
        with con.cursor() as cur:
            cur.execute("UPDATE orders SET status='paid' WHERE id=%s", (order_id,))
        con.commit()
    return redirect(url_for("admin"))

@app.route("/processing")
def processing():
    with get_db_conn() as con:
        with con.cursor() as cur:
            cur.execute("SELECT id, customer_name, items_json, total FROM orders WHERE status='paid' ORDER BY id DESC")
            rows = cur.fetchall()
    paid = [{
        "id": r[0],
        "customer_name": r[1],
        "items": json.loads(r[2] or "[]"),
        "total": float(r[3]) if r[3] is not None else 0.0
    } for r in rows]
    return render_template("processing.html", orders=paid)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
