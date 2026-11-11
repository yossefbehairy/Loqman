# دراسة باك إند Python بدون FastAPI

التاريخ: 11 نوفمبر 2026. الدراسة مبنية على قراءة الكود وجرد imports وroute decorators، ومراجعة وثائق Python 3.13 وElectron. لم يتغير كود التشغيل، ولم يُبنَ بديل أو يُختبر أداؤه. أرقام الاختبارات السابقة لا تثبت صلاحية البديل المقترح.

## النتيجة

إزالة FastAPI ممكنة؛ إدارة الوكلاء والعمليات وSQLite والأدوات لا تتطلبه. لكن استبداله بسيرفر HTTP مكتوب يدويًا سيحمّل المشروع مسؤوليات transport كانت تتولاها مكتبات جاهزة. لو الهدف Desktop بلا web framework، فـPython worker دائم مع بروتوكول رسائل إلى Electron بديل يستحق تجربة محدودة. لو الهدف قابلية الصيانة، فالأولوية لفصل المنطق عن HTTP، مع إبقاء FastAPI كـadapter مؤقت أثناء الفصل.

FastAPI إطار Python؛ استخدامه لا يجعل منطق التطبيق مكتوبًا بلغة أخرى. كذلك «مكتبات Python فقط» لا تعني تلقائيًا «لا native binaries»: SQLite والمحركات والمتصفح وCUA لها مكونات تشغيل مستقلة.

## تعريف النطاق

| المقصود | المطلوب | حجم التغيير النسبي |
|---|---|---|
| Core مستقل عن FastAPI | خدمات Python لا تستورد routes أو Request أو HTTPException؛ transport يستدعيها | أقل الخيارات مخاطرة ويخدم كل البدائل |
| إزالة FastAPI فقط | استبدال routing وrequest/response وlifespan وSSE؛ يمكن إبقاء SQLAlchemy/Pydantic | تغيير ملموس في الـAPI والتشغيل والاختبارات |
| بلا web framework أو HTTP server | worker Python يتلقى أوامر JSON ويرسل نتائج وأحداثًا إلى Electron | مناسب لتقييم Desktop، ويحتاج bridge لاحقًا |
| stdlib فقط لكل كود الباك إند | استبدال Pydantic وsettings وSQLAlchemy بمكتبات قياسية وSQL مباشر، وإعادة تكييف الأدوات | إعادة بناء أوسع بكثير؛ لا تُدمج في خطوة إزالة FastAPI |

المحركات الخارجية وChromium/Edge وCUA وElectron تبقى تبعيات خارجية حتى لو كان كود التنظيم كله Python. تغيير FastAPI لا يحولها إلى Python ولا يضيف أدوات أو sandbox.

## جرد الكود الحالي

- 69 ملف Python داخل `backend/app` وقت الجرد.
- 18 ملفًا تستورد FastAPI مباشرة: `main.py` و17 ملف routes.
- 76 decorator من نوع get/post/put/etc، وdecorator إضافي `api_route` للـOpenCode proxy، داخل routes؛ أي 77 تعريف مسار قبل route الجذر وواجهات الوثائق التلقائية. proxy يخدم أكثر من HTTP method.
- 35 ملف اختبار يذكر TestClient أو `client.` أو dependency overrides؛ هذا جرد نصي للتأثر المحتمل، وليس عدد اختبارات تحتاج الحذف أو إعادة الكتابة.
- SQLAlchemy مستخدم مباشرة في 28 ملفًا؛ يتداخل مع core وorchestration وledger، وليس طبقة تخزين قابلة للاستبدال بمجرد حذف import واحد.
- Pydantic مستخدم مباشرة في 22 ملفًا؛ يشمل عقود runtime وplanner وvalidator والإعدادات.
- `backend/app/cli.py` يشغّل Uvicorn؛ و`pyinstaller/agentos-backend.spec` يجمع Uvicorn وanyio وsse-starlette.
- Electron ينتظر `GET /api/health` ويعرض عنوان API محليًا. الإبقاء على HTTP contract يسمح باستبدال transport مع تقليل أثره على المستهلكين؛ إزالة HTTP تستلزم تغيير الربط لاحقًا، حتى دون إعادة تصميم الواجهة.

## الحدود غير المستقلة حاليًا

معظم core لا يستورد FastAPI، لكن هناك اعتماد عكسي مؤكد: `manager_for_factory` في `backend/app/core/conversation_runs.py` يستورد `get_run_manager` من routes. والـsingleton نفسه مملوك لملف route. ننقله إلى composition root/application container قبل ادعاء أن core مستقل.

بعض قواعد العمل ما زالت داخل routes، مثل قرارات CEO والتعامل مع المحادثات وتنسيق بدء المهام؛ لذلك ليس كافيًا استبدال decorators. تُنقل الخدمات فقط عند الحاجة إلى تشغيلها من transport ثانٍ، دون ترحيل شامل لكل المسارات أولًا.

## البديل الأول: HTTP من مكتبات Python القياسية

يمكن بناء adapter فوق `http.server.ThreadingHTTPServer`، مع event loop دائم واحد لإدارة runs وsubprocesses. عند الحاجة تُرحّل الطلبات إلى هذا loop بدل إنشاء loop لكل طلب. SQLite sessions تكون مستقلة لكل عملية، ولا نشارك Session واحدة بين threads.

لكن يصبح مطلوبًا تنفيذ وصيانة:

- router، path/query parsing، method handling وvalidation وردود errors المتوافقة.
- مصادقة local token وHost وOrigin/CORS وOPTIONS.
- حد body/header وحالات JSON التالف وContent-Length والمهلات وحدود الاتصالات.
- SSE: framing وheartbeat وreplay وLast-Event-ID، backpressure، disconnect وتنظيف الاشتراكات.
- تنزيل الملفات وheaders وإغلاق الموارد وcleanup عند انقطاع المستهلك.
- startup/crash recovery وshutdown وقت تشغيل مهام وعمليات فرعية.
- عقد API موثق بديل للـOpenAPI والتوثيق التلقائي إذا احتجناهما.

وثائق Python تنص صراحة أن `http.server` غير موصى به للإنتاج وأن فحوص الأمان فيه أساسية. التشغيل على loopback يقلل التعرض لكنه لا يعوض مراجعة البروتوكول. لا أوصي باعتماده كبديل نهائي لمجرد تقليل الاعتماديات.

`asyncio.start_server` يوفر TCP streams لا HTTP implementation. بناء HTTP parser يدوي فوقه سيكون مسؤولية جديدة للمشروع، وليس بديلًا جاهزًا لـFastAPI.

## البديل الثاني: Python worker ورسائل JSON — مرشح Desktop

```text
واجهة التطبيق
     ↕ API ضيقة في preload، لاحقًا عند ربط المنتج
Electron main: تشغيل worker والتحقق من المرسل وتمرير الأوامر
     ↕ stdin/stdout: طلبات ونتائج وأحداث JSON مؤطرة
Python application: services + Run Manager + policy + approvals
     ↕
SQLite / engine CLIs / browser driver / computer bridge
```

هنا لا يوجد HTTP server بين Electron والـworker. يبقى Python هو مالك البيانات والسياسات وحالة التشغيل. Electron يملك العملية والـbridge فقط؛ لا نكرر فيه قواعد الموافقة أو الحالة.

البروتوكول الأدنى يحتاج: request id وmethod وparams، result/error، event sequence وrun id، ready/shutdown، subscribe/replay وcancel. يمكن تطبيق مجموعة صغيرة من JSON-RPC 2.0 إذا التزمنا بعقدها؛ لا نسمي أي JSON lines بروتوكول MCP تلقائيًا.

ضوابط التصميم:

- loop دائم واحد؛ قراءة stdin الحاجبة تُعزل عنه بطريقة قابلة للاختبار على Windows. لا نفترض دعم add_reader على stdin في Windows، ولا ننفذ asyncio.run لكل رسالة.
- stdout للبروتوكول فقط، stderr للسجلات، queue محدودة وكاتب واحد، وحد أقصى لحجم frame قبل فك JSON.
- readiness رسالة صريحة، EOF من الأب ينهي التشغيل، وshutdown يلغي العمليات والمهام ضمن مهلة.
- إلغاء طلب RPC لا يساوي إلغاء run تلقائيًا؛ run له هوية وعمر مستقلان.
- الأحداث المهمة محفوظة في SQLite؛ فقد رسالة في pipe لا يفقد تاريخ التشغيل. replay عبر sequence بديل دلالي لـSSE.
- preload يعرض أوامر محددة ويتحقق main من المرسل؛ لا نعرض raw ipcRenderer أو تنفيذ Python عام.
- تنزيلات/صور كبيرة تمر بمراجع artifacts مع فحص الصلاحيات بدل حشرها في كل رسالة JSON.

مزاياه المحتملة: إزالة HTTP/CORS والمنافذ من مسار Desktop، وعقد أوامر يناسب worker المحلي. هذه مزايا معمارية متوقعة؛ تحسن الأداء أو الحجم أو startup يحتاج قياسًا.

كلفته: العميل الحالي وElectron health check وPlaywright fixtures يعتمدون على HTTP. يمكن دراسة worker واختباره دون تعديل React، لكن التحول النهائي إلى IPC لا يحدث مع إبقاء كل الربط كما هو. نسخة browser development تحتاج HTTP adapter اختياريًا أو harness آخر.

## MCP والمتصفح وCUA بعد إزالة HTTP

- CLI قد يطلق MCP proxy كعملية مستقلة؛ لا يستطيع استخدام stdin/stdout بين Electron والـworker مباشرة. نحتاج قناة تحكم أخرى موثقة ومحدودة لكل run، أو broker يمررها عبر المالك. هذه نقطة قرار في تجربة MCP الأولى.
- لو كان الشرط «لا FastAPI» فقط، يمكن إبقاء endpoint تحكم محلي صغير بتوثيق run tokens؛ هذا يختلف عن شرط «لا HTTP إطلاقًا».
- إزالة الإطار لا تحل native tool enforcement ولا الموافقات. الخدمتان يجب أن تظلا داخل application layer.
- Python browser implementation قد يحتاج Playwright أو dependency أخرى؛ شرط stdlib-only سيضيق الاختيارات كثيرًا. لا نكتب CDP/WebSocket stack كاملًا لتحقيق شعار «pure».
- مسار CUA الذي راجعناه في RuseBot يعتمد على Electron/Node SDK على Windows؛ يمكن إبقاء هذا الجزء مع Python core، لكنه لا يحقق شرط أن كل مكونات التطبيق Python.

## خيار stdlib-only

بدائل ممكنة وظيفيًا: dataclasses/typing للعقود، validation صريح، argparse/os/tomllib للإعدادات، sqlite3 بدل ORM، وasyncio/subprocess/json/pathlib للتنفيذ. dataclasses لا تنفذ validation تلقائيًا، وsqlite3 لا تستبدل علاقات ORM والمعاملات والمهاجرات تلقائيًا.

هذا يتطلب إعادة تدقيق CAS والإلغاء وmigration وbackup والتزامن. قاعدة SQLite يمكن الاحتفاظ بملفها ومخططها إذا كتبنا SQL متوافقًا؛ لا يوجد سبب لمسح بيانات المستخدم. اختبارات التكافؤ والترقية على نسخة آمنة شرط الانتقال.

لا توجد ضرورة تقنية مثبتة الآن لإزالة SQLAlchemy أو Pydantic لمجرد إزالة FastAPI. يمكن فصل القرارين، كما يمكن إبقاء pytest كاعتمادية تطوير حتى لو قُيّدت اعتماديات التشغيل.

## التوصية وخطوات الدراسة التنفيذية

1. **فصل core عن transport:** composition root مستقل، وخدمات قابلة للاستدعاء، وApplicationError بدل HTTPException داخل الخدمات الجديدة. FastAPI الحالي يبقى adapter أثناء الفصل.
2. **تجربة worker محدودة:** ready ثم conversation create/start/status/events/cancel باستخدام mock وDB مؤقتة. لا تعديل React في هذه التجربة؛ harness مستقل يمثل Electron.
3. **تجربة frozen exe مبكرًا:** التشغيل وEOF وshutdown والمهام المتزامنة وامتلاء queue والرسائل المجزأة والتالفة. قياس زمن الجاهزية والحجم والذاكرة مقابل baseline على نفس الجهاز.
4. **MCP واحد وأداة واحدة:** إثبات قناة proxy إلى خدمة policy/run دون مشاركة token التطبيق العام، مع إلغاء ورفض موافقة واختبار restart. لا نضيف browser/CUA قبل نجاح هذا المسار.
5. **قرار بعد الدليل:** إن كان المنتج Desktop فقط والـworker نجح، نرحل الربط تدريجيًا ونزيل FastAPI من حزمة Desktop عند تحقق التكافؤ. إن احتجنا HTTP للعملاء أو التطوير، يبقى adapter اختياريًا. stdlib-only كله قرار منفصل.

معايير القبول: تكافؤ أخطاء وvalidation، عدم تشغيل أي أداة بعد cancel، replay مرتب، طلب موافقة لا يمنع استلام cancel، restart لا يعيد side effects، وإغلاق الأب لا يترك عمليات CLI يتيمة. لا ننقل كل الاختبارات الـ560 إلى transport الجديد؛ نحتفظ باختبارات core ونستبدل اختبارات النقل المتأثرة باختبارات العقد المناسبة.

## المصادر

- [Python 3.13: http.server وحدوده](https://docs.python.org/3.13/library/http.server.html)
- [Python 3.13: asyncio streams وstart_server](https://docs.python.org/3.13/library/asyncio-stream.html)
- [Python 3.13: subprocesses ومتطلبات Windows](https://docs.python.org/3.13/library/asyncio-subprocess.html)
- [Python 3.13: event loop implementations](https://docs.python.org/3.13/library/asyncio-eventloop.html)
- [Python 3.13: sqlite3 والمعاملات](https://docs.python.org/3.13/library/sqlite3.html)
- [Electron: IPC وpreload](https://www.electronjs.org/docs/latest/tutorial/ipc)
- [FastAPI: validation وdependency injection وOpenAPI](https://fastapi.tiangolo.com/features/)
