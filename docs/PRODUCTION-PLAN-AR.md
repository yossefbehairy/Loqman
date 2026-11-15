# خطة الوصول للإنتاج — بدأت 14 نوفمبر 2026

مرجع العمل المستمر. أي جلسة تكمل تبدأ من **سجل التقدم** في آخر الملف.
السياق السابق: [AUDIT-2026-11-14-AR.md](AUDIT-2026-11-14-AR.md).

## موافقات صاحب المشروع (14 نوفمبر)

- الخطة كلها موافَق عليها. العمل على branch `providers-v2`، commit بعد كل مرحلة متحقق منها، **بدون push**.
- Loqman والـ agents التي ينشئها والمهام تمشي على المزوّد والنموذج الافتراضي المختار في Settings.
- توصيل النسخ الاحتياطي، إلغاء المهمة، وتقدّم Loqman بالواجهة.
- المنفّذون (تحديث 15 نوفمبر بطلب المستخدم): Codex منفّذ أساسي لشرائح كاملة (`-s workspace-write`) طالما ملفاتها لا تتداخل مع agent شغال؛ sub-agents بموديل Sonnet 5.5 بـ `effort: medium`. Codex لا يستطيع تشغيل الـ venv داخل sandbox فالجلسة الرئيسية تشغّل pytest وتتأكد أن الاختبارات الجديدة تفشل على الكود القديم (git stash) قبل الـ commit؛ Codex (`gpt-6.1-sol`، `gpt-6-astra`، `gpt-6.1-terra`) للمراجعة بوضع `-s read-only` أو لمهام لا تتعارض ملفاتها؛ الجلسة الرئيسية تنسّق وتتحقق.
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

## تفصيل P4–P8 حسب تصميم العقد

التصميم المعتمد: [DRIVER-CONTRACT-DESIGN.md](DRIVER-CONTRACT-DESIGN.md) (راجعته الجلسة الرئيسية في 15 نوفمبر؛ يُتّبع كما هو ما لم تُكتب هنا مخالفة).

| المرحلة | خطوات التصميم | ملاحظات ملزمة |
|---|---|---|
| P4 | الخطوات 1–3: `TurnDriver` و`ExecuteTaskDriver` يغلّف المحركات الحالية؛ schema v18 (`runtime_sessions`، أعمدة `conversation_runs` و`conversation_run_events` و`approvals`) عبر `migrate.py` مع اختبار ترقية من v17 ونسخة احتياطية؛ `collect_turn` | لا تغيير سلوكي ظاهر؛ البث يبقى status-only افتراضيًا؛ تسجيل الاستخدام في نقطة واحدة؛ Job Object على Windows ضمن المشرف المشترك |
| P5 | الخطوة 4: Claude `stream-json` و`--resume` خلف feature flag | نفس سياسة الأدوات الحالية أولًا؛ لا رجوع للمسار القديم بعد إرسال غير مؤكد |
| P6 | الخطوة 5: `codex app-server` | عملية لكل turn أولًا؛ لا توسيع للصلاحيات (read-only) |
| P7 | الخطوتان 6–7: نواة ACP؛ Antigravity ثم OpenCode بجانب القديم | لكل dialect أسلوب استكمال خاص؛ حذف adapter الـ OpenCode القديم فقط بعد إثبات تكافؤ القيود |
| P8 | الخطوة 8: الواجهة | تعرّف صريح على الحالات النهائية قبل تفعيل الأحداث الجديدة |

Loqman والمهام يبقون على `collect_turn` بنص كامل وسياق معزول لكل استدعاء حتى تصبح بانيات الـ prompt تزايدية (خطر تكرار التاريخ).

## سجل التقدم

- [x] P1 — التوجيه (pytest 378، Playwright 68/68، تجربة حقيقية ناجحة)
- [x] P2 — تنظيف وتحييد (pytest 326، Playwright 68/68، node 36، oxlint 9)
- [x] P3 — توصيل وإصلاحات صغيرة (commits `b1331d8`، `f459b91`)
- [x] P4 — عقد turns وأحداث (commit `2758cd9`؛ pytest 373، Playwright 78/78)
- [x] S — إصلاحات أمنية من مراجعتي Grok (commits `13bdc04`...`13a5b4d` worker، `0491896` Electron؛ S4 في P7، E4 يحتاج شهادة المالك)
- [x] P5 — Claude driver (commit `e628767`؛ pytest 423، تجربة حقيقية ناجحة؛ مطفأ افتراضيًا حتى P8)
- [ ] R — إصلاح ما وجدته مراجعتا Grok لـ P4 و P5 والأمن (R-A و R-B الآن؛ R-C بعد P6)
- [x] P6 — Codex driver (commit `7b80c37`؛ تجربة حقيقية ناجحة؛ مطفأ افتراضيًا `AGENTOS_CODEX_DRIVER=appserver`)
- [ ] P7 — نواة ACP
- [x] P8 — الواجهة: بث و Stop (commit `9108d99`؛ Playwright 87/87)
- [ ] P9 — جاهزية الإنتاج

### ملاحظات التنفيذ

(نتيجة كل مرحلة: ما تم، ما فشل، ما بقي، ورقم الـ commit.)

**P1 (Sonnet 5.5، تمت).** `core/preferences.py` (ملف `preferences.json` بجوار القاعدة)، `orchestration/routing.py`، `api/preferences.py`، و27 اختبارًا في `test_routing.py`. تجربة حقيقية ببيانات معزولة: Loqman ردّ من Claude Haiku ومن Codex (`gpt-6-luna`) بـ `engine` صريح؛ بعد ضبط الافتراضي على Claude أنشأ Loqman agent خُتم بـ `claude-code`/`haiku`؛ تفويض مهمة لذلك الـ agent نجح على Claude؛ الاستخدام سُجّل لكل محرك.
ملاحظات للمراحل التالية: المسار السريع (smalltalk) ردّ بالعربية على سؤال إنجليزي (P3). لا ترحيل تلقائي لاختيار المتصفح القديم إلى تفضيل الـ worker: حتى يختار المستخدم افتراضيًا في Settings تبقى المهام على OpenCode. أربع نسخ من جدول محرك←مزوّد ومصانع المحركات ما زالت موجودة (P2).

**تمهيد P9 (Codex gpt-6.1-terra، كُتب دون تشغيل بناء).** `pyinstaller/smoke_test.py` و`smoke-test.ps1` (بوابة فعلية للـ exe)، مهمة Windows في CI، `docs/RELEASE.md`. غير متحقق منه حتى يُشغَّل بناء حقيقي في P9.

**تصميم P4 (Codex gpt-6.1-sol).** `docs/DRIVER-CONTRACT-DESIGN.md`: يُراجع قبل بدء P4.

**15 نوفمبر.** الجلسة أُعيد تشغيلها؛ ضاع scratchpad (ومعه مراجعة Codex لـ P1، أُعيد تشغيلها). عمل Codex على Electron انقطع لكنه كان مكتملًا: راجعته الجلسة الرئيسية وصار commit `13bdc04` (مشغّل محصّن من `ELECTRON_RUN_AS_NODE`، فتح الروابط في المتصفح، `agentos:backup-save` عبر preload باسم `saveBackup()`، تحصين worker-client؛ 36 اختبار node). P2 توقف بحد الاستخدام قبل أي تعديل واستُكمل بـ SendMessage.

**مراجعة Codex لـ P1 (gpt-6.1-sol).** تُعالج في P3 بهذا الترتيب:
- **R-P1-1 (P1):** في وضع mock، `ceo.ask` مع `engine` حقيقي (مثل `claude-code`) يشغّل المحرك الحقيقي عبر `ctx.engine_factory`؛ الحماية في الاتجاه العكسي فقط (`api/ceo.py:135-144`). خطر على الاختبارات. الإصلاح: رفض أي محرك غير mock في وضع mock.
- **R-P1-3 (P2):** تغيير الافتراضي أثناء مهمة جارية يخلط المحرك والسياسة وتسمية السجل بين المراحل (`orchestration/ceo.py:278-292`). الإصلاح: تثبيت اختيار المحرك/النموذج/المزوّد عند إنشاء المهمة وتمريره لكل المراحل.
- **R-P1-4 (P2):** استرجاع تفضيل الـ worker عند البدء قد يكتب فوق اختيار أحدث للمستخدم (`useProviders.ts:271-274, 300-334`). الإصلاح: التقاط `choiceSeq` قبل بدء الطلبات الأولى.
- R-P1-2 و R-P1-5 (workflows و `chat.consult` يفترضان OpenCode): يزولان بحذف الكود في P2؛ يُتحقق من ذلك.
- ملاحظة: محركات الحسابات لا تحفظ ذاكرة Loqman بين الأسئلة إلا آخر 8 رسائل مقطوعة عند 500 حرف؛ الحل الحقيقي جلسات أصلية (P4–P5).

**P2 (Sonnet 5.5، تمت).** حُذف ~3,500 سطر: `api/chat.py`، محرك الـ workflows، `agy_runtime.py`، أوضاع `RUNTIME_MODE` غير real/mock (رسالة خروج واضحة)، `models.refresh` و`runtime.health` و`ceo.history` و`app.info`، وأنواع الواجهة القديمة. 59 اختبارًا حُذفت مع كودها و9 أضيفت. جدول المحركات الوحيد في `providers.py` والمصنع الوحيد `create_engine_runtime`. بقي: `health` (يستعمله Electron والـ smoke)، `models.free` (محرك mock)، و`approvals.capabilities` يذكر `agy` بدل `antigravity` (P3). R-P1-2 و R-P1-5 زالا بحذف الكود.

**P3 — نصف الـ worker (Codex gpt-6.1-terra، commit `b1331d8`).** الجلسة الرئيسية شغّلت الاختبارات: 332 ناجح؛ الستة الجديدة في `test_worker_regressions.py` تفشل على الكود القديم وتنجح بعد الإصلاح. اختبار قديم كان يثبّت «أول نموذج مجاني» عُدّل ليثبّت «نموذج مجاني». نصف الواجهة (Sonnet) جارٍ. P4 بدأ بالتوازي على الـ worker (Codex gpt-6.1-sol).

**P3 (تمت).** الواجهة: زر النسخ الاحتياطي، إلغاء المهمة، تقدّم Loqman، إخفاء «CEO history» الفارغة، لا وميض عند الفتح، وإصلاح سباق الاسترجاع. 10 اختبارات e2e.
**P4 (Codex gpt-6.1-sol + إصلاح Sonnet).** Codex كتب الطبقة دون تشغيل اختبارات؛ الجلسة الرئيسية شغّلتها فظهر تعليق عند الإغلاق مع محرك يتجاهل الإلغاء (الغلاف ينتظر بلا حد)؛ Sonnet جعل الانتظار محدودًا (`cancel_grace_s`) مع اختبار يفشل قبل الإصلاح. ترقية v18 جُرّبت على **نسخة** من قاعدة المستخدم الحقيقية (v17، 4 profiles، 5 رسائل، 4 usage): البيانات مطابقة، نسخة احتياطية `pre-v18` أُنشئت، التشغيل الثاني لا يغيّر شيئًا؛ النسخة المؤقتة حُذفت.

## S — مراجعة Grok الأمنية (حساب المستخدم، 15 نوفمبر)

**الـ worker:**
- S1 (P1): استرجاع نسخة احتياطية متلاعب بها (triggers، مسارات workspace مثل `C:\`، صفوف `publishing`) يغيّر جذر العمل ويوافق تلقائيًا على الكتابة (`core/backup.py:117`، `workspace_writes.py:97`، `workspace_fs.py:102`).
- S2 (P1): Codex يرث بيئة الـ worker ويملك shell مع نصوص ملفات غير موثوقة في الـ prompt (`codex_runtime.py:182, 326`).
- S3 (P2): Antigravity بنفس المشكلة (`agy_acp_runtime.py:272, 483`).
- S4 (P2): OpenCode يضع الـ prompt كله في سطر الأوامر (`opencode_runtime.py:179-185`) — يُحل في P7 مع ACP.
- S5 (P2): Claude يشغّل hooks المستخدم ويرث البيئة (`claude_runtime.py:151, 257`) — يُحل في P5.
- S6 (P2): نشر التقرير: فحص المسار ثم الإنشاء بالمسار (TOCTOU/junction) و`os.link` يتبع الروابط (`workspace_writes.py:62`).
- S7 (P2): Chromium الآلي بمنفذ debugging بلا token، و`browser_open` يقبل loopback والشبكات الخاصة (`browser.py:116, 175`).
- S8 (P3): hardlinks داخل الـ workspace تُقرأ كملفات عادية (`workspace_fs.py:109`، `skills_fs.py:164`).
- S9 (P3): ملفات مؤقتة للتقرير وللنسخة الاحتياطية تبقى بعد crash؛ مجلد النسخة بلا ACL خاص على Windows؛ نقرة `computer_click` عامة بعد فحص منفصل (`computer.py:157`).

**Electron:**
- E1 (P2): `window.agentos.call` يمرر أي method تقريبًا (workspaces.create، artifacts.content، approvals.decide، engines.open_setup).
- E2 (P2): ردود النماذج تحمّل صورًا خارجية تلقائيًا (تسريب عبر prompt injection) (`ChatMarkdown.tsx:360`).
- E3 (P2): لا CSP في التطبيق المثبّت، والخطوط من Google عند كل تشغيل (`index.html:7`).
- E4 (P2): لا توقيع للمثبّت ولا updater (`package.json` build) — يحتاج شهادة من صاحب المشروع.
- E5 (P3): DevTools في القائمة حتى في النسخة المثبّتة (`main.cjs:368`).
- E6 (P3): المثبّت يضم `electron/*.test.cjs`.
- E7 (P3): لا `setPermissionRequestHandler`.

**S (تمت).** worker: Codex كتب الإصلاحات دون تشغيل اختبارات (40 فشلًا، منها كسر خيار مفتاح API الاختياري)؛ Sonnet أصلحها وأكملها: 22 من 27 اختبارًا جديدًا تفشل على الكود القديم. حدود باقية: إعادة توجيه DNS في المتصفح الآلي، نافذة ضيقة في نشر التقرير تُكتشف وتُلغى ولا تُمنع، ACL مجلد النسخة الاحتياطية بلا اختبار. Electron: قائمة 43 method، CSP، خطوط محلية (Inter و Poppins و Plus Jakarta Sans، صحّحت الجلسة الرئيسية اختبار Codex الذي كان يبحث عن خطوط غير مستعملة)، لا صور خارجية، DevTools للتطوير فقط، رفض الصلاحيات. Playwright 80/80.

**P5 (Sonnet، تمت).** `ClaudeStreamDriver` خلف `AGENTOS_CLAUDE_DRIVER=stream`. تجربة حقيقية ببيانات معزولة: الرسالة الثانية استكملت الجلسة الأصلية (`resumed: true`) وتذكرت كلمة من الأولى، ردّ طويل بُثّ في 182 حدث `content.delta`، الإلغاء أوقفه. عمليات `claude.exe` التي ظهرت بعد التجربة كانت تابعة لإضافة VS Code لا للتطبيق. يُفعَّل افتراضيًا في P8 بعد أن تعرض الواجهة البث.

## R — مراجعتا Grok لـ P4 و P5+الأمن (16 نوفمبر)

**R-A (ملفات لا يلمسها P6/P8 — Codex الآن):**
- P4-3: `migrate.py:137` إنشاء `runtime_sessions` يُثبَّت فورًا؛ فشل قبل `user_version=18` يترك الجدول بلا `uq_runtime_sessions_native` إلى الأبد.
- P4-4: `supervisor.py:130` الـ Job Object يُسند بعد الإنشاء؛ أحفاد تهرب؛ `taskkill /T` يُتجاوز بعد موت الجذر.
- P4-6/7/8: `supervisor.py` قراءة POSIX بلا حد عند الإغلاق؛ إغلاق stdin من طرفين؛ فشل `CloseHandle` لا يُعاد.
- S-3: `backup.py` جداول وindexes النسخ الأقدم تُقبل بالاسم فقط؛ `restore_database` لا يعيد فحص المسارات؛ `trusted_schema=OFF` غير مطبق على القاعدة بعد الاسترجاع.
- S-4: `child_env.py` يسقط proxy و CA و ADC؛ مطابقة `LC_*` و `PROGRAMFILES*` كبادئة تمرر أسرارًا محتملة.
- S-5: `workspace_writes.py` الـ junction بعد الفحص ما زالت تسمح بالكتابة خارج الـ workspace؛ temp في المجلد الأب يفشل لو غير قابل للكتابة.
- S-9: `computer.py:167` نقرة في إطار النافذة تُرسل بإحداثيات ملفوفة.
- S-1 (P1): `browser.py` فحص العناوين الخاصة مرة واحدة فقط؛ redirects والنقرات والتنقل داخل الصفحة غير مفحوصة.

**R-B (طبقة التشغيل — Codex الآن):**
- P4-1: `conversation_runs.py:157` نجاح محفوظ مؤقتًا يُكتب كحدث نهائي لتشغيل أُلغي أو فشل، و`usage` يُسجّل `success=1` لتشغيل بلا رد.
- P4-2: `collector.py:129` إلغاء أثناء التنظيف بعد نجاح يرمي الرد الجاهز ويسجّل فشلًا.
- P4-5: `execute_driver.py:159` مهلة الثانية تفصل أمر قتل المحرك فيعيش بعد الإغلاق.
- P4-9: `conversation_runs.py:784` مهام المهام لا تمرر حد المخرجات للـ driver (حتى 8 MiB في الأحداث).

**R-C (Claude driver — بعد انتهاء P6 لأنه قد يلمس `claude_driver.py`):**
- S-2 (P1): رفض استكمال جلسة **بعد** كتابة الرسالة يؤدي لإعادة إرسالها في جلسة جديدة (`claude_driver.py:557, 635, 452`).
- S-6: قائمة انتظار stdout بلا حد بعد سقف البث (`claude_driver.py:146`).
- S-7: كتابة الرسالة وردود الصلاحيات على stdin بلا قفل (`claude_driver.py:559, 738`).
- S-8: الـ stream driver يتجاهل خيار مفتاح Anthropic الاختياري.
- S-10: الرفض لـ `can_use_tool` فقط؛ أي `control_request` آخر بلا رد؛ لا رفض للتشغيل إن لم تتوفر `--permission-prompts`.

**16 نوفمبر — P6 و P8 و R-B.**
- **P6 (Sonnet بعد أن رفض Codex بدء العمل لأن sandbox لا يرى أمر `codex`):** تأكدت الأوامر من `codex app-server generate-json-schema` للإصدار 0.167.0. تجربة حقيقية: الرد الثاني استكمل الـ thread الأصلي وتذكّر كلمة من الأول، ردّ طويل نزل في 187 حدثًا، الإلغاء أوقفه. الاتفاق: يبقى مطفأ افتراضيًا حتى يجرّبه المالك. ملاحظات من الـ agent: الـ prompt الأطول من 24,000 حرف يُرفض بدل القص الصامت؛ `config.toml` للمستخدم ما زال يُقرأ لأن app-server بلا `--ignore-user-config`.
- **P8:** الواجهة تفتح بث `normalised-v1` وتعرض النص مع الإنتاج؛ الحالات النهائية صريحة. الـ agent لم يستطع تعديل الـ bridge (خارج نطاقه)، فعدّلته الجلسة الرئيسية: تمرير صيغة الحدث في `vite-agentos-bridge.mjs` و`preload.cjs` و`main.cjs` (قيمة معروفة واحدة فقط) مع اختبارين. تشغيل Claude stream driver صار الافتراضي (`AGENTOS_CLAUDE_DRIVER=wrapper` لإيقافه)؛ 6 اختبارات تحجز محركًا باسم `claude-code` فأُضيف fixture في `conftest.py` يثبّت `wrapper`.
- **ملاحظة مهمة عن البث الحي:** جرّبت Claude الحقيقي مباشرة في وضع stream-json: يرسل الرد القصير في إطار `text_delta` **واحد** (بعد ~7 ثوانٍ) لا على دفعات، فالمستخدم يرى الرد دفعة واحدة وإن كان المسار كله سليمًا (78 حدثًا وصلت للواجهة في نافذة 1.6 ثانية). Codex يرسل على دفعات فعلًا. لا إصلاح مطلوب من جهتنا.
- **R-B:** 12 من 13 اختبارًا يفشل على الكود القديم؛ commit `dec632b`.
