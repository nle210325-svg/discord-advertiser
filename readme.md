# Discord Advertiser Pro

Multi-user Discord auto-advertiser platform.

## Deployment

Deploy to Railway with one click.
```

---

## 🎯 Quick Action Plan:

**Step 1: DELETE these files:**
- `__pycache__/`
- `advertiser.db`
- `config.json`
- `config.py`
- `FEATURES.md`
- `main.py`
- `proxies.txt`
- `setup.bat`
- `setup.sh`
- `start.bat`
- `start.py`
- `tokens.txt`
- `Ui.py`

**Step 2: CREATE these 4 new files:**
- `.gitignore` (content above)
- `Procfile` (content above)
- `runtime.txt` (content above)
- `README.md` (content above)

**Step 3: UPDATE this file:**
- `requirements.txt` (replace with content above)

**Step 4: KEEP these:**
- ✅ `web_server_multiuser.py`
- ✅ `templates/` folder
- ✅ `static/` folder

---

## 📂 Final Structure Should Be:
```
discord-advertiser/
├── .gitignore          ← CREATE
├── Procfile            ← CREATE
├── README.md           ← CREATE
├── requirements.txt    ← UPDATE
├── runtime.txt         ← CREATE
├── web_server_multiuser.py  ← KEEP
├── templates/          ← KEEP
└── static/             ← KEEP