# متابعة الباك إند

اقرأ `BACKEND-BUILD-PLAN-AR.md` و`BACKEND-IMPLEMENTATION-STATUS-AR.md` قبل الاستكمال. المشروع فيه تعديلات سابقة غير ملتزمة للمستخدم؛ حافظ عليها، ولا تعمل reset أو clean أو commit شامل.

## التحقق

من جذر المشروع استخدم `backend\.venv\Scripts\python.exe -m pytest backend/tests -q`. Python الموجود افتراضيًا على PATH غير صالح. لا تستخدم قاعدة بيانات المستخدم للتجارب؛ استخدم قاعدة SQLite ومجلدًا مؤقتين. عند تعديل schema، زوّد `SCHEMA_VERSION` وأضف ترقية قابلة للتكرار واختبر نسخة قديمة ونسختها الاحتياطية.

## تشخيص flaky في test_mission_engine_selection (Claude، 2026-11-06)

- **العَرَض:** `test_runtime_error_from_selected_engine_fails_task` يعطي أحيانًا `failed` بدل `partially_succeeded`. أعدتُ إنتاجه: 8 فشل من 40 تشغيلًا في عمليات منفصلة، وظهر العَرَض نفسه في اختبارات أخرى بالملف (`partially_succeeded` بدل `succeeded`).
- **السبب الجذري:** `backend/tests/conftest.py` كان يستخدم `sqlite:///:memory:` مع `StaticPool`، أي اتصال DBAPI واحد مشترك بين كل الجلسات. الـledger يكتب عبر `asyncio.to_thread` بجلسته القصيرة، فيتقاطع مع thread الـevent loop على نفس الاتصال. ظهرت أخطاء `SystemError` و`InterfaceError` في سجل الـledger، وفشل CAS في `transition_task` لمهمة سليمة فانتهت كـ`cancelled` ("Cancelled by user.") فتغيّرت حالة المهمة وحالة الـmission.
- **ليس خطأ إنتاج:** الإنتاج يستخدم ملف SQLite واتصالًا لكل جلسة مع WAL وbusy_timeout (`db/session.py`). لم أعدّل كود الإنتاج.
- **الإصلاح (ملف اختبار فقط):** `conftest.py` يستخدم الآن قاعدة SQLite مؤقتة في مجلد temp (تُحذف عند الخروج) باتصالات منفصلة، مع `foreign_keys=ON` و`WAL` و`busy_timeout`. لم تُستخدم بيانات المستخدم ولا استدعاءات موديل حقيقية.
- **النتائج بعد الإصلاح:** 0 فشل من 40 تشغيلًا للملف نفسه (كان 8/40). الحزمة الكاملة `backend\.venv\Scripts\python.exe -m pytest backend/tests -q`: `476 passed, 6 skipped` مرتين متتاليتين.

### للمراجعة من Codex

- راجع تغيير `conftest.py` (الاستيرادات `StaticPool` لم تعد مستخدمة هناك؛ `backend/scripts/real_opencode_check.py` يذكر `test_engine` أو `StaticPool` ولم ألمسه).
- ملاحظة تصميم غير معدّلة: عند فشل CAS في `_execute_single_task` تُنهى المهمة كـ`cancelled` حتى لو السبب خطأ DB وليس إلغاءً فعليًا. هذا يخفي أعطال التخزين؛ يُستحسن التمييز بين الإلغاء الفعلي وفشل الكتابة.
- الـ6 اختبارات skipped لم أفحص أسبابها بالتفصيل.

## تحديث التنفيذ — 2026-11-07

- Antigravity مدمج الآن كمحرك `antigravity` عبر ACP (`agy acp --stdio`) في conversation runs وmission workers ومراحل التخطيط والتجميع. adapter يفرض وضع plan ويرفض طلبات الصلاحية؛ هذا ليس عزل workspace ولا موافقة تفاعلية. لا تستخدم Gemini CLI. إعداد `RUNTIME_MODE=agy` القديم ما زال adapter تجريبيًا مختلفًا، ويظهر في سجل المهمة باسم `agy-legacy-cli`.
- تخطيط كل mission وتجميع نتيجتها ومحاولات workers والتفويض الصريح لها runs محفوظة وأحداث استخدام مرتبطة بها. الخطة تُتحقق قبل نجاح run، ومحرك الموظف وموديله يُفحصان من كتالوجه قبل أي DB write؛ موديلات الحسابات غير مجانية ممكنة للمحركات غير OpenCode مع إبقاء حالة الفوترة unknown. `GET /api/missions/{id}/runs` يعرض المراحل والمحاولات.
- اختبارات `TestClient` ذات lifespan معزولة عن قاعدة بيانات التطوير. اختبار restart المتداخل يمرر factory الجديدة إلى crash recovery والـrun manager أيضًا.
- آخر suite كامل بعد عزل TestClient وتوحيد تسجيل مراحل الـmission والتفويض الصريح والتحقق من كتالوج المحرك وهو: `560 passed, 6 skipped, 1 warning`. التحذير deprecation من httpx/Starlette. أعد suite كامل بعد أي تعديل لاحق قبل إعلان backend منتهي.

## الأولويات المتبقية

1. راجع [حالة التنفيذ](BACKEND-IMPLEMENTATION-STATUS-AR.md) قبل أي تعديل؛ واجهة React لم تُربط بعد بمسارات `/api/conversations` و`/api/conversation-runs` الجديدة.
2. التحية ما زالت تستدعي runtime خارج ConversationRunManager؛ workflows القديمة لها نظام حالة مستقل. workflows لديها CAS/إلغاء لكن لم تنتقل إلى سجل runs الموحد.
3. workspace registry يضبط ما يقرأه التطبيق فقط؛ CLI نفسه غير معزول بالـworkspace. لا تُكتب artifacts إلى القرص حتى يتوفر إنفاذ فعلي يمكن اختباره. موافقات الأدوات غير مدعومة native حاليًا.
4. لا يوجد بث نص تدريجي أو استكمال native sessions؛ أحداث conversation-run هي lifecycle فقط. فشل الإلغاء البعيد يظل غير مؤكد حسب المحرك.
5. لا يزال استيراد `localStorage`، واختبار restore على نسخة بيانات آمنة، وWindows build نظيف واختبار مستخدم جديد مطلوبة.

تجارب المحركات الحقيقية رسائل قصيرة فقط؛ لا تثبت الفوترة أو عمل كل موديل أو حدود أدوات المحرك. لا تخلط نجاح run بموافقة المستخدم على تنزيل/كتابة مخرج.
