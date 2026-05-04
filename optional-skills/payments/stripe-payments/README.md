# Stripe Payments Agent Skill

Turn your AI agent into a Stripe payments powerhouse. Create invoices, manage customers, track subscriptions, process refunds, and generate business intelligence — all from a single command.

Built for [Hermes Agent](https://github.com/NousResearch/hermes-agent) and compatible with any agent that supports the [Agent Skills specification](https://agentskills.io/specification).

## Quick Install

### Hermes Agent
```bash
hermes skills install welliv/stripe-payments
```

### Manual Install
[Download](https://github.com/welliv/stripe-payments/archive/refs/heads/master.zip) and extract to your agent's skills directory.

## Requirements

- Python 3.9+
- `httpx` — `pip install httpx` (or `pip install --break-system-packages httpx`)
- A Stripe API key — [get one here](https://dashboard.stripe.com/apikeys)

**Recommended:** Use a Restricted Key (`rk_live_`) with scoped permissions for safety.

### Required Stripe Permissions
Your key needs these permissions enabled:
- Invoices (Read & Write)
- Customers (Read & Write)
- Payment Intents (Read)
- Charges & Refunds (Read & Write)
- Products & Prices (Read & Write)
- Subscriptions (Read)
- Balance (Read)

## Setup

1. Set your Stripe API key in `~/.hermes/.env`:
```bash
echo 'STRIPE_API_KEY=sk_live_xxxxx' >> ~/.hermes/.env
```

2. Verify it's working:
```bash
cd ~/.hermes/skills/stripe-payments/scripts
python3 stripe.py balance
```

## Commands (27 total)

### 💳 Core Payments
| Command | What it does |
|---------|-------------|
| `invoice` | Create a hosted invoice with PDF |
| `paylink` | Stripe Checkout page (auto-captures customer info) |
| `send` | Hosted invoice + email to client |
| `status` | Check payment or invoice status |

### 📊 Business Intelligence
| Command | What it does |
|---------|-------------|
| `stats` | One-glance overview: income, refunds, open invoices |
| `followup` | Open invoices sorted by age, with payment links |
| `list` | Recent payments with receipt URLs |
| `reconcile` | Match Stripe against your expectations |
| `history` | Local invoice history (SQLite tracking) |

### 👥 Customers & Products
| Command | What it does |
|---------|-------------|
| `customers` | List all customers |
| `customer` | View specific customer details |
| `products` | List all products with prices |
| `newproduct` | Create a product + price (one-time or recurring) |

### 🔄 Subscriptions
| Command | What it does |
|---------|-------------|
| `subscriptions` | List subscriptions + active MRR |
| `cancelsub` | Cancel a subscription immediately |

### ⚙️ Management
| Command | What it does |
|---------|-------------|
| `balance` | Show Stripe balance |
| `refund` | Full/partial refund (with `--dry-run` safety) |
| `portal` | Generate customer billing portal link |
| `coupon` | Create discount coupons |
| `webhooks` | List webhook endpoints |

### 🧠 MCP-proof Commands (not in Stripe MCP)
| Command | What it does |
|---------|-------------|
| `search` | Search across customers, invoices, and charges |
| `receipt` | Proof of payment with receipt URL |
| `ltv` | Customer lifetime value (total paid, refund rate, first/last) |
| `duplicate` | Clone invoice — same line items, new date |
| `aging` | AR aging report — open invoices by age bucket |
| `bulk-refund` | Bulk refunds from CSV with dry-run |
| `churn` | Subscription churn analysis (active vs canceled, MRR loss) |

## Example Prompts

> Create a $500 invoice for consulting work and send it to client@example.com

> Show me my Stripe overview — how much came in this week and what's outstanding

> List all my open invoices and sort them by what's been overdue the longest

> Create a monthly subscription product called "Pro Plan" for $49/month

> Show me all my customers and their details

> Process a full refund for the latest charge, but preview it first so I can confirm

> Create a checkout payment link for $25 for "Workshop access" where anyone can pay without needing to pre-register

## Architecture

### Invoice vs PayLink
- **`invoice`** — HostedStripe invoice with number, PDF, hosted URL. Tied to a customer record. Use when you **know** who you're billing.
- **`paylink`** — Stripe Checkout session where the customer enters their Name, Email, and Billing info. Auto-creates a Customer record. Use when you **don't** know the payer yet.

### Live Mode Detection
The script auto-detects your mode from the key prefix:
- `sk_test_` → **TEST** mode
- `sk_live_` or `rk_live_` → **LIVE** mode — real money, real invoices

Always run `python3 stripe.py balance` after switching to live mode to confirm.

### Refund Safety
In live mode, **always preview refunds first**:
```bash
python3 stripe.py refund --dry-run ch_xxxxx    # Full refund preview
python3 stripe.py refund --dry-run ch_xxxxx 5000  # $50 partial refund preview
```

## Project Structure

```
stripe-payments/
├── SKILL.md              → Agent instructions (Skill spec)
├── README.md             → Human-readable docs
└── scripts/
    └── stripe.py         → Main script (~1,325 lines, one dependency)
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `STRIPE_API_KEY not set` | Add it to `~/.hermes/.env` |
| `Permission denied` | Your restricted key is missing a required permission — add it in Stripe Dashboard |
| `ModuleNotFoundError: No module named 'httpx'` | `pip install httpx` |
| Live invoice shows `test_` URL | Verify your key prefix in `.env` — it should start with `sk_live_` or `rk_live_` |

## License

MIT
