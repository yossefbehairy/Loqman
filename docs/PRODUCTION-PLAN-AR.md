# خطة الوصول للإنتاج — بدأت 14 نوفمبر 2026

مرجع العمل المستمر. أي جلسة تكمل تبدأ من **سجل التقدم** في آخر الملف.
السياق السابق: [AUDIT-2026-11-14-AR.md](AUDIT-2026-11-14-AR.md).

## موافقات صاحب المشروع (14 نوفمبر)

- الخطة كلها موافَق عليها. العمل على branch `providers-v2`، commit بعد كل مرحلة متحقق منها، **بدون push**.
- Loqman والـ agents التي ينشئها والمهام تمشي على المزوّد والنموذج الافتراضي المختار في Settings.
- توصيل النسخ الاحتياطي، إلغاء المهمة، وتقدّم Loqman بالواجهة.
- المنفّذون: sub-agents بموديل Sonnet 5.5؛ Codex (`gpt-6.1-sol`، `gpt-6-astra`، `gpt-6.1-terra`) للمراجعة بوضع `-s read-only` أو لمهام لا تتعارض ملفاتها؛ الجلسة الرئيسية تنسّق وتتحقق.
- عند انتهاء حد الاستخدام: الروتين يكمل من سجل التقدم بعد رجوعه.
- نسخ RuseBot (`D:\rusebot` والمستودعان على GitHub) لا تُمس ولا تُحذف. نأخذ منها **السلوك** ونكتبه Python؛ لا ننقل أي شيء cloud.

## المشكلة التي نحلها

الـ worker مبني حول «محرك افتراضي» واحد (OpenCode) وعقد `execute_task` بشكل OpenCode SDK (ابعت prompt وانتظر النص كله؛ `agent_id`؛ `session_title`؛ `free_only`). محادثة الـ agent فقط تستعمل المزوّد المختار؛ Loqman والمهام والتخطيط ترجع دائمًا إلى OpenCode المجاني.

## المراحل

| # | المرحلة | الناتج | التحقق |
|---|---|---|---|
| P1 | **التوجيه**: مصدر واحد يحدد المحرك والنموذج لـ Loqman، للـ agents التي ينشئها، للمهام وللتخطيط؛ الواجهة ترسل الاختيار صريحًا؛ قيد «مجاني فقط» يبقى داخل OpenCode وحده | Loqman يرد من Claude/Codex عند اختيارهما | pytest + e2e + تجربة حقيقية |
| P2 | **تنظيف وتحييد**: حذف `api/chat.py`، محرك workflows القديم (`workflows.*`، `executions.*`، `engine/executor.py`، `validator.py`، `state_machine.py`، `context.py`، `core/executions.py`) واختباراته، `runtime/agy_runtime.py`، أوضاع `RUNTIME_MODE` غير `real`/`mock`، `models.refresh`، `runtime.health`، `ceo.history`؛ مصنع محركات واحد وجدول مزوّدين واحد من `providers.py`؛ رسائل أخطاء باسم المحرك الفعلي؛ أنواع الواجهة القديمة و`public/icons.svg` | لا مراجع ميتة | pytest + e2e + build |
| P3 | **توصيل وإصلاحات صغيرة**: زر نسخ احتياطي في Settings؛ Cancel للمهمة؛ عرض `ceo.ask_progress`؛ اقتراحات Loqman التلقائية لا تظهر في «Needs you»؛ لا محادثة «CEO history» فارغة لمستخدم جديد؛ لا وميض Loqman عند الفتح؛ اختبار Codex لا يختار أغلى نموذج | مزايا تعمل من الواجهة | pytest + e2e |
| P4 | **عقد turns وأحداث** (على شكل `ProviderAdapter` و`runtime-events` في RuseBot): `start_turn` / `interrupt` / `respond` / أحداث `turn.started`، `content.delta`، `item.*`، `request.opened/resolved`، `turn.completed`، `runtime.error`، مع جلسة أصلية لكل محادثة؛ driver وهمي للاختبارات؛ مسار الأحداث من الـ worker إلى الواجهة. المحركات الحالية تُغلَّف مؤقتًا بمحوّل حتى تُستبدل | العقد واختباراته | pytest |
| P5 | **Claude driver**: `stream-json` بالاتجاهين، `--resume`، أدوات بموافقة | بث وإيقاف وجلسة على Claude | pytest بـ CLI وهمي + تجربة حقيقية |
| P6 | **Codex driver**: `codex app-server` (JSON-RPC: `thread/start`، `turn/start`، `turn/interrupt`) | نفس القدرات على Codex | كذلك |
| P7 | **نواة ACP** واحدة لـ Antigravity و OpenCode (`session/new`، `prompt`، `update`، `cancel`، `load`، `request_permission`)، ثم حذف adapter الـ OpenCode القديم (سيرفر HTTP و agents مقفولة) | OpenCode محرك عادي | كذلك |
| P8 | **الواجهة**: نص متدفق في المحادثة، Stop حقيقي، حالة الأدوات | تجربة محادثة حيّة | e2e + تجربة حقيقية |
| P9 | **جاهزية الإنتاج**: بناء `agentos-backend.exe` جديد والمثبّت؛ smoke على التطبيق المُغلَّف ببيانات نظيفة؛ تجربة «Open terminal» فعليًا؛ ترقية قاعدة بيانات حقيقية منسوخة؛ مهمة Windows في CI للبناء المجمّد؛ مراجعة Codex نهائية؛ تحديث الوثائق والإصدار | مثبّت مجرَّب | قائمة فحص P9 |

## قواعد لكل المنفّذين

- لا push. لا حذف ولا تعديل لأي `*.db` أو `*.bak` خارج مجلدات الاختبار. لا مساس بـ `D:\rusebot`.
- لا استدعاء لنماذج حقيقية داخل الاختبارات الآلية. التجربة الحقيقية تقوم بها الجلسة الرئيسية ببيانات معزولة.
- كل إصلاح أو سلوك جديد باختبار؛ للعيوب: اختبار يفشل أولًا.
- ترقية schema مسموحة من P4 فصاعدًا فقط، عبر `db/migrate.py` مع اختبار ترقية ونسخة احتياطية تلقائية، ودون كسر قاعدة بيانات موجودة.
- لا تحذيرات lint جديدة (`npx oxlint`: 9 حاليًا). لا تبعيات npm جديدة بدون سبب مكتوب.
- بعد تعديل الـ worker وقبل Playwright: لا حاجة لإيقاف خادم؛ Playwright يشغّل خادمه ببيانات جديدة (المنفذ 5173 يجب أن يكون حرًا).
- في محددات Playwright: `exact: true` للأسماء القصيرة.

## فحوص التحقق (الجلسة الرئيسية بعد كل مرحلة)

```
backend/.venv/Scripts/python.exe -m pytest backend/tests -q -p no:cacheprovider
node --test frontend/electron/bridge.test.cjs frontend/electron/worker-client.test.cjs frontend/electron/transport.test.cjs
cd frontend && npx tsc -b && npx oxlint && npm run build && npx playwright test
```

خط الأساس عند البدء (commit `032a42b`): pytest 351 ناجح و1 متخطى؛ node 11؛ Playwright 65/65؛ oxlint 9 تحذيرات.

## سجل التقدم

- [x] P1 — التوجيه (pytest 378، Playwright 68/68، تجربة حقيقية ناجحة)
- [ ] P2 — تنظيف وتحييد
- [ ] P3 — توصيل وإصلاحات صغيرة
- [ ] P4 — عقد turns وأحداث
- [ ] P5 — Claude driver
- [ ] P6 — Codex driver
- [ ] P7 — نواة ACP
- [ ] P8 — الواجهة: بث و Stop
- [ ] P9 — جاهزية الإنتاج

### ملاحظات التنفيذ

(نتيجة كل مرحلة: ما تم، ما فشل، ما بقي، ورقم الـ commit.)

**P1 (Sonnet 5.5، تمت).** `core/preferences.py` (ملف `preferences.json` بجوار القاعدة)، `orchestration/routing.py`، `api/preferences.py`، و27 اختبارًا في `test_routing.py`. تجربة حقيقية ببيانات معزولة: Loqman ردّ من Claude Haiku ومن Codex (`gpt-6-luna`) بـ `engine` صريح؛ بعد ضبط الافتراضي على Claude أنشأ Loqman agent خُتم بـ `claude-code`/`haiku`؛ تفويض مهمة لذلك الـ agent نجح على Claude؛ الاستخدام سُجّل لكل محرك.
ملاحظات للمراحل التالية: المسار السريع (smalltalk) ردّ بالعربية على سؤال إنجليزي (P3). لا ترحيل تلقائي لاختيار المتصفح القديم إلى تفضيل الـ worker: حتى يختار المستخدم افتراضيًا في Settings تبقى المهام على OpenCode. أربع نسخ من جدول محرك←مزوّد ومصانع المحركات ما زالت موجودة (P2).

**تمهيد P9 (Codex gpt-6.1-terra، كُتب دون تشغيل بناء).** `pyinstaller/smoke_test.py` و`smoke-test.ps1` (بوابة فعلية للـ exe)، مهمة Windows في CI، `docs/RELEASE.md`. غير متحقق منه حتى يُشغَّل بناء حقيقي في P9.

**تصميم P4 (Codex gpt-6.1-sol).** `docs/DRIVER-CONTRACT-DESIGN.md`: يُراجع قبل بدء P4.
