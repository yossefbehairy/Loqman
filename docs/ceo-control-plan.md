# CEO Control Plane — خطة التنفيذ (Senior Plan v1)

> الهدف: CEO يرد على الأسئلة من بيانات حقيقية، ثم يتحكم في التطبيق (فرق/عقود) بأفعال مؤكدة.
> القاعدة الذهبية: **قراءة بلا حدود، كتابة بتأكيد بشري دائماً.**

---

## 1) ما الذي اكتشفته في الاركتكتشر الحالي (ولماذا الخطة بهذا الشكل)

| الحقيقة من الكود | الأثر على التصميم |
|---|---|
| الـ runtime عملية واحدة فقط `prompt → text` (`runtime/base.py` + `opencode_runtime.py`) — لا يوجد tool loop | سنبني حلقة ReAct خاصة بنا فوق `execute_task` بدل الاعتماد على أدوات OpenCode الأصلية (التي تتطلب permission flow تفاعلياً وتكسر `MockRuntime`) |
| `MockRuntime` يتفرع على كلمات مفتاحية (`"consultation"`, `"ceo planner"`, `"synthesize the completed"`) — `mock_runtime.py:112-142` | أي برومبت جديد يجب أن يعيد استخدام نفس العلامات أو نحدّث الـ mock معه، وإلا تنكسر الـ 87 اختباراً |
| مهام الـ missions يجب أن ترجع STRICT JSON (الـ executor يعمل `json.loads`, والتلخيص يقتطع) — `ceo.py:187-195` | بروتوكول استدعاء الأدوات سيكون JSON صارماً ليناسب نفس المسار، لا نصاً حراً |
| `KNOWN_ROLES` مجموعة مغلقة (`planner.py:23`) والـ CEO planning يرفض غيرها | أي دور جديد (مثل `security_auditor`) يتطلب: إضافة للدور + seed بروفايل + تغطية mock — ثلاث لمسات دائماً |
| الفرق (`Team`) تعيش في `localStorage` بالفرونتند فقط؛ الباكند يملك `AgentProfile` فقط | "CEO يبني فريقاً" = إنشاء profiles بقسم/مدير في DB؛ ظهورها في جراف الشركة يتطلب توحيد المفهوم (مرحلة 3، فيها شغل فرونتند) |
| عميل Sha7at الخارجي يضرب `/api/missions` مباشرة بلا توكن (ملاحظة Antigravity الأمنية) | أدوات الكتابة **خطيرة** على هذا السطح → بوابة التأكيد ليست رفاهية بل شرط إطلاق |
| `suggested_mission = None` دائماً + تعارض 7 اختبارات challenger | لا نمس هذا العقد في هذه الخطة؛ مسار الأسئلة الجديد منفصل تماماً عن `/api/chat` |

**قرارات معمارية محسومة مقدماً:**
1. **حلقة أدوات ذاتية** (`backend/app/orchestration/agent_loop.py` جديد) فوق `execute_task` — لا MCP خارجي، لا أدوات OpenCode الأصلية. السبب: حتمية الاختبارات + سقف التكلفة (free-only موجود) + لا permission popups.
2. **الكتابة عبر `pending_actions`** — الفعل يُقترح ويُحفظ، ولا يُنفذ إلا بموافقة صريحة. لا تنفيذ تلقائي أبداً.
3. **الأسئلة بلا صفوف mission** — `POST /api/ceo/ask` متزامن، لا يلوث جدول الـ missions ولا الـ Inbox.

---

## 2) المكونات الجديدة (4 ملفات + جدول واحد)

```
backend/app/orchestration/
  agent_loop.py      ← حلقة ReAct: prompt → (tool_call | final) × N≤6 → رد
  ceo_tools.py       ← سجل الأدوات: الاسم، السكيمة، read/write، المنفذ
backend/app/api/routes/
  ceo.py             ← POST /api/ceo/ask · POST /api/ceo/actions · POST /api/ceo/actions/{id}/approve|deny
backend/app/db/models.py
  + PendingAction    ← (id, kind, payload_json, status, created_by, decided_at)
```

### بروتوكول استدعاء الأدوات (JSON envelope — يناسب executor الحالي)
```jsonc
// رد الموديل في كل دورة: إما أداة أو جواب نهائي — لا شيء ثالث
{ "tool_call": { "name": "list_missions", "args": { "status": "succeeded", "limit": 5 } } }
{ "final": "نفذت 3 مهام ناجحة آخر ساعة، منها ..." }
```
- الحلقة: كحد أقصى 6 دورات → بعدها تُرجع أفضل ما جُمع (لا حلقة لا نهائية أبداً).
- كل `tool_call` يُتحقق من سكيمته بـ Pydantic قبل التنفيذ (اسم غير معروف = خطأ يُغذّى راجعاً للموديل ضمن الدورات).
- أدوات `write` لا تُنفذ في الحلقة أبداً — تُسجل كـ `pending` وتُعاد كاقتراح.

### كتالوج الأدوات v1

| الأداة | النوع | ماذا تفعل | المصدر |
|---|---|---|---|
| `list_missions` | read | آخر المهام بفلتر حالة | `MissionRepository.list` |
| `get_mission` | read | مهمة + مهامها + الملخص | `_mission_to_out` موجود |
| `list_profiles` | read | الموظفون وموديلاتهم | `AgentProfileRepository.list_all` |
| `get_profile` | read | عقد موظف واحد | موجود |
| `propose_update_contract` | write✋ | اقتراح تعديل (موديل/variant/instructions) | → `pending_actions` |
| `propose_new_profile` | write✋ | اقتراح موظف/دور جديد (لاحقاً: فريق) | → `pending_actions` |

✋ = يتطلب `POST /api/ceo/actions/{id}/approve` من المستخدم + بث SSE `ceo.action.decided`.

---

## 3) الـ API الجديدة (عقود ثابتة)

```
POST /api/ceo/ask
  body:    { question: str(1..2000), model_id?: free-only, variant? }
  returns: { reply: str, actions_proposed: [{id, kind, summary}], iterations: n }
  errors:  400 سؤال فارغ · 502 runtime/فشل موديل (نفس أسلوب missions)

POST /api/ceo/actions                 → إنشاء اقتراح يدوي (اختياري v1.1)
GET  /api/ceo/actions?status=pending  → قائمة المعلّق
POST /api/ceo/actions/{id}/approve    → ينفذ عبر نفس كود راوت البروفايلات (لا منطق مكرر)
POST /api/ceo/actions/{id}/deny      → رفض مسجل
```

قاعدة إعادة الاستخدام: منفذ `approve` يستدعي `repo.update(...)` مباشرة (نفس سطر `agent_profiles.py:107`) — لا نعيد كتابة validation (`settings_json` وغيره).

---

## 4) البرومبتات (متوافقة مع MockRuntime)

- برومبت الحلقة يحمل علامة `"ceo tools"` الجديدة **وداخلها** يحافظ على سلوك mock الحالي بإضافة فرع واحد في `mock_runtime.py`:
  - `elif "ceo tools" in prompt_lower:` → يرد `{"tool_call": ...}` حتمياً حسب نص السؤال (أسئلة missions → `list_missions`، أسئلة بروفايل → `list_profiles`، وإلا `{"final": ...}`).
- لا نمس الفروع القائمة (`consultation`/`ceo planner`/synthesis) — صفر انحدار.

---

## 5) خطة الاختبارات (قبل أي دمج)

| الاختبار | الملف | يغطي |
|---|---|---|
| مطابقة الأسئلة → الأداة الصحيحة (mock) | `test_ceo_ask.py` | 3 أسئلة عربية/إنجليزية |
| `final` مباشر بلا أدوات | `test_ceo_ask.py` | تحية/سؤال عام |
| اقتراح كتابة → pending ولا يُنفذ | `test_ceo_actions.py` | approve لاحقاً ينفذ، deny يبقيه |
| أداة مجهولة/args فاسدة → خطأ نظيف | `test_ceo_actions.py` | Pydantic validation |
| سقف 6 دورات (mock عنيد) | `test_ceo_ask.py` | ينتهي برد جزئي، لا تعليق |
| انحدار كامل | الموجودة | 87 كما هي (challenger المستبعدة تبقى مستبعدة) |

تحقق حي واحد (real runtime): سؤال عربي عن آخر مهمة ناجحة → رد يتضمن ID حقيقياً من DB.

---

## 6) مراحل الإطلاق ومعايير القبول

- **P1 — اسأل الـ CEO (read-only):** `ask` + 4 أدوات قراءة + اختباراتها. القبول: 3 أسئلة عربية تجيب من DB حقيقية، صفر كتابة ممكنة كودياً (لا يوجد مسار كتابة أصلاً).
- **P2 — اقتراح + تأكيد:** جدول `pending_actions` + approve/deny + SSE + اختباراتها. القبول: اقتراح "سرّع سارة" ينفذ فقط بعد approve، وdeny لا يغيّر شيئاً.
- **P3 — فرق بأمره:** دور جديد + إنشاء profiles جماعي + توحيد Team مع الفرونتند (يتطلب تغيير `App.tsx`/`agentosDefaults.ts`). القبول: "اعمل فريق مراجعة أمنية" يظهر في شاشة الشركة.

**خارج النطاق صراحة:** تنفيذ كتابة تلقائي، أدوات ملفات/شبكة، صلاحيات OpenCode الأصلية، تغيير عقد `suggested_mission` المتنازع عليه.

---

## 7) المخاطر والتخفيف

| الخطر | التخفيف |
|---|---|
| عميل خارجي يستدعي الكتابة | بوابة التأكيد + surfaces القراءة فقط افتراضياً؛ توكن محلي (توصية Antigravity) شرط قبل أي تعريض أوسع |
| موديل مجاني يهلوس استدعاءات | سكيمة Pydantic صارمة + حد 6 دورات + كل نتيجة أداة حقيقية من DB (لا يخترع بيانات) |
| نمو سياق الحلقة يبطئ الرد | excerpts مقطوعة (1500 حرف كالتلخيص الحالي) + حد الدورات |
| كسر الـ 87 اختباراً | mock يُمدد لا يُعدل؛ عقود `/api/chat` و`/api/missions` untouched |

---

## 8) حالة التنفيذ (محدَّثة)

| المرحلة | الحالة | الدليل |
|---|---|---|
| **P1 — اسأل الـ CEO (read-only)** | ✅ مكتمل | `orchestration/agent_loop.py` + `orchestration/ceo_tools.py` (4 أدوات قراءة) + `POST /api/ceo/ask` — `tests/test_ceo_ask.py` |
| **P2 — اقتراح + تأكيد** | ✅ مكتمل | جدول `pending_actions` + approve/deny بهيدر `X-AgentOS-Confirm` (403 بدونه، 409 للتكرار، optimistic concurrency للعقود) + SSE `ceo.action.decided` — `tests/test_ceo_actions.py` |
| **P3 — فرق بأمره** | ✅ مكتمل | دور `security_auditor` + أداة `propose_new_team` (1–8 أعضاء، سكيمة Pydantic، تحقق الباتش كامل قبل أول كتابة، صفر zombie rows) + approve ذري يرجع `created_profiles`/`team_name` + **واجهة CEO كاملة** (أول عنصر في الـ nav: Ask + صندوق موافقات بتأكيد داخل التطبيق) + **مادية الفريق في شاشة الشركة** (الاعتماد → Team + Agents مرتبطين بـ profileId) — `tests/test_ceo_team.py` + E2E `e2e/ceo_control_plane.spec.ts` |

**ملاحظات:**
- عقد `suggested_mission = None` لم يتغير. الاختبارات الـ 7 challenger القديمة التي كانت تؤكد العقد السابق تم تحديثها لتؤكد العقد الحالي (`is None`) بدل بقائها حمراء/مستبعدة — نفس القرار، اختبارات متسقة معه.
- الإجمالي الآن: **143 اختبار backend** + **47 اختبار E2E** (9 ملفات، `RUNTIME_MODE=mock`) — كلها خضراء محليًا، وبوابة CI تشمل E2E.
- "توحيد Team مع الفرونتند" نُفِّذ بدون نقل الفرق من `localStorage` إلى DB: الاعتماد يبني `Team` + `Agent` في حالة الفرونت (مربوطين بالـ `profileId` الحقيقي) فيظهرون فورًا في Company. النقل الكامل لـ DB يبقى خيارًا مستقبليًا لو احتجناه.
