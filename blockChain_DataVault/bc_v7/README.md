# DataVault — Admin & User Panel

A Flask + SQLite + Bootstrap project with two panels:

- **Admin Panel** — manage datasets, approve/reject user requests, view the blockchain ledger, and review activity history.
- **User Panel** — browse datasets and request modifications.

Both roles share a single login page and a single sign-up page (with a role selector).

## 1. Setup

```bash
cd datavault
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Run

```bash
python app.py
```

The app starts at **http://127.0.0.1:5000**.
The SQLite database is created automatically on first run at `instance/datavault.db`.

## 3. Create your first accounts

There's no pre-seeded login — use the **Sign Up** page to create:

1. An **Admin** account (select "Admin" as the role).
2. A **User** account (select "User" as the role).

Then log in from the same login page, choosing the matching role.

## 4. What's inside

```
datavault/
├── app.py              # Flask routes (auth, admin, user)
├── database.py         # SQLite helpers + blockchain hashing logic
├── schema.sql          # Table definitions
├── requirements.txt
├── instance/           # datavault.db is created here
├── uploads/            # uploaded dataset files are stored here
├── static/
│   ├── css/style.css   # design tokens + all page styling
│   └── js/script.js    # confirm modals, sidebar, notifications
└── templates/
    ├── login.html
    ├── signup.html
    ├── error.html
    ├── admin/
    │   ├── base_admin.html   # sidebar + topbar shell
    │   ├── datasets.html     # Page 1
    │   ├── requests.html     # Page 2
    │   ├── blockchain.html   # Page 3
    │   ├── history.html      # Page 4
    │   └── profile.html
    └── user/
        ├── base_user.html
        ├── dashboard.html
        ├── my_requests.html
        └── profile.html
```

## 5. How the blockchain log works

Every important admin action (dataset upload, dataset delete, request approve/reject,
and user modification requests) is written as a new **block**:

```
hash = SHA-256( index + data + previous_hash + timestamp )
```

Each block stores the hash of the block before it, so the chain can be visually
inspected on the **Blockchain Logs** page. This is a simplified educational model
of a blockchain (not a distributed/consensus network) — enough to demonstrate
tamper-evident logging inside the app.

## 6. Notes / next steps you may want to add

- Swap SQLite for PostgreSQL/MySQL by changing `database.py`'s connection logic if you need multi-user concurrency at scale.
- Add email verification on sign-up.
- Add pagination on the Blockchain Logs / History tables once they grow large.
- The "View Dataset" action currently downloads the raw file — you can build a
  preview page for CSV/JSON files if you want in-browser viewing.
