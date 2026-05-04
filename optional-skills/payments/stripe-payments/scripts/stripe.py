#!/usr/bin/env python3
"""Stripe Payments — Unified CLI (v1.0.0)

Production-grade Stripe management from the terminal. 27 commands.

Commands:
  invoice <amount_cents> <description> [--email X] [--name X] [--currency USD]
  paylink <amount_cents> <description> [--email X]
  send <amount_cents> <email> <description> [--name X]
  status [invoice_id|latest]
  balance
  stats
  followup
  list [limit]
  refund <charge_id|pi_id> [amount] [reason] [--dry-run]
  products
  customers [limit]
  customer <email_or_id>
  newproduct <name> [amount_cents] [--interval m/w/y] [--currency USD]
  subscriptions [limit] [--status active]
  cancelsub <sub_id> [--at-period-end]
  webhooks
  portal <customer_email>
  coupon <percent> <code> [description] [--duration once/repeating/forever] [--months N]
  reconcile [hours]
  history [limit]
  search <query>
  receipt <invoice_id|pi_id>
  ltv <email>
  duplicate <invoice_id>
  aging
  bulk-refund <csv_path> [--dry-run|--live]
  churn
  sync-tiers
  bulklink <product> --tier MOQ|A|B|C|D [--email X]

Stripe API key loaded from ~/.hermes/.env (STRIPE_API_KEY)
"""
import os, sys, json, time, sqlite3, csv, io, uuid, hashlib
from urllib.parse import urlencode
from contextlib import contextmanager
from datetime import datetime, timedelta

# ── Setup ──────────────────────────────────────────────────────────────
env_file = os.path.expanduser("~/.hermes/.env")
if os.path.exists(env_file):
    with open(env_file) as _f:
        for line in _f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)

SK = os.environ.get("STRIPE_API_KEY")
if not SK:
    print("Error: STRIPE_API_KEY not set in ~/.hermes/.env")
    sys.exit(1)

VERSION = "1.0.0"
MODE = "live" if SK.startswith("sk_live_") or SK.startswith("rk_live_") else "TEST"
API_VERSION = "2024-12-18.acacia"

# ── API helpers ────────────────────────────────────────────────────────
import httpx

MAX_RETRIES = 3
RETRY_BACKOFF = 1.5
_client = None

def _get_client():
    global _client
    if _client is None:
        _client = httpx.Client(timeout=30)
    return _client

def _sanitize_error(e):
    """Never leak API keys in error messages."""
    msg = str(e)
    for secret in [SK, SK[4:]] if SK else []:
        if secret and secret in msg:
            msg = msg.replace(secret, "***REDACTED***")
    return msg

def _headers(form=False):
    h = {
        "Authorization": "Bearer " + SK,
        "Stripe-Version": API_VERSION,
        "Idempotency-Key": uuid.uuid4().hex,
    }
    if form:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    return h

def _api_call(method, endpoint, data=None, params=None, retry=0):
    """Core API call with retry, rate-limit handling, and sanitized errors."""
    client = _get_client()
    url = "https://api.stripe.com" + endpoint
    headers = _headers(form=(data is not None))
    try:
        if method == "GET":
            r = client.get(url, headers=headers, params=params)
        elif method == "DELETE":
            r = client.delete(url, headers=headers)
        else:
            if data:
                r = client.post(url, headers=headers, data=data)
            else:
                r = client.post(url, headers=headers)
        if r.status_code == 429 and retry < MAX_RETRIES:
            wait = RETRY_BACKOFF ** (retry + 1)
            time.sleep(wait)
            return _api_call(method, endpoint, data, params, retry + 1)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        try:
            err_body = e.response.json()
            msg = err_body.get("error", {}).get("message", str(e))
        except Exception:
            msg = _sanitize_error(e)
        raise SystemExit(f"Stripe API error ({e.response.status_code}): {msg}")
    except httpx.RequestError as e:
        if retry < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF ** (retry + 1))
            return _api_call(method, endpoint, data, params, retry + 1)
        raise SystemExit(f"Network error: {_sanitize_error(e)}")

def _get(endpoint, params=None):
    return _api_call("GET", endpoint, params=params)

def _post(endpoint, data=None):
    return _api_call("POST", endpoint, data=data)

def _delete(endpoint):
    return _api_call("DELETE", endpoint)

def _paginate(endpoint, params=None, limit=100):
    """Fetch all pages up to limit. Returns list of objects."""
    params = dict(params or {})
    params["limit"] = str(min(limit, 100))
    all_data = []
    while True:
        result = _get(endpoint, params)
        batch = result.get("data", [])
        all_data.extend(batch)
        if not result.get("has_more") or len(all_data) >= limit:
            break
        params["starting_after"] = batch[-1]["id"]
    return all_data[:limit]

# ── Invoice History (SQLite) ───────────────────────────────────────────
@contextmanager
def _history_db():
    hist_dir = os.path.expanduser("~/.hermes/stripe")
    os.makedirs(hist_dir, exist_ok=True)
    db_path = os.path.join(hist_dir, "invoices.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS invoices (
        id TEXT PRIMARY KEY, invoice_id TEXT, type TEXT, amount_cents INTEGER,
        description TEXT, url TEXT, hosted_url TEXT, invoice_pdf TEXT,
        status TEXT, created INTEGER)""")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()

# ── Input validation ───────────────────────────────────────────────────
def _validate_amount(value, field="amount"):
    try:
        n = int(value)
        if n < 0:
            raise ValueError
        return n
    except (ValueError, TypeError):
        raise SystemExit(f"Error: {field} must be a non-negative integer (cents), got: {value}")

def _validate_email(email):
    if email and "@" not in email:
        raise SystemExit(f"Error: invalid email: {email}")
    return email

def _validate_currency(currency):
    currency = (currency or "usd").lower()
    if len(currency) != 3:
        raise SystemExit(f"Error: currency must be 3-letter ISO code, got: {currency}")
    return currency

# ── Commands ───────────────────────────────────────────────────────────

def cmd_invoice(amount_cents, description, email=None, customer_name=None, currency="usd"):
    """Create a hosted Stripe invoice with PDF."""
    amount_cents = _validate_amount(amount_cents)
    currency = _validate_currency(currency)
    cust_email = email if email else f"invoice-{int(time.time())}@hermes.agent"
    if email:
        _validate_email(email)
    existing = _get("/v1/customers", {"email": cust_email, "limit": 1})
    if existing.get("data"):
        customer_id = existing["data"][0]["id"]
    else:
        cdata = {"email": cust_email, "metadata[source]": "hermes-agent"}
        if customer_name:
            cdata["name"] = customer_name
        customer_id = _post("/v1/customers", data=cdata)["id"]

    inv = _post("/v1/invoices", data={"customer": customer_id})
    invoice_id = inv["id"]

    _post("/v1/invoiceitems", data={
        "customer": customer_id, "invoice": invoice_id,
        "amount": str(amount_cents), "currency": currency, "description": description,
    })

    fin = _post("/v1/invoices/" + invoice_id + "/finalize")

    try:
        with _history_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO invoices (id, invoice_id, type, amount_cents, description, url, hosted_url, invoice_pdf, status, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (fin["id"], invoice_id, "hosted_invoice", amount_cents, description,
                 fin.get("hosted_invoice_url", ""), fin.get("hosted_invoice_url", ""),
                 fin.get("invoice_pdf", ""), fin.get("status", "open"), int(time.time())))
            conn.commit()
    except Exception as e:
        print(f"Warning: failed to save invoice history: {e}", file=sys.stderr)

    return {
        "type": "hosted_invoice", "invoice_id": invoice_id,
        "invoice_number": fin.get("number", ""), "amount_cents": amount_cents,
        "description": description, "customer_id": customer_id,
        "customer_email": cust_email, "hosted_url": fin.get("hosted_invoice_url", ""),
        "invoice_pdf": fin.get("invoice_pdf", ""), "status": fin.get("status", "open"),
        "currency": currency, "mode": MODE,
    }

def cmd_pay_link(amount_cents, description, customer_email=None, currency="usd"):
    """Stripe Checkout Session — simplest payment page."""
    amount_cents = _validate_amount(amount_cents)
    data = {
        "mode": "payment",
        "line_items[0][price_data][currency]": _validate_currency(currency),
        "line_items[0][price_data][unit_amount]": str(amount_cents),
        "line_items[0][price_data][product_data][name]": description,
        "line_items[0][quantity]": "1",
        "success_url": "https://dashboard.stripe.com/payments",
        "cancel_url": "https://dashboard.stripe.com/payments",
        "metadata[source]": "hermes-agent",
        "metadata[description]": description,
    }
    if customer_email:
        _validate_email(customer_email)
        data["customer_email"] = customer_email
    else:
        data["customer_creation"] = "always"
        data["billing_address_collection"] = "auto"
    session = _post("/v1/checkout/sessions", data=data)
    return {
        "type": "pay_link", "session_id": session["id"], "url": session["url"],
        "amount_cents": amount_cents, "description": description,
        "customer_email": customer_email or "", "mode": MODE,
    }

def cmd_send(amount_cents, email, description, customer_name=None, currency="usd"):
    """Hosted invoice emailed to client."""
    amount_cents = _validate_amount(amount_cents)
    _validate_email(email)
    custs = _get("/v1/customers", {"email": email, "limit": 1})
    if custs.get("data"):
        customer_id = custs["data"][0]["id"]
    else:
        cdata = {"email": email}
        if customer_name:
            cdata["name"] = customer_name
        customer_id = _post("/v1/customers", data=cdata)["id"]

    inv = _post("/v1/invoices", data={"customer": customer_id})
    invoice_id = inv["id"]

    _post("/v1/invoiceitems", data={
        "customer": customer_id, "invoice": invoice_id,
        "amount": str(amount_cents), "currency": _validate_currency(currency), "description": description,
    })

    fin = _post("/v1/invoices/" + invoice_id + "/finalize")
    return {
        "type": "hosted_invoice", "invoice_id": invoice_id,
        "invoice_number": fin.get("number", ""), "amount_cents": amount_cents,
        "description": description, "customer_id": customer_id,
        "customer_email": email, "hosted_url": fin.get("hosted_invoice_url", ""),
        "invoice_pdf": fin.get("invoice_pdf", ""), "mode": MODE,
    }

def cmd_status(identifier):
    """Check payment status."""
    if identifier == "latest":
        pis = _get("/v1/payment_intents", {"limit": 1})
        data = pis.get("data", [])
        if not data:
            return {"error": "No payments found"}
        pi = _get("/v1/payment_intents/" + data[0]["id"],
                  {"expand[]": ["invoice", "customer", "latest_charge.payment_method_details"]})
        return _format_pi(pi)
    if identifier.startswith("in_"):
        inv = _get("/v1/invoices/" + identifier)
        return _format_invoice(inv)
    if identifier.startswith("pi_"):
        pi = _get("/v1/payment_intents/" + identifier,
                  {"expand[]": ["invoice", "customer", "latest_charge.payment_method_details"]})
        return _format_pi(pi)
    if identifier.startswith("plink_"):
        pl = _get("/v1/payment_links/" + identifier)
        return {"type": "payment_link", "url": pl.get("url"), "active": pl.get("active", False)}
    return {"error": "Unknown identifier: " + identifier}

def cmd_balance():
    bal = _get("/v1/balance")
    avail = bal.get("available", [{}])[0]
    pend = bal.get("pending", [{}])[0]
    return {
        "type": "balance",
        "available_cents": avail.get("amount", 0), "available_currency": avail.get("currency", "usd").upper(),
        "pending_cents": pend.get("amount", 0), "pending_currency": pend.get("currency", "usd").upper(),
        "livemode": bal.get("livemode", False), "mode": MODE,
    }

def cmd_products():
    products = _get("/v1/products", {"limit": 20, "expand[]": "data.default_price"})
    results = []
    for p in products.get("data", []):
        price = p.get("default_price")
        price_str = ""
        if price and isinstance(price, dict):
            price_str = f"${price.get('unit_amount', 0) / 100:.2f} {price.get('currency', 'usd').upper()}"
            if price.get("recurring"):
                price_str += f"/{price['recurring'].get('interval', '')}"
        results.append({
            "id": p["id"], "name": p.get("name", ""), "price": price_str,
            "description": p.get("description", ""), "active": p.get("active", False),
        })
    return {"type": "products", "count": len(results), "products": results}

def cmd_list_payments(limit=10):
    data = _paginate("/v1/payment_intents",
                     {"expand[]": ["data.invoice", "data.customer", "data.latest_charge"]},
                     limit=limit)
    return {"type": "payments_list", "count": len(data), "payments": [_format_pi(pi) for pi in data]}

def cmd_stats():
    """One-glance overview."""
    bal = _get("/v1/balance")
    avail = bal.get("available", [{}])[0]
    pend = bal.get("pending", [{}])[0]

    charges = _paginate("/v1/charges", {"created[gte]": str(int(time.time()) - 7*86400)}, limit=100)
    succeeded = [c for c in charges if c.get("status") == "succeeded"]
    failed = [c for c in charges if c.get("status") != "succeeded"]
    week_in = sum(c["amount"] for c in succeeded)

    open_inv = _paginate("/v1/invoices", {"status": "open"}, limit=50)
    total_open = sum(inv["amount_due"] for inv in open_inv if inv.get("amount_due"))

    refunds = _paginate("/v1/refunds", {"created[gte]": str(int(time.time()) - 7*86400)}, limit=100)
    week_out = sum(r["amount"] for r in refunds)

    overdue_count = sum(1 for inv in open_inv if (int(time.time()) - inv.get("created", 0)) / 86400 > 7)

    return {
        "type": "stats",
        "available_cents": avail.get("amount", 0), "available_currency": avail.get("currency", "usd").upper(),
        "pending_cents": pend.get("amount", 0), "pending_currency": pend.get("currency", "usd").upper(),
        "week_in_cents": week_in, "week_out_cents": week_out, "net_week_cents": week_in - week_out,
        "charge_count_week": len(succeeded), "failed_count_week": len(failed),
        "refund_count_week": len(refunds),
        "open_invoices": len(open_inv), "overdue_invoices": overdue_count,
        "total_open_cents": total_open, "mode": MODE,
    }

def cmd_followup():
    """Open invoices sorted by age."""
    data = _paginate("/v1/invoices", {"status": "open", "expand[]": "data.customer"}, limit=100)
    results = []
    now = int(time.time())
    for inv in data:
        age_days = (now - inv.get("created", 0)) / 86400
        cust = inv.get("customer")
        cust_email = ""
        if isinstance(cust, dict):
            cust_email = cust.get("email", "")
        results.append({
            "id": inv.get("id"), "number": inv.get("number", ""),
            "amount_cents": inv.get("amount_due", 0), "amount": inv.get("amount_due", 0) / 100.0,
            "currency": inv.get("currency", "usd").upper(),
            "created": inv.get("created"), "age_days": round(age_days, 1),
            "hosted_url": inv.get("hosted_invoice_url", ""),
            "customer_email": inv.get("customer_email") or cust_email,
            "invoice_pdf": inv.get("invoice_pdf", ""),
            "description": inv.get("description", ""),
        })
    results.sort(key=lambda x: x["age_days"], reverse=True)
    total_open = sum(r["amount_cents"] for r in results)
    return {"type": "followup", "count": len(results), "invoices": results, "total_open_cents": total_open}

def cmd_reconcile(hours=24):
    from_ts = int(time.time()) - (hours * 3600)
    data = _paginate("/v1/payment_intents",
                     {"created[gte]": str(from_ts), "expand[]": "data.invoice"},
                     limit=100)
    successful, pending, failed = [], [], []
    for pi in data:
        entry = {"id": pi.get("id"), "amount": pi.get("amount", 0) / 100.0,
                 "currency": pi.get("currency", "usd").upper(), "status": pi.get("status")}
        if pi.get("status") == "succeeded":
            successful.append(entry)
        elif pi.get("status") in ("requires_payment_method", "requires_confirmation"):
            pending.append(entry)
        else:
            failed.append(entry)
    return {"type": "reconciliation", "window_hours": hours,
            "successful": successful, "pending": pending, "failed": failed,
            "total_success": sum(p["amount"] for p in successful),
            "total_pending": sum(p["amount"] for p in pending)}

def cmd_refund(charge_id_or_pi, amount_cents=None, reason=None, dry_run=False):
    """Full/partial refund with dry-run safety."""
    if charge_id_or_pi.startswith("pi_"):
        pi = _get("/v1/payment_intents/" + charge_id_or_pi, {"expand[]": "latest_charge"})
        ch = pi.get("latest_charge", {})
        if not ch or not ch.get("id"):
            return {"error": f"No charge found for payment intent {charge_id_or_pi}"}
        charge_id = ch["id"]
        # For full refund, get the original amount
        original_amount = pi.get("amount", 0)
    else:
        charge_id = charge_id_or_pi
        # Fetch charge to get original amount for full refund display
        try:
            charge = _get("/v1/charges/" + charge_id)
            original_amount = charge.get("amount", 0)
        except Exception:
            original_amount = 0

    # Determine effective amount for display
    effective_amount = amount_cents if amount_cents else original_amount

    if dry_run:
        return {
            "type": "refund_preview",
            "charge_id": charge_id,
            "amount_cents": effective_amount,
            "refund_type": "FULL" if not amount_cents else f"PARTIAL (${amount_cents/100:.2f})",
            "reason": reason or "requested",
            "mode": MODE,
            "dry_run": True,
        }

    data = {"charge": charge_id}
    if amount_cents:
        _validate_amount(amount_cents, "refund amount")
        data["amount"] = str(amount_cents)
    if reason:
        data["reason"] = reason

    refund = _post("/v1/refunds", data=data)
    # BUG FIX: use actual refund amount from API response, not input
    actual_amount = refund.get("amount", effective_amount)
    full = "FULL" if not amount_cents else f"PARTIAL (${amount_cents/100:.2f})"
    return {
        "type": "refund", "refund_id": refund["id"], "charge_id": charge_id,
        "amount_cents": actual_amount, "refund_type": full,
        "reason": reason or "requested", "status": refund.get("status", ""), "mode": MODE,
    }

def cmd_portal(customer_email):
    """Customer self-service portal."""
    _validate_email(customer_email)
    custs = _get("/v1/customers", {"email": customer_email, "limit": 1})
    if not custs.get("data"):
        return {"error": f"No customer found for {customer_email}"}
    try:
        sess = _post("/v1/billing_portal/sessions", data={
            "customer": custs["data"][0]["id"],
            "return_url": "https://dashboard.stripe.com"
        })
        return {"type": "portal", "url": sess["url"], "customer_email": customer_email}
    except Exception as e:
        return {"error": f"Portal session failed: {_sanitize_error(e)}"}

def cmd_coupon(percent, code, description=None, duration="once", duration_in_months=None):
    """Create discount coupon."""
    if not (0 < percent <= 100):
        return {"error": "Percent must be between 1 and 100"}
    data = {
        "percent_off": str(percent),
        "id": code.replace(" ", "-").lower(),
        "duration": duration,
        "metadata[source]": "hermes-agent",
    }
    # BUG FIX: actually send description to Stripe API
    if description:
        data["name"] = description
    if duration == "repeating" and duration_in_months:
        data["duration_in_months"] = str(duration_in_months)
    coupon = _post("/v1/coupons", data=data)
    return {"type": "coupon", "code": coupon.get("id"), "percent_off": coupon.get("percent_off"),
            "duration": coupon.get("duration"), "valid": True}

def cmd_history(limit=10):
    with _history_db() as conn:
        rows = conn.execute("SELECT * FROM invoices ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
    results = [{"id": r[0], "invoice_id": r[1], "type": r[2], "amount_cents": r[3],
                "description": r[4], "url": r[5], "hosted_url": r[6], "invoice_pdf": r[7],
                "status": r[8], "created": r[9]} for r in rows]
    return {"type": "history", "count": len(results), "invoices": results}

# ── Customer & Product commands ────────────────────────────────────────

def cmd_customers(limit=20):
    data = _paginate("/v1/customers", limit=limit)
    results = []
    for c in data:
        results.append({
            "id": c["id"], "name": c.get("name", ""),
            "email": c.get("email", "no-email"),
            "created": c.get("created"),
            "balance": c.get("balance", 0),
        })
    return {"type": "customers", "count": len(results), "customers": results}

def cmd_customer_info(email_or_id):
    if email_or_id.startswith("cus_"):
        cust = _get("/v1/customers/" + email_or_id)
    else:
        _validate_email(email_or_id)
        custs = _get("/v1/customers", {"email": email_or_id, "limit": 1})
        if not custs.get("data"):
            return {"error": f"No customer found for {email_or_id}"}
        cust = _get("/v1/customers/" + custs["data"][0]["id"])
    # Also fetch recent activity
    subs = _get("/v1/subscriptions", {"customer": cust["id"], "limit": 3, "status": "active"})
    invs = _get("/v1/invoices", {"customer": cust["id"], "limit": 5})
    return {
        "type": "customer_info",
        "id": cust.get("id"), "name": cust.get("name", ""),
        "email": cust.get("email", ""), "created": cust.get("created"),
        "balance": cust.get("balance", 0),
        "active_subscriptions": len(subs.get("data", [])),
        "recent_invoices": [
            {"id": i["id"], "status": i.get("status"), "amount": i.get("amount_due", 0) / 100.0}
            for i in invs.get("data", [])
        ],
    }

def cmd_create_product(name, description=None, unit_amount_cents=None, currency="usd", recurring=None):
    data = {"name": name}
    if description:
        data["description"] = description
    currency = _validate_currency(currency)

    prod = _post("/v1/products", data=data)
    result = {
        "type": "product_created", "id": prod["id"], "name": name, "active": prod.get("active", True),
    }

    if unit_amount_cents:
        unit_amount_cents = _validate_amount(unit_amount_cents, "price")
        price_data = {
            "product": prod["id"], "unit_amount": str(unit_amount_cents),
            "currency": currency,
        }
        if recurring:
            price_data["recurring[interval]"] = recurring
        price = _post("/v1/prices", data=price_data)
        _post("/v1/products/" + prod["id"], data={"default_price": price["id"]})
        result["price_id"] = price["id"]
        result["unit_amount"] = unit_amount_cents
        result["recurring"] = recurring or "one-time"

    return result

# ── Subscriptions ──────────────────────────────────────────────────────

def cmd_subscriptions(limit=10, status="all"):
    params = {}
    if status and status not in ("all", ""):
        params["status"] = status
    data = _paginate("/v1/subscriptions", params=params, limit=limit)
    results = []
    for s in data:
        plan = s.get("plan", {})
        if not plan:
            items = s.get("items", {}).get("data", [])
            if items:
                plan = items[0].get("price", {})
        cust = s.get("customer", {})
        cust_email = ""
        if isinstance(cust, dict):
            cust_email = cust.get("email", "")
        elif isinstance(cust, str):
            try:
                c = _get("/v1/customers/" + cust)
                cust_email = c.get("email", "")
            except Exception:
                pass
        results.append({
            "id": s["id"], "status": s.get("status"),
            "plan": plan.get("nickname", plan.get("id", "")),
            "amount": plan.get("unit_amount", 0) / 100.0 if plan.get("unit_amount") else 0,
            "interval": plan.get("recurring", {}).get("interval", ""),
            "customer_email": cust_email,
            "customer_id": cust if isinstance(cust, str) else cust.get("id", ""),
            "created": s.get("created"),
            "period_end": s.get("current_period_end"),
        })
    total_mrr = sum(r["amount"] for r in results if r["status"] == "active" and r["interval"] == "month")
    return {"type": "subscriptions", "count": len(results), "subscriptions": results, "total_mrr": total_mrr}

def cmd_cancel_subscription(sub_id, at_period_end=False):
    """BUG FIX: Actually cancel the subscription."""
    if at_period_end:
        # Cancel at end of billing period
        sub = _post("/v1/subscriptions/" + sub_id, data={"cancel_at_period_end": "true"})
        return {
            "type": "subscription_cancelled",
            "id": sub["id"], "status": sub.get("status", ""),
            "cancel_at": sub.get("current_period_end"),
            "customer": sub.get("customer", ""),
            "mode": "cancel_at_period_end",
        }
    else:
        # Immediate cancellation — MUST use DELETE
        sub = _delete("/v1/subscriptions/" + sub_id)
        return {
            "type": "subscription_cancelled",
            "id": sub["id"], "status": sub.get("status", "canceled"),
            "customer": sub.get("customer", ""),
            "mode": "immediate",
        }

def cmd_webhooks():
    endpoints = _get("/v1/webhook_endpoints", {"limit": "10"})
    results = []
    for ep in endpoints.get("data", []):
        results.append({
            "id": ep["id"],
            "url": ep.get("url", ""),
            "status": ep.get("status", ""),
            "events": len(ep.get("enabled_events", [])),
        })
    return {"type": "webhooks", "count": len(results), "endpoints": results}


# ── Search ──────────────────────────────────────────────────────────────

def cmd_search(query):
    """Search across customers, invoices, and charges."""
    ql = query.lower()
    results = []
    try:
        for c in _paginate("/v1/customers", limit=50):
            if ql in (c.get("name") or "").lower() or ql in (c.get("email") or "").lower():
                results.append({"type": "customer", "id": c["id"],
                                "name": c.get("name", ""), "email": c.get("email", "")})
    except Exception:
        pass
    try:
        for inv in _paginate("/v1/invoices", {"limit": "50"}, limit=50):
            desc = (inv.get("description") or "").lower()
            num = (inv.get("number") or "").lower()
            em = (inv.get("customer_email") or "").lower()
            if ql in desc or ql in num or ql in em:
                results.append({"type": "invoice", "id": inv["id"],
                                "number": inv.get("number", ""),
                                "amount_cents": inv.get("amount_due", 0),
                                "status": inv.get("status", ""),
                                "description": inv.get("description", "")})
    except Exception:
        pass
    try:
        for ch in _paginate("/v1/charges", limit=50):
            desc = (ch.get("description") or "").lower()
            if ql in desc:
                results.append({"type": "charge", "id": ch["id"],
                                "amount_cents": ch.get("amount", 0),
                                "status": ch.get("status", ""),
                                "description": ch.get("description", "")})
    except Exception:
        pass
    return {"type": "search", "query": query, "count": len(results), "results": results}


# ── Receipt / Proof of Payment ──────────────────────────────────────────

def cmd_receipt(invoice_or_pi_id):
    """Proof of payment — fetch invoice/PI, show paid status, amount, date, payer, URL."""
    if invoice_or_pi_id.startswith("in_"):
        inv = _get("/v1/invoices/" + invoice_or_pi_id)
        if inv.get("status") != "paid":
            return {"type": "receipt", "error": "Invoice %s is %s, not paid" % (invoice_or_pi_id, inv.get("status")),
                    "status": inv.get("status")}
        cust_email = inv.get("customer_email", "")
        cust = inv.get("customer")
        if isinstance(cust, dict):
            cust_email = cust.get("email", cust_email)
        return {
            "type": "receipt", "source": "invoice",
            "id": inv["id"], "number": inv.get("number", ""),
            "amount_cents": inv.get("amount_paid", 0),
            "currency": inv.get("currency", "usd").upper(),
            "status": "paid",
            "paid_at": inv.get("status_transitions", {}).get("paid_at"),
            "customer_email": cust_email,
            "hosted_url": inv.get("hosted_invoice_url", ""),
            "invoice_pdf": inv.get("invoice_pdf", ""),
            "description": inv.get("description", ""),
        }
    elif invoice_or_pi_id.startswith("pi_"):
        pi = _get("/v1/payment_intents/" + invoice_or_pi_id,
                   {"expand[]": ["latest_charge", "invoice"]})
        if pi.get("status") != "succeeded":
            return {"type": "receipt", "error": "Payment %s is %s, not succeeded" % (invoice_or_pi_id, pi.get("status"))}
        ch = pi.get("latest_charge", {})
        inv = pi.get("invoice")
        inv_id = inv["id"] if isinstance(inv, dict) else inv
        return {
            "type": "receipt", "source": "payment_intent",
            "id": pi["id"], "charge_id": ch.get("id", ""),
            "amount_cents": pi.get("amount", 0),
            "currency": pi.get("currency", "usd").upper(),
            "status": "succeeded",
            "paid_at": ch.get("created"),
            "customer_email": ch.get("billing_details", {}).get("email", ""),
            "receipt_url": ch.get("receipt_url", ""),
            "invoice_id": inv_id or "",
            "description": ch.get("description", ""),
        }
    else:
        return {"error": "Unknown identifier: %s. Use in_xxx or pi_xxx." % invoice_or_pi_id}


# ── Customer Lifetime Value ─────────────────────────────────────────────

def cmd_ltv(email):
    """Customer lifetime value — total paid, refund rate, first/last payment."""
    _validate_email(email)
    custs = _get("/v1/customers", {"email": email, "limit": 1})
    if not custs.get("data"):
        return {"error": "No customer found for %s" % email}
    cust = custs["data"][0]
    cid = cust["id"]

    charges = _paginate("/v1/charges", {"customer": cid}, limit=100)
    succeeded = [c for c in charges if c.get("status") == "succeeded"]
    total_charged = sum(c["amount"] for c in succeeded)

    refunds = []
    for ch in succeeded:
        ch_refs = _get("/v1/charges/" + ch["id"] + "/refunds")
        refunds.extend(ch_refs.get("data", []))
    total_refunded = sum(r["amount"] for r in refunds)

    subs = _get("/v1/subscriptions", {"customer": cid, "limit": 20})
    active_subs = [s for s in subs.get("data", []) if s.get("status") == "active"]
    invs = _get("/v1/invoices", {"customer": cid, "limit": 50})
    paid_invs = [i for i in invs.get("data", []) if i.get("status") == "paid"]
    open_invs = [i for i in invs.get("data", []) if i.get("status") == "open"]

    first_charge = min((c["created"] for c in charges), default=None)
    last_charge = max((c["created"] for c in charges), default=None)

    return {
        "type": "ltv",
        "customer_email": email, "customer_id": cid,
        "customer_name": cust.get("name", ""),
        "total_charged_cents": total_charged,
        "total_refunded_cents": total_refunded,
        "net_revenue_cents": total_charged - total_refunded,
        "refund_rate": round(total_refunded / total_charged * 100, 1) if total_charged else 0,
        "charge_count": len(succeeded), "refund_count": len(refunds),
        "paid_invoices": len(paid_invs), "open_invoices": len(open_invs),
        "active_subscriptions": len(active_subs),
        "first_payment": first_charge, "last_payment": last_charge,
        "customer_since": cust.get("created"),
    }


# ── Duplicate Invoice ───────────────────────────────────────────────────

def cmd_duplicate(invoice_id):
    """Clone an existing invoice — same line items, new date."""
    if not invoice_id.startswith("in_"):
        return {"error": "Expected invoice ID (in_xxx), got: %s" % invoice_id}
    orig = _get("/v1/invoices/" + invoice_id)
    if orig.get("status") == "draft":
        return {"error": "Invoice %s is still draft" % invoice_id}

    items = _get("/v1/invoices/" + invoice_id + "/lines", {"limit": "100"})
    line_items = items.get("data", [])
    if not line_items:
        return {"error": "No line items found on source invoice"}

    customer_id = orig.get("customer")
    if not customer_id:
        return {"error": "Source invoice has no customer"}

    new_inv = _post("/v1/invoices", data={"customer": customer_id})
    new_id = new_inv["id"]

    for li in line_items:
        item_data = {
            "customer": customer_id, "invoice": new_id,
            "amount": str(li.get("amount", 0)),
            "currency": li.get("currency", "usd"),
            "description": li.get("description", "Duplicated item"),
        }
        _post("/v1/invoiceitems", data=item_data)

    fin = _post("/v1/invoices/" + new_id + "/finalize")
    return {
        "type": "invoice_duplicated",
        "source_invoice": invoice_id,
        "new_invoice_id": new_id,
        "new_number": fin.get("number", ""),
        "items_copied": len(line_items),
        "amount_cents": fin.get("amount_due", 0),
        "hosted_url": fin.get("hosted_invoice_url", ""),
        "invoice_pdf": fin.get("invoice_pdf", ""),
    }


# ── AR Aging Report ─────────────────────────────────────────────────────

def cmd_aging():
    """Accounts receivable aging — open invoices bucketed by age."""
    data = _paginate("/v1/invoices", {"status": "open", "expand[]": "data.customer"}, limit=100)
    now = int(time.time())
    buckets = {"0-30": [], "31-60": [], "61-90": [], "90+": []}

    for inv in data:
        age_days = (now - inv.get("created", 0)) / 86400
        cust = inv.get("customer")
        cust_email = cust.get("email", "") if isinstance(cust, dict) else ""
        entry = {
            "id": inv["id"], "number": inv.get("number", ""),
            "amount_cents": inv.get("amount_due", 0),
            "age_days": round(age_days, 1),
            "customer_email": inv.get("customer_email") or cust_email,
            "hosted_url": inv.get("hosted_invoice_url", ""),
        }
        if age_days <= 30:
            buckets["0-30"].append(entry)
        elif age_days <= 60:
            buckets["31-60"].append(entry)
        elif age_days <= 90:
            buckets["61-90"].append(entry)
        else:
            buckets["90+"].append(entry)

    totals = {k: sum(i["amount_cents"] for i in v) for k, v in buckets.items()}
    return {
        "type": "aging", "buckets": buckets,
        "bucket_totals_cents": totals,
        "total_open_cents": sum(totals.values()),
        "total_invoices": sum(len(v) for v in buckets.values()),
    }


# ── Bulk Refund from CSV ────────────────────────────────────────────────

def cmd_bulk_refund(csv_path, dry_run=True):
    """Bulk refunds from CSV. Columns: charge_id,amount_cents"""
    import csv as csv_mod
    if not os.path.exists(csv_path):
        return {"error": "File not found: %s" % csv_path}
    results = []
    with open(csv_path) as f:
        reader = csv_mod.DictReader(f)
        for row in reader:
            charge_id = row.get("charge_id") or row.get("id") or row.get("pi_id")
            amount_str = row.get("amount_cents") or row.get("amount")
            if not charge_id:
                results.append({"error": "Missing charge_id", "row": dict(row)})
                continue
            amount = None
            if amount_str:
                try:
                    amount = int(amount_str)
                except ValueError:
                    results.append({"error": "Invalid amount: %s" % amount_str, "charge_id": charge_id})
                    continue
            if dry_run:
                results.append({"charge_id": charge_id, "amount_cents": amount, "action": "would_refund", "dry_run": True})
            else:
                try:
                    results.append(cmd_refund(charge_id, amount_cents=amount))
                except Exception as e:
                    results.append({"charge_id": charge_id, "error": str(e)})
    return {"type": "bulk_refund", "dry_run": dry_run, "count": len(results), "results": results}


# ── Churn Analysis ──────────────────────────────────────────────────────

def cmd_churn():
    """Subscription churn analysis — canceled vs active, MRR loss."""
    all_subs = _paginate("/v1/subscriptions", {"status": "all"}, limit=100)
    active = [s for s in all_subs if s.get("status") == "active"]
    canceled = [s for s in all_subs if s.get("status") == "canceled"]
    past_due = [s for s in all_subs if s.get("status") == "past_due"]
    trialing = [s for s in all_subs if s.get("status") == "trialing"]

    def _mrr(subs):
        total = 0
        for s in subs:
            for item in s.get("items", {}).get("data", []):
                price = item.get("price", {})
                if price.get("recurring", {}).get("interval") == "month":
                    total += price.get("unit_amount", 0)
                elif price.get("recurring", {}).get("interval") == "year":
                    total += price.get("unit_amount", 0) // 12
        return total

    active_mrr = _mrr(active)
    canceled_mrr = _mrr(canceled)
    total_ever = len(all_subs)
    churn_rate = round(len(canceled) / total_ever * 100, 1) if total_ever else 0

    return {
        "type": "churn",
        "active": len(active), "canceled": len(canceled),
        "past_due": len(past_due), "trialing": len(trialing),
        "total_ever": total_ever, "churn_rate_pct": churn_rate,
        "active_mrr_cents": active_mrr, "lost_mrr_cents": canceled_mrr,
    }


# ── Tier Config & Bulk Links ────────────────────────────────────────────

TIER_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tiers.json")

def _load_tier_config():
    if not os.path.exists(TIER_CONFIG_PATH):
        return {}
    with open(TIER_CONFIG_PATH) as f:
        return json.load(f)

def cmd_sync_tiers():
    """Sync tiers.json from Stripe price nicknames."""
    products = _paginate("/v1/products", limit=100)
    config = {}

    for prod in products:
        prod_name = prod.get("name", "")
        prod_id = prod["id"]
        prices = _get("/v1/prices", {"product": prod_id, "limit": 100})
        price_list = prices.get("data", [])

        # Group by nickname pattern: "Product - RANGE units"
        tiers = {}
        moq = 30  # default
        for p in price_list:
            nickname = p.get("nickname", "")
            if not nickname or " - " not in nickname:
                continue
            # Parse: "Product Name - 100-199 units" or "Product Name - 1000+ units"
            parts = nickname.split(" - ", 1)
            if len(parts) != 2:
                continue
            range_part = parts[1].replace(" units", "").strip()

            # Determine min/max from range
            if range_part.endswith("+"):
                min_qty = int(range_part[:-1])
                max_qty = 9999
            elif "-" in range_part:
                rmin, rmax = range_part.split("-", 1)
                min_qty = int(rmin)
                max_qty = int(rmax)
            else:
                continue

            # Map range to tier label
            if min_qty == 1:
                label = "MOQ"
                moq = 30  # will be overridden if we find a specific MOQ
            elif min_qty == 10:
                label = "MOQ"
                moq = 10
            elif min_qty == 50 and max_qty == 99:
                label = "MOQ"
                moq = 50
            elif min_qty == 30 and max_qty == 99:
                label = "MOQ"
                moq = 30
            elif min_qty == 100:
                label = "A"
            elif min_qty == 200:
                label = "B"
            elif min_qty == 500:
                label = "C"
            elif min_qty == 1000:
                label = "D"
            else:
                continue

            tiers[label] = {
                "price_id": p["id"],
                "min": min_qty,
                "max": max_qty,
                "unit_amount": p.get("unit_amount", 0),
            }

        if tiers:
            config[prod_name] = {
                "product_id": prod_id,
                "moq": moq,
                "tiers": tiers,
            }

    with open(TIER_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

    return {
        "type": "sync_tiers",
        "products": len(config),
        "total_tiers": sum(len(p["tiers"]) for p in config.values()),
        "config_path": TIER_CONFIG_PATH,
    }

def cmd_bulklink(product, tier, customer_email=None):
    """Create a Checkout Session with enforced quantity range for a product tier."""
    config = _load_tier_config()
    if product not in config:
        available = ", ".join(sorted(config.keys()))
        return {"error": f"Product '{product}' not found. Available: {available}"}

    product_config = config[product]
    if tier not in product_config["tiers"]:
        available = ", ".join(sorted(product_config["tiers"].keys()))
        return {"error": f"Tier '{tier}' not found for {product}. Available: {available}"}

    tier_config = product_config["tiers"][tier]
    data = {
        "mode": "payment",
        "line_items[0][price]": tier_config["price_id"],
        "line_items[0][adjustable_quantity][enabled]": "true",
        "line_items[0][adjustable_quantity][minimum]": str(tier_config["min"]),
        "line_items[0][adjustable_quantity][maximum]": str(tier_config["max"]),
        "success_url": "https://dashboard.stripe.com/payments",
        "cancel_url": "https://dashboard.stripe.com/payments",
        "metadata[source]": "hermes-bulklink",
        "metadata[product]": product,
        "metadata[tier]": tier,
    }
    if customer_email:
        _validate_email(customer_email)
        data["customer_email"] = customer_email

    session = _post("/v1/checkout/sessions", data=data)

    return {
        "type": "bulk_link",
        "url": session["url"],
        "session_id": session["id"],
        "product": product,
        "tier": tier,
        "unit_price_cents": tier_config["unit_amount"],
        "min_qty": tier_config["min"],
        "max_qty": tier_config["max"],
        "customer_email": customer_email or "",
        "mode": MODE,
    }


# ── Pretty-print ────────────────────────────────────────────────────────

def _fmt_cents(cents, currency="USD"):
    return "$%.2f %s" % (cents / 100.0, currency)

def _fmt_ts(ts):
    if not ts:
        return "N/A"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

def _print_result(result):
    if not isinstance(result, dict):
        print(result)
        return
    rtype = result.get("type", "unknown")
    if "error" in result:
        print("Error: %s" % result["error"])
        return

    if rtype == "balance":
        print("Mode: %s" % result["mode"])
        print("Available: %s" % _fmt_cents(result["available_cents"], result["available_currency"]))
        print("Pending:   %s" % _fmt_cents(result["pending_cents"], result["pending_currency"]))

    elif rtype == "hosted_invoice":
        print("Invoice %s" % result.get("invoice_number", result["invoice_id"]))
        print("Amount: %s" % _fmt_cents(result["amount_cents"], result.get("currency", "USD").upper()))
        print("Status: %s" % result.get("status", "open"))
        if result.get("hosted_url"):
            print("URL: %s" % result["hosted_url"])
        if result.get("invoice_pdf"):
            print("PDF: %s" % result["invoice_pdf"])
        print("Mode: %s" % result["mode"])

    elif rtype == "pay_link":
        print("Payment Link: %s" % result["url"])
        print("Amount: %s" % _fmt_cents(result["amount_cents"]))
        print("Mode: %s" % result["mode"])

    elif rtype == "stats":
        print("=== Stripe Stats (%s) ===" % result["mode"])
        print("Available: %s" % _fmt_cents(result["available_cents"], result["available_currency"]))
        print("Pending:   %s" % _fmt_cents(result["pending_cents"], result["pending_currency"]))
        print("")
        print("Last 7 days:")
        print("  Income:  %s (%d charges)" % (_fmt_cents(result["week_in_cents"]), result["charge_count_week"]))
        print("  Refunds: %s (%d refunds)" % (_fmt_cents(result["week_out_cents"]), result["refund_count_week"]))
        print("  Net:     %s" % _fmt_cents(result["net_week_cents"]))
        print("  Failed:  %d" % result["failed_count_week"])
        print("")
        print("Open invoices: %d (%d overdue)" % (result["open_invoices"], result["overdue_invoices"]))
        print("Total open: %s" % _fmt_cents(result["total_open_cents"]))

    elif rtype == "followup":
        print("=== Open Invoices (%d) ===" % result["count"])
        print("Total open: %s" % _fmt_cents(result["total_open_cents"]))
        print()
        for inv in result["invoices"]:
            print("  %s" % (inv["number"] or inv["id"][:12]))
            print("    %s  |  %sd old" % (_fmt_cents(inv["amount_cents"], inv["currency"]), inv["age_days"]))
            print("    %s" % inv["customer_email"])
            if inv.get("hosted_url"):
                print("    %s" % inv["hosted_url"])
            print()

    elif rtype == "search":
        print('=== Search: "%s" (%d results) ===' % (result["query"], result["count"]))
        for r in result["results"]:
            if r["type"] == "customer":
                print("  [Customer] %s <%s> (%s)" % (r.get("name", ""), r.get("email", ""), r["id"]))
            elif r["type"] == "invoice":
                print("  [Invoice]  %s  %s  %s" % (r.get("number", r["id"][:12]), _fmt_cents(r["amount_cents"]), r.get("status", "")))
            elif r["type"] == "charge":
                print("  [Charge]   %s...  %s  %s" % (r["id"][:16], _fmt_cents(r["amount_cents"]), r.get("status", "")))

    elif rtype == "receipt":
        print("=== Receipt ===")
        print("Source: %s" % result["source"])
        print("Amount: %s" % _fmt_cents(result["amount_cents"], result.get("currency", "USD")))
        print("Status: %s" % result["status"])
        print("Paid:   %s" % _fmt_ts(result.get("paid_at")))
        print("Payer:  %s" % result.get("customer_email", ""))
        if result.get("hosted_url"):
            print("Invoice URL: %s" % result["hosted_url"])
        if result.get("invoice_pdf"):
            print("PDF: %s" % result["invoice_pdf"])
        if result.get("receipt_url"):
            print("Receipt: %s" % result["receipt_url"])

    elif rtype == "ltv":
        print("=== Customer LTV: %s ===" % result["customer_email"])
        print("Name: %s" % result.get("customer_name", "N/A"))
        print("Customer since: %s" % _fmt_ts(result.get("customer_since")))
        print("Total charged: %s" % _fmt_cents(result["total_charged_cents"]))
        print("Total refunded: %s" % _fmt_cents(result["total_refunded_cents"]))
        print("Net revenue: %s" % _fmt_cents(result["net_revenue_cents"]))
        print("Refund rate: %s%%" % result["refund_rate"])
        print("Charges: %d  |  Refunds: %d" % (result["charge_count"], result["refund_count"]))
        print("Paid invoices: %d  |  Open: %d" % (result["paid_invoices"], result["open_invoices"]))
        print("Active subscriptions: %d" % result["active_subscriptions"])
        if result.get("first_payment"):
            print("First payment: %s" % _fmt_ts(result["first_payment"]))
        if result.get("last_payment"):
            print("Last payment:  %s" % _fmt_ts(result["last_payment"]))

    elif rtype == "invoice_duplicated":
        print("Invoice duplicated!")
        print("Source:  %s" % result["source_invoice"])
        print("New:     %s (%s)" % (result["new_invoice_id"], result.get("new_number", "")))
        print("Items:   %d copied" % result["items_copied"])
        print("Amount:  %s" % _fmt_cents(result["amount_cents"]))
        if result.get("hosted_url"):
            print("URL: %s" % result["hosted_url"])

    elif rtype == "aging":
        print("=== Accounts Receivable Aging ===")
        print("Total open: %s (%d invoices)" % (_fmt_cents(result["total_open_cents"]), result["total_invoices"]))
        print()
        for bucket, label in [("0-30", "Current (0-30 days)"), ("31-60", "31-60 days"),
                               ("61-90", "61-90 days"), ("90+", "90+ days (critical)")]:
            total = result["bucket_totals_cents"][bucket]
            count = len(result["buckets"][bucket])
            print("  %s: %s (%d invoices)" % (label, _fmt_cents(total), count))
            for inv in result["buckets"][bucket]:
                print("    %s  %s  %sd  %s" % (inv["number"] or inv["id"][:12], _fmt_cents(inv["amount_cents"]), inv["age_days"], inv["customer_email"]))

    elif rtype == "bulk_refund":
        label = "DRY RUN" if result["dry_run"] else "LIVE"
        print("=== Bulk Refund (%s) ===" % label)
        print("Processed: %d" % result["count"])
        for r in result["results"]:
            if r.get("error"):
                print("  ERROR: %s (%s)" % (r["error"], r.get("charge_id", "")))
            elif r.get("dry_run"):
                amt = _fmt_cents(r["amount_cents"]) if r.get("amount_cents") else "FULL"
                print("  Would refund: %s %s" % (r["charge_id"], amt))
            else:
                print("  Refunded: %s %s (%s)" % (r.get("refund_id", ""), _fmt_cents(r.get("amount_cents", 0)), r.get("refund_type", "")))

    elif rtype == "churn":
        print("=== Subscription Churn Analysis ===")
        print("Active: %d  |  Canceled: %d  |  Past due: %d  |  Trialing: %d" % (result["active"], result["canceled"], result["past_due"], result["trialing"]))
        print("Total ever: %d" % result["total_ever"])
        print("Churn rate: %s%%" % result["churn_rate_pct"])
        print("Active MRR: %s" % _fmt_cents(result["active_mrr_cents"]))
        print("Lost MRR:   %s" % _fmt_cents(result["lost_mrr_cents"]))

    elif rtype == "products":
        print("=== Products (%d) ===" % result["count"])
        for p in result["products"]:
            status = "active" if p["active"] else "inactive"
            print("  %s (%s) [%s]" % (p["name"], p["id"], status))
            if p.get("price"):
                print("    Price: %s" % p["price"])
            if p.get("description"):
                print("    %s" % p["description"])

    elif rtype == "customers":
        print("=== Customers (%d) ===" % result["count"])
        for c in result["customers"]:
            print("  %s <%s> (%s)" % (c.get("name", "No name"), c["email"], c["id"]))

    elif rtype == "customer_info":
        print("=== Customer: %s ===" % result.get("name", "No name"))
        print("Email: %s" % result["email"])
        print("ID: %s" % result["id"])
        print("Since: %s" % _fmt_ts(result.get("created")))
        print("Active subs: %d" % result["active_subscriptions"])
        if result.get("recent_invoices"):
            print("Recent invoices:")
            for i in result["recent_invoices"]:
                print("  %s...  %s  %s" % (i["id"][:16], _fmt_cents(int(i["amount"] * 100)), i["status"]))

    elif rtype == "subscriptions":
        print("=== Subscriptions (%d) ===" % result["count"])
        print("Active MRR: %s" % _fmt_cents(int(result["total_mrr"] * 100)))
        print()
        for s in result["subscriptions"]:
            print("  %s...  %s  %s/%s  %s" % (s["id"][:16], s["status"], _fmt_cents(int(s["amount"] * 100)), s["interval"], s["customer_email"]))

    elif rtype in ("refund", "refund_preview"):
        prefix = "DRY RUN — " if result.get("dry_run") else ""
        print("%sRefund %s — %s" % (prefix, result["refund_type"], _fmt_cents(result["amount_cents"])))
        print("Refund ID: %s" % result.get("refund_id", "preview"))
        print("Reason: %s" % result["reason"])
        print("Mode: %s" % result["mode"])

    elif rtype == "portal":
        print("Billing portal: %s" % result["url"])
        print("Customer: %s" % result["customer_email"])

    elif rtype == "coupon":
        print("Coupon created: %s" % result["code"])
        print("Discount: %s%% off" % result["percent_off"])
        print("Duration: %s" % result["duration"])

    elif rtype == "webhooks":
        print("=== Webhook Endpoints (%d) ===" % result["count"])
        for ep in result["endpoints"]:
            print("  %s  [%s]  %d events" % (ep["url"], ep["status"], ep["events"]))

    elif rtype == "product_created":
        print("Product created: %s (%s)" % (result["name"], result["id"]))
        if result.get("price_id"):
            print("Price: %s — %s/%s" % (result["price_id"], _fmt_cents(result["unit_amount"]), result["recurring"]))

    elif rtype == "subscription_cancelled":
        print("Subscription cancelled: %s" % result["id"])
        print("Mode: %s" % result["mode"])
        print("Status: %s" % result["status"])

    elif rtype == "reconciliation":
        print("=== Reconciliation (last %dh) ===" % result["window_hours"])
        print("Successful: %d — %s" % (len(result["successful"]), _fmt_cents(int(result["total_success"] * 100))))
        print("Pending:    %d — %s" % (len(result["pending"]), _fmt_cents(int(result["total_pending"] * 100))))
        print("Failed:     %d" % len(result["failed"]))

    elif rtype == "history":
        print("=== Invoice History (%d) ===" % result["count"])
        for inv in result["invoices"]:
            print("  %s...  %s  %s" % (inv.get("invoice_id", inv["id"])[:16], _fmt_cents(inv["amount_cents"]), inv.get("description", "")))

    elif rtype == "payments_list":
        print("=== Recent Payments (%d) ===" % result["count"])
        for p in result["payments"]:
            print("  %s...  %s  %s" % (p.get("id", "")[:16], _fmt_cents(p.get("amount_cents", 0)), p.get("status", "")))

    elif rtype == "sync_tiers":
        print("Tiers synced: %d products, %d total tiers" % (result["products"], result["total_tiers"]))
        print("Config: %s" % result["config_path"])

    elif rtype == "bulk_link":
        print("Bulk Link: %s" % result["url"])
        print("Product: %s" % result["product"])
        print("Tier: %s (%d-%d units)" % (result["tier"], result["min_qty"], result["max_qty"]))
        print("Price: %s/unit" % _fmt_cents(result["unit_price_cents"]))
        if result.get("customer_email"):
            print("Customer: %s" % result["customer_email"])
        print("Mode: %s" % result["mode"])

    else:
        print(json.dumps(result, indent=2, default=str))


# ── CLI ─────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Stripe Payments — Unified CLI (v%s)" % VERSION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s invoice 5000 "Consulting" --email user@example.com
  %(prog)s paylink 5000 "Widget" --currency eur
  %(prog)s send 5000 user@example.com "Consulting"
  %(prog)s status latest
  %(prog)s refund ch_xxx --dry-run
  %(prog)s search "john"
  %(prog)s ltv user@example.com
  %(prog)s aging
  %(prog)s receipt in_xxx
  %(prog)s bulk-refund refunds.csv --dry-run""",
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + VERSION)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("invoice", help="Create hosted invoice")
    p.add_argument("amount", type=int, help="Amount in cents")
    p.add_argument("description", help="Line item description")
    p.add_argument("--email", help="Customer email")
    p.add_argument("--name", help="Customer name")
    p.add_argument("--currency", default="usd")

    p = sub.add_parser("paylink", help="Stripe Checkout payment link")
    p.add_argument("amount", type=int)
    p.add_argument("description")
    p.add_argument("--email")
    p.add_argument("--currency", default="usd")

    p = sub.add_parser("send", help="Hosted invoice emailed to client")
    p.add_argument("amount", type=int)
    p.add_argument("email")
    p.add_argument("description")
    p.add_argument("--name")
    p.add_argument("--currency", default="usd")

    p = sub.add_parser("status", help="Check payment/invoice status")
    p.add_argument("identifier")

    sub.add_parser("balance", help="Stripe balance")
    sub.add_parser("stats", help="One-glance overview")
    sub.add_parser("followup", help="Open invoices by age")

    p = sub.add_parser("list", help="Recent payments")
    p.add_argument("limit", nargs="?", type=int, default=10)

    p = sub.add_parser("refund", help="Full/partial refund")
    p.add_argument("charge_id")
    p.add_argument("amount", nargs="?", type=int, default=None)
    p.add_argument("reason", nargs="?", default=None)
    p.add_argument("--dry-run", action="store_true")

    sub.add_parser("products", help="List products")

    p = sub.add_parser("customers", help="List customers")
    p.add_argument("limit", nargs="?", type=int, default=20)

    p = sub.add_parser("customer", help="Customer details")
    p.add_argument("email_or_id")

    p = sub.add_parser("newproduct", help="Create product + price")
    p.add_argument("name")
    p.add_argument("amount", nargs="?", type=int, default=None)
    p.add_argument("--interval", choices=["day", "week", "month", "year"])
    p.add_argument("--currency", default="usd")

    p = sub.add_parser("subscriptions", help="List subscriptions")
    p.add_argument("limit", nargs="?", type=int, default=10)
    p.add_argument("--status", default="all")

    p = sub.add_parser("cancelsub", help="Cancel subscription")
    p.add_argument("sub_id")
    p.add_argument("--at-period-end", action="store_true")

    sub.add_parser("webhooks", help="List webhook endpoints")

    p = sub.add_parser("portal", help="Customer billing portal")
    p.add_argument("email")

    p = sub.add_parser("coupon", help="Create coupon")
    p.add_argument("percent", type=float)
    p.add_argument("code")
    p.add_argument("description", nargs="?", default=None)
    p.add_argument("--duration", default="once", choices=["once", "repeating", "forever"])
    p.add_argument("--months", type=int, default=None)

    p = sub.add_parser("reconcile", help="Match Stripe to reality")
    p.add_argument("hours", nargs="?", type=int, default=24)

    p = sub.add_parser("history", help="Local invoice history")
    p.add_argument("limit", nargs="?", type=int, default=10)

    p = sub.add_parser("search", help="Search customers/invoices/charges")
    p.add_argument("query")

    p = sub.add_parser("receipt", help="Proof of payment")
    p.add_argument("id")

    p = sub.add_parser("ltv", help="Customer lifetime value")
    p.add_argument("email")

    p = sub.add_parser("duplicate", help="Duplicate an invoice")
    p.add_argument("invoice_id")

    sub.add_parser("aging", help="AR aging report")

    p = sub.add_parser("bulk-refund", help="Bulk refunds from CSV")
    p.add_argument("csv_path")
    p.add_argument("--dry-run", action="store_true", default=True)
    p.add_argument("--live", action="store_true")

    sub.add_parser("churn", help="Subscription churn analysis")

    sub.add_parser("sync-tiers", help="Sync tiers.json from Stripe price nicknames")

    p = sub.add_parser("bulklink", help="Checkout session with enforced quantity range")
    p.add_argument("product", help="Product name")
    p.add_argument("--tier", required=True, choices=["MOQ", "A", "B", "C", "D"], help="Pricing tier")
    p.add_argument("--email", help="Customer email")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "invoice": lambda: cmd_invoice(args.amount, args.description, email=args.email, customer_name=args.name, currency=args.currency),
        "paylink": lambda: cmd_pay_link(args.amount, args.description, customer_email=args.email, currency=args.currency),
        "send": lambda: cmd_send(args.amount, args.email, args.description, customer_name=args.name, currency=args.currency),
        "status": lambda: cmd_status(args.identifier),
        "balance": cmd_balance,
        "stats": cmd_stats,
        "followup": cmd_followup,
        "list": lambda: cmd_list_payments(args.limit),
        "refund": lambda: cmd_refund(args.charge_id, amount_cents=args.amount, reason=args.reason, dry_run=args.dry_run),
        "products": cmd_products,
        "customers": lambda: cmd_customers(args.limit),
        "customer": lambda: cmd_customer_info(args.email_or_id),
        "newproduct": lambda: cmd_create_product(args.name, unit_amount_cents=args.amount, currency=args.currency, recurring=args.interval),
        "subscriptions": lambda: cmd_subscriptions(args.limit, status=args.status),
        "cancelsub": lambda: cmd_cancel_subscription(args.sub_id, at_period_end=args.at_period_end),
        "webhooks": cmd_webhooks,
        "portal": lambda: cmd_portal(args.email),
        "coupon": lambda: cmd_coupon(args.percent, args.code, description=args.description, duration=args.duration, duration_in_months=args.months),
        "reconcile": lambda: cmd_reconcile(args.hours),
        "history": lambda: cmd_history(args.limit),
        "search": lambda: cmd_search(args.query),
        "receipt": lambda: cmd_receipt(args.id),
        "ltv": lambda: cmd_ltv(args.email),
        "duplicate": lambda: cmd_duplicate(args.invoice_id),
        "aging": cmd_aging,
        "bulk-refund": lambda: cmd_bulk_refund(args.csv_path, dry_run=not args.live),
        "churn": cmd_churn,
        "sync-tiers": cmd_sync_tiers,
        "bulklink": lambda: cmd_bulklink(args.product, tier=args.tier, customer_email=args.email),
    }

    fn = dispatch.get(args.command)
    if not fn:
        print("Unknown command: %s" % args.command)
        sys.exit(1)
    result = fn()
    _print_result(result)


if __name__ == "__main__":
    main()
