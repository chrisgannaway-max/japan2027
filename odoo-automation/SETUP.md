# Getting it running

Three stages, and **nothing costs money until the last one**. You can test every feature,
including e-mail and posting to Odoo, without signing up for anything.

---

## 1. On your own machine (free, no accounts)

```bash
git clone <repo> && cd odoo-automation
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

# OCR for scanned invoices. Skip it and everything else still works.
sudo apt-get install tesseract-ocr        # macOS: brew install tesseract

cp config/properties.example.yaml config/properties.yaml
cp config/users.example.yaml       config/users.yaml
cp config/expense_categories.example.yaml config/expense_categories.yaml

PORTAL_SECRET=$(python3 -c "import secrets;print(secrets.token_hex(32))") python3 -m portal serve
```

Open http://localhost:8000 and sign in as `admin` / `admin`. Drop a night-audit PDF on the
Night audit page and you should get a balanced entry. `python -m pytest -q` runs the 88 tests.

**Change the example passwords** before this is reachable by anyone else:
`python3 -m portal hash 'the real password'` and paste the result as `password_hash`.

---

## 2. Testing the two things that talk to the outside world (still free)

### E-mail, without an account anywhere

Run a mail catcher in one terminal:

```bash
python3 -m aiosmtpd -n -l 127.0.0.1:8025
```

and start the portal pointing at it:

```bash
SMTP_HOST=127.0.0.1 SMTP_PORT=8025 SMTP_SECURITY=none \
SMTP_FROM=nightaudit@example.com PORTAL_BASE_URL=http://localhost:8000 \
PORTAL_SECRET=dev python3 -m portal serve
```

Every message the portal sends is printed in the first terminal, reset links included. Enough
to prove the whole forgotten-password flow before anyone signs up for a mail service.

When you want to see a real message arrive in a real inbox, Resend's free tier sends from
`onboarding@resend.dev` with no domain and no DNS records. That is the point at which you
know exactly what to ask the client for.

### Odoo, without a subscription

Odoo Community is free and open source. One command gives you a throwaway instance with full
API access, which is the only way to test posting properly:

```bash
docker run -d --name odoo-db -e POSTGRES_PASSWORD=odoo -e POSTGRES_USER=odoo postgres:16
docker run -d --name odoo -p 8069:8069 --link odoo-db:db odoo:17
```

Open http://localhost:8069, create a database, install **Invoicing**, then make a user with
Accounting rights and an API key under Preferences, Account Security. Point the portal at it:

```bash
ODOO_URL=http://localhost:8069 ODOO_DB=<database> ODOO_TRANSPORT=xmlrpc \
ODOO_USER=<login> ODOO_API_KEY=<key> DELIVERY_MODE=odoo python3 -m portal serve
```

`python3 -m pms_to_odoo check` tells you whether it connected. This also answers a live
question for the client: whether the entries land the way their accountant expects.

---

## 3. Putting it somewhere they can reach (the first money)

Only needed once you want the client using it daily.

| What | Service | Plan | Cost |
|---|---|---|---|
| Hosting | Render, Railway or Fly.io, from the `Dockerfile` | 2 GB instance | ~$25/mo |
| Database and files | Supabase | Pro | ~$25/mo |
| E-mail | Resend or Postmark | free tier is enough | $0 |
| Domain | any registrar | — | ~$15/yr |

A free hosting tier is fine for showing them: it sleeps when idle and wakes on the first
request, which is only awkward in a live demo. Do not run OCR on a 512 MB instance; rendering
a scanned page at 300 dpi will exhaust it.

Set these in the host's dashboard rather than in the repository:

```
PORTAL_SECRET=<64 random hex characters>     # sessions are invalidated if this changes
PORTAL_STORE=db                              # manage properties and logins in the browser
PORTAL_SECURE_COOKIES=always
PORTAL_MFA_ROLES=admin
PORTAL_BASE_URL=https://<the public address>
```

Then `python3 -m portal seed` once to copy the config files into the database, and use the
Settings and E-mail setup pages from there.
