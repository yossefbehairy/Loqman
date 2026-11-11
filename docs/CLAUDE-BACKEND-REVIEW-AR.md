# مراجعة مقترح مراحل الباك إند وإعادة استخدام RuseBot

> مراجعة مستقلة من Claude Code بتاريخ 11 نوفمبر 2026 عبر مسار `claude-delegate` للقراءة فقط. النص أدناه رأي المراجع، وليس إثباتًا لتشغيل المقترحات. ملاحظات Codex بعد مراجعة النتيجة:
>
> - `Artifact` لديه بالفعل `run_id` اختياري في `backend/app/db/models.py`؛ عبارة التقرير بأنه غير مرتبط بـrun غير دقيقة. الذي لا يوجد هو هوية استدعاء أداة تضمن عدم تكرار الأثر على القرص.
> - `waiting_approval` اختيار تصميم وليس شرطًا لازمًا؛ يمكن إبقاء run في `running` مع حالة أداة/موافقة منفصلة. المشكلة المؤكدة هي غياب دورة انتظار وتنفيذ مكتملة، والحاجة إلى تحديد علاقة مهلة المحرك بوقت انتظار المستخدم.
> - كتابة المخرجات ميزة جديدة تحتاج عقدًا واختبارات؛ يمكن إضافتها بخدمة منفصلة مع الحفاظ على سلوك تنزيل deliverables الحالي. ليست عيبًا قائمًا في الخدمة الحالية.
> - ادعاءات Playwright وخيارات CLI التي علّمها المراجع بأنها غير متحققة تحتاج فحصًا عند التنفيذ. لا نعتمد وصف closed-world قبل اختبار hooks والإعدادات وأدوات المحرك الفعلية.
> - انتهت المراجعة بنجاح، وسجل الأدوات يحتوي Read/Glob/Grep فقط. أطلق relay تنبيه تغير ملفات أثناء القراءة؛ فحص أوقات الكتابة في مجلدات الكود والاختبارات والوثائق وgraphify وجد `graphify-out/cache/stat-index.json` متغيرًا خلال المراجعة، ولم يجد تعديلًا في الكود ضمن هذه المجلدات. لا يمكن نسبة تغير cache للوكيل من هذا الدليل وحده.

---

مراجعة قراءة فقط. ما عدّلت ولا شغّلت أي حاجة، ولا كتبت plan file لأنك طلبت كده. أرقام الاختبارات (560 passed) من الـorchestrator، أنا ما شغلتش اختبارات.

## الحكم التنفيذي

الاتجاه سليم: Python يملك السياسة والحالة، وبنستعير الأنماط من RuseBot ومنسخش السيرفر، وبنبدأ بـMCP محدود قبل المتصفح وCUA. لكن الخطة بتقلّل من تقدير ثلاث حاجات:

1. **مسار الاتصال العكسي** من MCP process للـbackend (policy، approvals، run id، token) غير محدد في المقترح.
2. **نموذج الـrun والـapprovals الحالي** مش جاهز لحالة "منتظر موافقة".
3. **Phase 2 كبيرة جدًا** لو اتفهمت على إنها ترحيل المسارات القديمة.

أنصح تدمج 2+3+جزء من 4 في slice رأسية واحدة بمحرك واحد، وتأجّل ترحيل legacy.

---

## أولًا: الصح في المقترح (حقائق مؤكدة من الكود)

- **أدوات native محتاجة إنفاذ منفصل عن MCP gate.** ده مؤكد:
  - Codex مفيهوش tool-less mode (`codex_runtime.py:19-21`).
  - Claude بيشتغل بـ`--tools ""` و`--strict-mcp-config` (`claude_runtime.py:256-257`).
  - Antigravity بيرد على كل `request_permission` بـ`cancelled` وبيمرر `mcpServers: []` (`agy_acp_runtime.py:272-275`, `:395`).
- **بنية الـapprovals موجودة بس مش متوصلة.** `core/approvals.py:4-8,34-38` بيعلن صراحة إن مفيش adapter بيطلبها. الـ`decide` ذري (`:163-199`).
- **نمط الربط بالـhash موجود.** `deliverables.py:224-245` بيربط القرار بـsha256 المحتوى، وده نفس اللي محتاجينه للأدوات.
- **ترتيب policy → browser → CUA صحيح.**

---

## ثانيًا: المشاكل مرتبة بالتأثير

### 1. (عالي) مسار الـcallback للـMCP مش متحدد

الـMCP stdio server بيتشغّل كـprocess بيطلقه الـCLI نفسه. عشان يعرف policy/approval/run لازم يكلّم الـbackend. RuseBot بيحل ده بـproxy رفيع:

- بياخد `OMB_HARNESS_URL` + token لكل turn من الـenv (`harness-mcp-proxy.ts:12`).
- نفس الفكرة في الكمبيوتر: `OMB_CONTROL_URL/TOKEN` (`local-computer-proxy.ts:5-12`).

المقترح مرسمش topology ولا token لكل run. عندنا مشكلتين إضافيتين:

- **الـtoken.** الـadapters بتشيل `AGENTOS_LOCAL_TOKEN` من env الـchild عمدًا (`codex_runtime.py:183`, `agy_acp_runtime.py:486`). يعني محتاجين capability token تاني، قصير العمر ومربوط بـrun.
- **الـpackaging.** الباك إند PyInstaller **onefile** (`pyinstaller/agentos-backend.spec:3`) وبيتشغل من `resourcesPath/agentos-backend.exe` (`main.cjs:130-134`). الـproxy هيكون `agentos-backend.exe --mcp-proxy` متفتح لكل turn، وتكلفة فك onefile مش معروفة. اختباره في Phase 7 متأخر؛ لازم يدخل الـslice الأولى.

### 2. (عالي) نموذج الـrun/approval مش بيدعم الانتظار

- **مفيش حالة `waiting_approval`.** الـactive هي `queued/running` بس، في 3 أماكن على الأقل:
  - `conversation_runs.py:55`
  - `approvals.py:29`
  - `models.py:135`
  
  وكمان الـpartial unique index (`models.py:143-144`). إضافة حالة جديدة = migration + `SCHEMA_VERSION`.
- **الـtimeout بيعدّ أثناء الانتظار.** الـrun timeout افتراضي 90 وأقصى 600 (`conversation_runs.py` routes `:57`)، بينما الـapproval TTL افتراضي 300 (`approvals.py:31-32`). يعني run عمره 90 ثانية هيموت قبل ما المستخدم يوافق. لازم قرار: توقف الـdeadline وقت الانتظار؟ وهو ثابت في agy مثلًا (`agy_acp_runtime.py:181`).
- **الـApproval ماسك نص حر بس** (`action_summary` و`scope`، `models.py:214-215`). المقترح بيقول "مربوطة بالفعل ومدخلاته" لكن ده مش موجود. محتاج `tool_call_id` + `args_sha256`، والـexecutor يعيد التحقق وقت التنفيذ.
- **مفيش آلية تصحّي الـtool المنتظر.** `decide` بيكتب في الـDB بس. محتاج future/event في الذاكرة أو polling.
- **الإلغاء.** `_cancel_impl` (`conversation_runs.py:592-608`) بيقتل الـCLI فقط. أي browser action شغالة جوه الـbackend هتفضل شغالة إلا لو في registry للأدوات لكل run بيتنادى عليه من الإلغاء.

### 3. (عالي) كتابة الملفات بتكسر invariant موثّق

`deliverables.py:6-8` و`:95` بيقولوا إن ده "NOTHING is written to a workspace" مع `written_to_workspace: False` ثابتة. Phase 4 بتغير ده، فلازم تحديث للـdocs والاختبارات. كمان `Artifact` عليه `UniqueConstraint(workspace_id, relative_path)` (`models.py:404-412`) وغير مربوط بـrun، فـ"عدم تكرار الأثر عند إعادة الطلب" محتاج ربط بـ`tool_call_id`.

### 4. (متوسط-عالي) الأحداث الحالية مصممة كـlifecycle فقط

- `type` هو `String(16)` (`models.py:193`) و"approval.required" و"approval.resolved" طولهم 17. SQLite مش هيعترض لكن ده انحراف عن الـschema.
- الأهم: type = status بالتصميم (`conversation_runs.py:206`)، وكشف النهاية بيعتمد على `type in TERMINAL_STATUSES` (`:295`). خلط event kinds جديدة مع الـstatus هيلخبط. افصل `kind` عن `status`.
- كل event = transaction + `to_thread`. الـtext deltas لازم تتجمّع دفعات.

### 5. (متوسط-عالي) Profile binding ناقص في الكود، والـinstructions هتزاحم الـprompt

- المحادثة مفيهاش profile ولا engine (`models.py:108-116`).
- `TurnCreate` بياخد engine/model/prompt بس (`routes/conversation_runs.py:51-59`).
- `_prepare_turn_inner` مفيهوش instructions (`conversation_runs.py:461-463`).
- `AgentProfile.engine` nullable و`default_model_id` افتراضيه موديل OpenCode المجاني (`models.py:249,255`). الـfreeze لازم يعالج `NULL`.
- الـprompt محدد بـ24000، وميزانيته محسوبة (`:513`)، والـadapters بتقطع الـ**نهاية** بـ`prompt[:MAX]` (`codex_runtime.py:316`, `claude_runtime.py:255`). يعني الـinstructions الجديدة لو ما اتحسبتش هتقطع رسالة المستخدم الحالية.
- زود `tool_policy` في الـsnapshot من أول migration عشان ما تعملش migration تاني بعد كده.

### 6. (متوسط) Phase 2 فعلًا ممكن تتحول لـrefactor ضخم

- الـchat والـCEO والـgreeting والـworkflows لسه على مسارات منفصلة (`STATUS-AR:26,28`, `HANDOFF-AR:32-33`).
- الـ`AgentRuntime` الحالي `execute_task → AgentResult` واحد، مفيهوش events ولا run_id (`base.py:38-75`).
- عندنا أصلًا registry مكرر: `engine_registry.py:8-21` و`_default_runtime_factory` في `conversation_runs.py:115-131`.

الحل: contract إضافي optional (`tools`, `events sink`, `UnsupportedCapability`) فوق الموجود، من غير ترحيل legacy.

### 7. (متوسط) "Codex" و"OpenCode" مش adapters بتتوسع بـflag

- Codex: schema أحداث `codex exec --json` **غير متحقق منه live** (`codex_runtime.py:29-34`)، وRuseBot بيستخدم app-server JSON-RPC لأن منه بتيجي الموافقات. ده rewrite مش flag.
- OpenCode: مسموح بس بـagentين من إعدادنا (`opencode_runtime.py:239-250,594-598`)، وسياسة الأدوات جوه تعريف الـagent. ما فحصتش آليات MCP عنده.

### 8. (تنبيه) الـ6 skipped على Windows هي symlinks

يعني escape عبر symlink **مش متختبر فعليًا** على جهازكم. اختبروا الـjunctions (`mklink /J` مش محتاج admin) بالإضافة لـUNC والـdrive-letter والـADS والأسماء المحجوزة.

---

## ثالثًا: وحدات RuseBot: أعيد استخدام ولا أعيد بناء؟

| الوحدة | القرار | ملاحظات |
|---|---|---|
| `shared/tool-scope.ts` (135 سطر) | **أعد تنفيذها في Python + انقل حالات الاختبار** | فيها قرار مهم: `allow` غير محدد = يرث الكتالوج كله (`:41,71`)، فإحنا نعمل default-deny. `toolScopeWidens` (`:106`) مفيد لما الـprofile يتعدل. |
| `mcp-gate.ts` | **انقل الفكرة بس** | مش self-contained: بيستورد ~10 modules (`:6-16`). المفيد: فلترة `tools/list` (`:358-366`)، رفض `tools/call` عند التنفيذ (`:284-294`)، fail-closed على frames/ids غلط (`:274,303,329-338`)، reap للـchild (`:231-246`). تحذير: أدوات وسيطة زي `use_tool` بتتخطى فحص الاسم (`:290`). |
| `turn-mcp.ts` | فكرة fail-closed | لو في toolScope وما في gate يرمي error (`:49-56`). |
| `runtime-events.ts` | ضيّق للمجموعة اللازمة | `request.opened/resolved` و`turn.wait_started` (`:77-97,116-161`). |
| `browser/tools.ts` | **عقود + حالات اختبار** | schemas مغلقة (`:121-127,163-164`) وaliases (`:181-196`). |
| `session-control.ts` (~140 سطر) | **أعد تنفيذها** بـasyncio Lock + epoch | مقتبسة من T3 Code بترخيص MIT (`:1-3`). |
| `computer-control.ts` | **أعد تنفيذها** | تقرر الرفض بدل الـqueue، وفي الذاكرة عمدًا (`:1-3`). |
| `mcp-bridge.ts` | انقل الفكرة | فحص الـgate قبل كل `tools/call` وfail-closed عند فشل الفحص (`:150`)، ورفض النداء المتأخر (`:151`)، وـ`ping` بيتجاوب في الـbridge لأن الـdriver بيرجع -32601 (`:173`). |
| `server/testing/*` | **مصدر protocol transcripts** | `fake-claude-cli.ts`, `fake-acp-cli.ts`, `fake-agy-cli.ts`, `fake-mcp-server.ts`, `hostile-site.mjs`, `fake-pages.ts`. حوّلوها لـfake scripts بايثون على نمط `[python, fake.py]` الموجود. تحقق منها مقابل نسخ الـCLI المثبتة. |

**ترخيص:** بعض الملفات مقتبسة من T3 Code (MIT) وpuppeteer (Apache)، مسجلة في `NOTICE:9-18,47,50-54`. أي نقل لازم يحمل إشعارهم مش إشعار RuseBot بس.

---

## رابعًا: Python Playwright ولا TS bridge؟

**ملاحظة مهمة:** RuseBot **ترك Playwright عمدًا** لصالح CDP driver (`service.ts:7`, `dispatch.ts:6`, `tools.ts:5`). الكود ما بيشرح السبب، فما أقدرش أقول أسبابه.

**TS bridge:**
- الكود مش قابل للاستخراج الصغير. `service.ts:12-15` و`policy.ts:6` بيستوردوا `browser-lane`, `browser-runtime`, `browser-secret-hold`, `shared/redact`, `electron/egress-addresses.mjs`.
- بيضيف process تاني + IPC + auth، ولازم يتوصّل من Electron main.
- لو أخدت الـCDP driver كامل، بتاخد 110 ملف وتبعياتهم.

**Python Playwright:**
- ثقيل على الـpackaging: الـwheel بتشحن Node driver جواها، فميزة "من غير Node" وهمية.
- المتصفح: ممكن channel لـEdge الموجود على Windows بدل تحميل Chromium (غير متحقق من توفره عند المستخدمين).
- refs المستقرة وإبطالها عند التنقل هتحتاج طبقة عندنا، وده حسب نسخة Playwright. تحققوا من النسخة قبل الاعتماد.
- **سياسة الـegress هي المخاطرة الحقيقية.** سياسة RuseBot بتصنّف العناوين بعد الـresolve وبتغطي rebinding (`policy.ts:14,33-35`). لو الاعتماد على `route()` وحده، DNS rebinding والـWebSocket والـservice workers ممكن تفلت (من معرفتي بـPlaywright لا من الكود، يلزم تحقق). البديل الأقوى بغض النظر عن الـdriver: forward proxy خاص بينا في Python يعمل resolve ويصنّف ويتصل بنفسه، وChrome يتوجه له.

**توصيتي:** Python، لكن بعد spike محدود وخلف `BrowserDriver` Protocol عشان القرار قابل للتبديل. Python يملك control و refs و egress و approvals، ومفيش سبب ينتقل أي منهم لـNode.

---

## خامسًا: CUA على Windows: تبعية مخفية كبيرة

"Python يملك دورة حياة الـdriver" **مش متاحة مباشرة**:

- الـhost على Windows هو Node SDK: `@trycua/cua-driver/embedded` + `cua_driver_sdk.dll` + `cua_driver_node_runtime.node` (`prepare-cua-win.mjs:115-126`, `cua.mjs:157-173`). `EmbeddedCuaDriverHost.start()` بيرجّع socket (`cua.mjs:225-236`).
- على Windows الربط بـdaemon المستخدم الخارجي ممنوع عمدًا (`cua.mjs:176-178,286-288`).
- في patch للـPE لنسخة GUI-subsystem (`cua.mjs:132-138`, `cua-windows-background.mjs:2-8`) و patch لـresolver داخل الـbundle (`prepare-cua-win.mjs:148-163`)، وإصدار مثبت 0.28.2 بـchecksum من GitHub (`:23-27`).

**الشكل الواقعي:** Electron main يملك الـhost (start/stop عند الإغلاق) وينشر descriptor، وPython يستهلكه ويملك الـgate والـcontrol والـpolicy والـapprovals. ده نفس نمط `local-computer.ts:20-47`.

نقطتين لازم تدخل في التصميم:

- **self-approval:** الـCUA بيتحكم في الشاشة كلها، بما فيها نافذة Loqman اللي فيها زرار الموافقة. الـagent يقدر يضغط "Approve" بنفسه. RuseBot عنده `approvalScope: "local-computer"` وحقل `source` يميز user عن غيره (`runtime-events.ts:137,153-161`)، لكن القرار عندنا لازم يكون خارج متناول الـCUA (استبعاد نافذة التطبيق أو قناة موافقة مش قابلة للنقر من الـagent).
- **قائمة أدوات Windows:** ما لقيتش قائمة أدوات Windows مثبتة. `local-computer.ts:7` فيها `REQUIRED_LINUX_TOOLS` بس، وأي اختبار على Windows في `cua-windows-isolation.test.mjs:32` بيعمل mock للـplatform. ما تعتبروش دليل على قدرات Windows.

---

## سادسًا: ترتيب مراجَع

| # | المرحلة | المحتوى |
|---|---|---|
| 0 | قرارات + spikes صغيرة | فحص `claude --help` على النسخة المثبتة، قياس تكلفة تشغيل `--mcp-proxy` من الـonefile exe |
| 1 | Profile binding + snapshot | يشمل instructions في ميزانية الـprompt، و`tool_policy_json` في الـsnapshot، وmigration مع `SCHEMA_VERSION` |
| 2 | **الـslice الرأسية** (يدمج 2+3+جزء 4) | تفاصيلها تحت |
| 3 | محرك تاني (Antigravity ACP) | يثبت إن العقد مش خاص بـClaude. هو الأسهل لموافقات native لأننا أصلًا ماسكين حلقة JSON-RPC (`agy_acp_runtime.py:267-277`) لكن plan-mode ممكن يعطّل الأدوات، غير متحقق |
| 4 | Browser: spike ثم مجموعة صغيرة | open/read/snapshot/click/fill/close + egress proxy |
| 5 | CUA | بعد قرار ملكية الـhost وحماية self-approval |
| 6 | ترحيل chat/CEO/workflows + packaging النهائي | بعد ما الأدوات تشتغل |

### محتوى الـslice (الخطوة 2)

- Claude adapter مع `--mcp-config` لـserver بتاعنا و`--strict-mcp-config` و`--tools ""`، مع إضافة الـflags لـ`REQUIRED_FLAGS` (fail-closed). النتيجة: **closed-world**، يعني كل فعل بيمر من خلال server بتاعنا، وده إنفاذ فعلي بدون ما ننتظر approvals native. (`--mcp-config` و`--allowedTools` من معرفتي بالـCLI، **لم أتحقق منها**؛ إعدادات المستخدم لسه بتتحمّل حسب `claude_runtime.py:16-19` ولازم تتفحص.)
- أداتين: `workspace_read` و`workspace_write` (بموافقة).
- حالة `waiting_approval` مع pause للـdeadline، وapproval مربوط بـ`tool_call_id` + `args_sha256`، وregistry للأدوات يتأثر بالإلغاء.
- event `kind` منفصل عن `status`.
- لا gate عام لسيرفرات طرف ثالث الآن؛ لما الـserver ملكنا، مش بنسجّل الأدوات الممنوعة أصلًا، ونعيد الفحص وقت الاستدعاء.

### معايير القبول (قابلة للاختبار بـfake CLI)

1. أداة `workspace_write` بتوقف الـrun على `waiting_approval`؛ deny → الملف غير موجود؛ approve → الملف يتكتب **مرة واحدة** حتى لو الطلب اتكرر بنفس `tool_call_id`.
2. approve بعد cancel أو timeout أو من run تاني → 409 والملف غير موجود.
3. استدعاء أداة ممنوعة باسمها مباشرة بيترفض عند التنفيذ حتى لو ما كانتش في `tools/list`.
4. traversal وjunction وUNC وdrive-letter وADS وأسماء Windows المحجوزة.
5. restart أثناء الانتظار → الـrun `interrupted` والـapproval `expired` (الآلية موجودة في `recover_interrupted`، `conversation_runs.py:330-349`).
6. SSE replay بترتيب صحيح مع الـkinds الجديدة وLast-Event-ID.
7. smoke من الـfrozen exe (`--mcp-proxy`) مش من الـvenv.
8. اختبار حقيقي يدوي اختياري بحساب فعلي، ومعلّم إنه كده.

**أجّل:** ترحيل legacy، gate عام لسيرفرات الغير، Codex app-server، OpenCode tools، shell، eval في المتصفح، CUA.

---

## سابعًا: قرارات لازم تتحسم

1. المحرك الأول: Claude closed-world؟ وإن Codex خارج أي ادعاء "policy enforced" لحد app-server.
2. نموذج الـrun: إضافة `waiting_approval` (مع migration للـindex) ولا إبقاء `running` + approval معلّق؟ وهل الـdeadline بيتوقف؟
3. فصل `kind` عن `status` في الأحداث، وسياسة batching للـdeltas.
4. هوية الـtool call وربط الموافقة بـhash، وموافقة "مرة واحدة" فقط بدون "always".
5. طوبولوجيا MCP: proxy رفيع + HTTP callback + token لكل run؛ وSDK `mcp` الرسمي ولا JSON-RPC يدوي (مفيش dependency حاليًا على `mcp`).
6. المتصفح: نتيجة الـspike وقرار Edge مقابل Chromium، وegress proxy.
7. مين يملك CUA host، وحماية self-approval.
8. إدارة التراخيص عند نقل أي ملف.
9. default policy = deny، وأنواع الأدوات اللي تتطلب موافقة دايمًا.

---

## اللي ما فحصتوش

- `runtime/ledger.py` و`core/workspace_fs.py` و`core/run_inputs.py` (الأخيرين غالبًا فيهم path validation نعيد استخدامه في Phase 4 بدل ما نبني من الصفر).
- تفاصيل OpenCode HTTP/MCP.
- `server/drivers/claude.ts` و`codex.ts` (كلامي عن `--permission-prompt-tool` وapp-server من معرفتي العامة).
- `browser/cdp/refs.ts` و`snapshot.ts` و`egress/gate.ts`.
- `docs/verification/README.md` في RuseBot (AGENTS.md بيطلب fixtures معزولة قبل أي ادعاء).
- قائمة أدوات CUA الفعلية على Windows.
- ما شغّلت أي كود، وما تحققت من سلوك أي CLI.

تحذير الـhook عن "invisible-unicode" في `codex_runtime.py` و`claude_runtime.py` على الأرجح سببه حرف BOM الحرفي داخل `lstrip("﻿")`، مش محتوى مشبوه.
