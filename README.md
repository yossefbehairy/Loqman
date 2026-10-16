# Loqman — لقمان | Local-First Multi-Agent Workflow Desktop App

![CI](https://github.com/yossefbehairy/Loqman/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-blue.svg)
![Python: 3.13](https://img.shields.io/badge/python-3.13-blue.svg)
![Node: 22](https://img.shields.io/badge/node-22-green.svg)

> **AgentOS** engine, published under the **Loqman** repository.
> تطبيق ديسكتوب يعمل محليًا بالكامل لتنسيق وكلاء ذكاء اصطناعي عبر محادثة تنفيذية، عقود حوكمة، خريطة تفاعلية، وبث جماعي — مع FastAPI + React + Electron.

**English:** Local-first desktop studio to coordinate specialized AI agents across
departments (Planning, Research, QA) — chat with managers, enforce execution
contracts, watch live telemetry on an agent map, and audit multi-turn handovers.
No cloud lock-in: SQLite locally, free-model-only runtime, frozen offline installer.

---

## ✨ المميزات | Features

- **Executive Chat (R1):** محادثة بأسلوب Grok/ChatGPT مع pill لاختيار المدير
  (General Manager / د. سارة / م. طارق / أ. ياسمين) وتنقل جانبي من 5 views.
- **Company & Contracts (R2/R3):** هيكل إدارات هرمي + شارات (👑 Manager / 💼 Specialist)
  + دعم أسماء عربية طبيعية + تعديل System Prompt وعقد التنفيذ
  (strict JSON, retries 1–3, timeout, موديلات مجانية فقط).
- **Interactive Agent Map (R4):** مناطق أقسام مرئية + halos نابضة للحالة
  (`running` / `completed` بالمللي ثانية / `error`) + Contract Card عند الضغط.
- **Team Chat Stream (R5):** فيد زمني بأسلوب Slack/Discord للـ handovers
  (تفويض → تسليم → مراجعة) + JSON قابل للتوسيع + SSE حي.
- **Studio (Legacy):** DAG canvas بخمس عقد، تحقق طوبولوجي، تنفيذ + SSE drawer،
  سجل تنفيذ SQLite.
- **Desktop:** Electron + PyInstaller sidecar (`agentos-backend.exe`) — تشغيل بدون
  Python/Node على جهاز المستخدم، بورت ديناميكي، SQLite في `%APPDATA%\AgentOS`.
- **Free-model guardrails:** الكتالوج الحي هو المرجع؛ أي موديل غير مؤكد
  `0/0 + active` يُرفض — لا fallback مدفوع.

## 🏗️ المعمارية | Architecture

```text
Loqman-Setup-x.x.x.exe (NSIS)
└── AgentOS.exe (Electron)
    ├── app.asar → electron/ (main, preload, splash) + dist/ (React build)
    └── resources/agentos-backend.exe (FastAPI frozen, PyInstaller onefile)

Runtime:
  Electron ──spawns──▶ backend --port <free> --data-dir <userData>
  Electron ──polls───▶ GET /api/health
  Renderer (file://) ─▶ window.__AGENTOS_API__ = http://127.0.0.1:<port>/api
  SQLite ────────────▶ %APPDATA%\AgentOS\agentos.db
  AI runtime (external, not bundled) → OpenCode @ http://127.0.0.1:49100
```

| Layer | Tech |
| ----- | ---- |
| Backend | FastAPI, SQLAlchemy (SQLite WAL), Pydantic v2, SSE (`sse-starlette`), pytest |
| Frontend | React 19, Vite, Tailwind, xyflow, Electron 44 |
| Runtime | OpenCode CLI transport (`opencode api …`) + deterministic `MockRuntime` للاختبارات |
| Tests | pytest (backend) + Playwright Chromium (frontend E2E R1–R5 + Studio) |

## 📁 هيكل المشروع | Structure

```text
backend/
  app/
    api/routes/      # health, models, chat, missions, ceo, workflows, executions, agent_profiles
    core/            # lifecycle (crash recovery)
    db/              # session, models, repository (+ seed 10 profiles)
    engine/          # executor, state_machine, validator, context, events
    orchestration/   # ceo, planner, agent_loop
    runtime/         # opencode_runtime (real), mock_runtime (tests), base, errors
    config.py cli.py main.py
  tests/             # 20+ pytest suites
  requirements.txt
frontend/
  src/
    api/             # client.ts (single API entry) + opencode.ts
    components/      # chat, company, map, stream, canvas, inspector, settings…
    data/ types/ utils/
  electron/          # main.cjs, preload.cjs, splash.html
  e2e/               # r1..r5 + studio specs
  vite.config.ts (base './' — لا تحذفها، مطلوبة لـ file://)
pyinstaller/         # build-backend.ps1, smoke-test.ps1
docs/                # architecture.html, ceo-control-plan.md
DESKTOP.md TEST_INFRA.md TEST_READY.md
```

## 🚀 التشغيل السريع | Quickstart

### المتطلبات

- Python 3.13 + `backend/requirements.txt`
- Node 22 + `frontend/node_modules`
- (للمهام الحية فقط) OpenCode server على `http://127.0.0.1:49100`

### 1) Backend

```powershell
cd D:\AgentOS
copy .env.example .env
# فعّل venv وثبّت
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# شغّل من جذر الريبو
cd ..
backend\.venv\Scripts\python.exe -m backend.app.cli
# API: http://127.0.0.1:8000/docs | Health: GET /api/health
```

### 2) Frontend (متصفح)

```powershell
cd frontend
npm install
npm run dev     # http://127.0.0.1:5173 (proxy /api → :8000)
```

### 3) Desktop (Electron + backend تلقائي)

```powershell
cd frontend
npm run desktop
```

### 4) بناء نسخة التوزيع

```powershell
# 1) جمّد الباكند
powershell -ExecutionPolicy Bypass -File pyinstaller\build-backend.ps1
powershell -ExecutionPolicy Bypass -File pyinstaller\smoke-test.ps1

# 2) ابنِ الواجهة وغلّف المثبت
cd frontend
npm run build
npm run desktop:dist   # release/AgentOS-Setup-<version>.exe
```

> أو دبل-كليك على `Start-AgentOS.bat` لتشغيل أحدث نسخة مبنية.

## ⚙️ الإعداد | Configuration

انسخ `.env.example` إلى `.env` (في الجذر) وعدّل محليًا. أهم القيم:

| Key | Default | Notes |
| --- | ------- | ----- |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | ابقِ على loopback. `cli.py` يدعم `--host/--port/--data-dir` |
| `RUNTIME_MODE` | `real` | `mock` في الاختبارات |
| `OPENCODE_BASE_URL` | `http://127.0.0.1:49100` | خارج حزمة الديسكتوب |
| `DEFAULT_MODEL_ID` | `muse-spark-1.3-contributor-free` | يُعاد التحقق منه حيًا قبل كل تنفيذ |
| `AGENTOS_DATA_DIR` | _(Electron sets it)_ | يوجّه `agentos.db` لمجلد userData |

## ✅ الاختبارات | Testing

```powershell
# Backend (48 test)
backend\.venv\Scripts\pytest.exe backend\tests -v

# Frontend typecheck + lint + build
cd frontend
npx tsc -b
npm run lint
npm run build

# E2E (26 test, Chromium)
npx playwright test
# ملف واحد: npx playwright test e2e/r1_executive_chat.spec.ts
```

التفاصيل: `TEST_INFRA.md` (هرم الـ 5 tiers + مصفوفة R1–R5) و `TEST_READY.md`
(أوامر التشغيل لكل milestone). البوابة: **100% pass** قبل أي release.

## 🔒 الأمان | Security

- يرتبط على `127.0.0.1` فقط. لا تضف `0.0.0.0` أو `allow_origins=["*"]`.
- لا أسرار في الكود: `.env` و `*.db` و `node_modules` و `.venv` و `release/`
  كلها gitignored — راجع `.gitignore` و `.env.example`.
- أخطاء الـ runtime تُنقّح عبر `_redact()` قبل اللوج.
- Electron: `contextIsolation ON`, `nodeIntegration OFF`؛ فقط
  `window.__AGENTOS_API__` + `window.__AGENTOS_DESKTOP__`.
- بلّغ عن الثغرات عبر **private advisory** (لا issue علني) — راجع `SECURITY.md`.

## 🗺️ قيود معروفة | Known limits

- AI runtime غير مضمّن في المثبت — يجب تشغيل OpenCode على نفس الجهاز لمهام `real`.
- لا توقيع كود ولا auto-updater بعد (مخطط: GitHub Releases + electron-updater).
- `uvicorn` يعمل بـ `h11` فقط؛ REST + SSE غير متأثرين.

## 🤝 المساهمة | Contributing

راجع `CONTRIBUTING.md` — بوابات الجودة: `pytest` + `tsc -b` + `oxlint` +
`vite build` + `playwright`. PRs صغيرة مركزة مع دليل اختبار.

## 📄 License

MIT — see [LICENSE](LICENSE).
