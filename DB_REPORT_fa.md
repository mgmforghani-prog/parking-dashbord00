# گزارش جامع ساختار دیتابیس ParkingDB و اتصال داشبورد Sanjeman

**منابع:** parking_db_schema_CSV.csv (ستون‌ها/نوع/Null)، parking_db_relationships_CSV.csv (فقط FKهای ثبت‌شده)، فهرست نام جدول‌ها (.txt)، کد app.py/db.py/analytics.py.

## محدودیت‌های صریح

- ✔ دسترسی مستقیم به SQL Server در این محیط وجود نداشت؛ **تعداد ردیف‌ها، کلید اصلی (PK)، ایندکس‌ها، تعریف View‌ها و محتوای GateTypes/Gates استخراج نشد**. فقط از فایل‌های Schema/Relationship استفاده شد.
- فایل Schema فقط نام/نوع/Nullable دارد؛ PK ندارد. در فایل روابط **فقط ۱۹ FK** ثبت شده و هیچ FK ای برای Gates/GateTypes/Taradods وجود ندارد. پس هر رابطه‌ای خارج از ۱۹ FK «تأییدنشده» است و کاردینالیتی آن‌ها با Query زیر باید تأیید شود.
- معنای `GateTypeID` (ورود/خروج/دوطرفه…) **از فایل‌ها قابل اثبات نیست**؛ در کد فقط عنوان `GateTypes.Title` نمایش داده می‌شود. نقش ورودی/خروجی همچنان از ستون `InputGateID`/`OutputGateID` تردد گرفته می‌شود.
- **ناسازگاری نام‌ها بین فایل فهرست و Schema** (هیچ‌کدام اصلاح یا حدس زده نشد؛ کد از نام‌های Schema استفاده می‌کند):
  - فهرست: `ANNPRPlates` ↔ Schema/کد: `ANPRPlates`
  - فهرست: `CarID` ↔ Schema: `CardID`
  - فهرست: `DiscountCategories` ↔ Schema: `DiscountCategorys`
  - Schema: `View_Customer_Cards_Detials␠` (با فاصله‌ی انتهایی) ↔ فهرست بدون فاصله؛ کد هر دو را امتحان می‌کند.
- `offline_db.py` و پوشه‌ی Snapshot موجود پروژه آپلود نشده بود؛ فرمت اسنپ‌شات‌های فعلی دیده نشد (بخش «حالت آفلاین»).
- تست زنده با SQL Server و اجرای کامل Streamlit در این محیط ممکن نبود (بخش «نتیجه‌ی تست‌ها»).

## Query تأیید PK/FK/تعداد ردیف روی سرور (فقط خواندنی)

```sql
SELECT s.name AS sch, t.name AS tbl, SUM(p.rows) AS row_count FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
JOIN sys.partitions p ON p.object_id=t.object_id AND p.index_id IN (0,1) GROUP BY s.name, t.name ORDER BY s.name, t.name;
SELECT OBJECT_SCHEMA_NAME(ic.object_id) AS sch, OBJECT_NAME(ic.object_id) AS tbl, c.name AS pk_col FROM sys.indexes i
JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id WHERE i.is_primary_key=1;
SELECT ID, Title, GateTypeID FROM dbo.Gates ORDER BY ID;  SELECT ID, Title FROM dbo.GateTypes ORDER BY ID;
-- یکتایی Gates.ID و ID های بدون نگاشت در تردد:
SELECT ID, COUNT(*) c FROM dbo.Gates GROUP BY ID HAVING COUNT(*)>1;
SELECT DISTINCT t.InputGateID FROM dbo.Taradods t WHERE NOT EXISTS (SELECT 1 FROM dbo.Gates g WHERE g.ID=t.InputGateID);
SELECT DISTINCT t.OutputGateID FROM dbo.Taradods t WHERE t.ExitDateTime IS NOT NULL AND NOT EXISTS (SELECT 1 FROM dbo.Gates g WHERE g.ID=t.OutputGateID);
```

## خلاصه‌ی موجودی: 109 نام در فهرست؛ 109 شیء در Schema؛ همه بررسی شدند

- جداول عملیاتی: 14
- جداول مرجع/تنظیمات/تجهیزات/تعرفه/مشتری (طبقه‌بندی بر پایه نام و ستون‌ها؛ تأییدنشده): 68
- جداول مالی: 11
- جداول لاگ: 7
- جداول کاربران/دسترسی: 1
- Viewها: 8

**فهرست نام‌ها در برابر Schema:** همه‌ی ۱۰۹ نام فهرست بررسی شد؛ ۳ نام با Schema اختلاف املایی دارد (بالا) و ۱ مورد فاصله‌ی انتهایی دارد.

## FKهای ثبت‌شده در فایل روابط (کل ۱۹ مورد)

- `dbo.ActivityLogs`.ActivityId → dbo.Activities.Id
- `dbo.BlockPlates`.PlateId → dbo.Plates.ID
- `dbo.CustomerCards`.CustomerGoupID → dbo.CustomerGroups.ID
- `dbo.CustomerCards`.CustomerID → dbo.Customers.ID
- `dbo.CustomerPlates`.CustomerID → dbo.Customers.ID
- `dbo.DiscountCodes`.DiscountID → dbo.Discounts.ID
- `dbo.Discounts`.DiscountTypeID → dbo.DiscountTypes.ID
- `dbo.DiscountTypes`.PricingProfileID → dbo.PricingProfiles.ID
- `dbo.EventLogs`.UserSessionID → dbo.UserSessions.ID
- `dbo.PosDevices`.PcPosCompanyId → dbo.PcPosCompanies.ID
- `dbo.UserSessions`.UserLogoffReasonID → dbo.UserLogoffReasons.ID
- `rbac.Menus`.ParentID → rbac.Menus.ID
- `rbac.Permission_Roles`.PermissionID → rbac.Permissions.ID
- `rbac.Permission_Roles`.RoleID → rbac.Roles.ID
- `rbac.Role_Menus`.MenuID → rbac.Menus.ID
- `rbac.Role_Menus`.RoleID → rbac.Roles.ID
- `rbac.Roles`.ParentID → rbac.Roles.ID
- `rbac.User_Roles`.RoleID → rbac.Roles.ID
- `rbac.User_Roles`.UserID → rbac.Users.ID

## جزئیات هر جدول/View
ستون‌ها به‌صورت `نام:نوع` (علامت ؟ = Nullable). «PK: نامشخص» یعنی در فایل‌ها موجود نیست.


### جداول عملیاتی

**`dbo.ANPRPlates`** (TABLE) — 10 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:bigint، PlateImage:varbinary؟، CarImage:varbinary؟، CreateDate:datetime، Confidence:float، P1:nvarchar؟، P2:nvarchar؟، P3:nvarchar؟، P4:nvarchar؟، GateId:int؟
- مصرف در داشبورد: صفحه فنی: کیفیت ANPR (anpr) — اختیاری (صفحه فنی)
- ریسک: دارای ستون باینری/تصویر؛ در Snapshot و Queryهای تحلیلی انتخاب نشود.

**`dbo.BlockPlates`** (TABLE) — 11 ستون · PK: نامشخص · FK خروجی: PlateId → dbo.Plates.ID · FK ورودی: —
- ستون‌ها: Id:int، CreateDate:datetime، CreateUserId:int، PlateId:int، BlockCode:nvarchar؟، BlockDescription:nvarchar؟، UnblockCode:nvarchar؟، UnblockDescription:nvarchar؟، UpdateDate:datetime، UpdateUserId:int، IsBlock:bit
- مصرف در داشبورد: blocked (با پشتیبان از Taradods) — اختیاری

**`dbo.CheckRequests`** (TABLE) — 5 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، CraeteDate:datetime، Image:varbinary؟، TaradodID:bigint؟، Manual:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- ریسک: دارای ستون باینری/تصویر؛ در Snapshot و Queryهای تحلیلی انتخاب نشود.

**`dbo.CustomerLogHelper`** (TABLE) — 4 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:bigint، CustomerId:int، EnterDateTime:datetime، ExitDateTime:datetime؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.Guests`** (TABLE) — 7 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، UserID:int، TaradodID:bigint، Epan:varchar؟، CreateDate:datetime، Description:nvarchar؟، SendToServer:bit
- مصرف در داشبورد: پشتیبان کوئری view (EXISTS) و بخش مهمان (guests) — اختیاری/پشتیبان

**`dbo.KioskPlates`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، ANPRPlateId:bigint، PlateId:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.ParkNights`** (TABLE) — 9 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، UserId:int، DateTime:datetime، CreateDate:datetime، P1:nvarchar، P2:nvarchar، P3:nvarchar، P4:nvarchar، Description:nvarchar؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.ParkNightSys`** (TABLE) — 9 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: LogId:bigint، LogType:int، CreateDate:datetime، EnterDate:datetime، P1:nvarchar؟، P2:nvarchar؟، P3:nvarchar؟، P4:nvarchar؟، TagId:varchar؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.Plates`** (TABLE) — 8 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: dbo.BlockPlates.PlateId
- ستون‌ها: ID:int، CreateDate:datetime، UpdateDate:datetime، P1:nvarchar؟، P2:nvarchar؟، P3:nvarchar؟، P4:nvarchar؟، FailureCount:int
- مصرف در داشبورد: پلاکِ خودروهای داخل (inside) — JOIN یک‌به‌یک روی Plates.ID — ضروری برای پلاک

**`dbo.Snapshot`** (TABLE) — 10 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، DateTime:datetime، InputImage:varbinary؟، TagID:varchar؟، DateTimeString:varchar؟، IsOut:bit، ReaderRequest:bit، RequestTypeID:int؟، UserID:int؟، CustomerLogID:bigint؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- پیشنهاد بخش مستقل: حاوی تصویر؛ فقط در صفحه‌ی مستقل و با دسترسی
- ریسک: دارای ستون باینری/تصویر؛ در Snapshot و Queryهای تحلیلی انتخاب نشود.

**`dbo.Taradods`** (TABLE) — 29 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، CarTypeID:int، CardID:varchar، EnrtyDateTime:datetime، EntryUserID:int، InputGateID:int، EntryImage:image؟، ExitDateTime:datetime؟، ExitUserID:int؟، OutputGateID:int، ExitImage:image؟، TotalPrice:money؟، FinalPrice:int؟، PaymentID:int؟، PosDetailID:bigint؟، IsLost:bit؟، Cash:int؟، Pos:int؟، Refund:int؟، UserDescription:nvarchar؟، PlateID:int؟، SendToServer:bit، VATPrice:int؟، ANPRPlateId:bigint؟، RoundPrice:int، ExitPlateID:int؟، ExitANPRPlateID:bigint؟، Epan:varchar؟، PosTerminalNo:bigint؟
- مصرف در داشبورد: همه‌ی صفحه‌ها: ردیف‌های تردد (taradods)، تجمیع مالی (fin_tx)، خودروهای داخل (inside/inside_audit)، تصاویر — ضروری
- ریسک: `InputGateID/OutputGateID` بدون FK به Gates؛ `OutputGateID` برای خودروی خارج‌نشده NOT NULL است (احتمالاً مقدار پیش‌فرض) → نام خروج فقط برای ترددِ خارج‌شده ساخته می‌شود. ستون‌های تصویر (EntryImage/ExitImage) سنگین‌اند و در Queryهای تجمیعی انتخاب نمی‌شوند.
- ریسک: دارای ستون باینری/تصویر؛ در Snapshot و Queryهای تحلیلی انتخاب نشود.

**`dbo.TaradodsCount`** (TABLE) — 6 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، DateTime:datetime؟، EntryCount:bigint؟، ExitCount:bigint؟، EntryCardCount:bigint؟، ExitCardCount:bigint؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- پیشنهاد بخش مستقل: KPI سبک شمارش ورود/خروج پس از تطبیق با Taradods

**`dbo.TaradodSummary`** (TABLE) — 15 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، EntryDate:date، EntryUserID:int، CarTypeID:int، InputGateID:int، ExitDate:date؟، ExitUserID:int؟، OutputGateID:int؟، TotalPrice:money؟، FinalPrice:int؟، Cash:int؟، Pos:int؟، PlateID:int؟، VATPrice:int؟، RoundPrice:int؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- پیشنهاد بخش مستقل: می‌تواند جایگزین سبک‌تر تجمیع‌های روزانه شود، پس از تطبیق جمع‌ها با Taradods

**`dbo.VipZoneLogs`** (TABLE) — 8 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، TaradodId:bigint، EnterUserId:int، EnterGateId:int، EnterDate:datetime، ExitUserId:int؟، ExitGateId:int؟، ExitDate:datetime؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- پیشنهاد بخش مستقل: صفحه مستقل VIP (TaradodId, EnterGateId, ExitGateId) پس از تأیید


### جداول مرجع/تنظیمات/تجهیزات/تعرفه/مشتری (طبقه‌بندی بر پایه نام و ستون‌ها؛ تأییدنشده)

**`dbo.Activities`** (TABLE) — 2 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: dbo.ActivityLogs.ActivityId
- ستون‌ها: Id:int، ActivityTitle:nvarchar
- مصرف در داشبورد: همان activity (FK ثبت‌شده) — فعلاً مصرف‌نشده

**`dbo.Advertise`** (TABLE) — 6 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، Title:nvarchar، ImageFile:varbinary، StartDate:datetime، EndDate:datetime، IsActive:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- ریسک: دارای ستون باینری/تصویر؛ در Snapshot و Queryهای تحلیلی انتخاب نشود.

**`dbo.AppPaymentTypes`** (TABLE) — 2 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، Title:nvarchar
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.Car_DayTypes`** (TABLE) — 19 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، CalculationMethod:int، CarTypeID:int، DayTypeID:int، PricingProfileID:int، InputCost:int، InputTime:int، FirstPeriodTime:int؟، FirstPeriodTimeCoefficient:float؟، PeriodSize:int، PeriodPrice:int، PeriodTimeCoefficient:float، BoardingParkInputPriceEnable:bit، BoardingHours:int، NightBoardingParkPrice:int، Night2IncreasePrice:int، Night3IncreasePrice:int، Night4IncreasePrice:int، Night5IncreasePrice:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.CardID`** (TABLE) — 6 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، TagID:varchar، CSN6:varchar، CSN:varchar، APDisable:bit، PlateDisable:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.CarTypes`** (TABLE) — 8 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، Title:nvarchar، Icon:varbinary؟، ShortKey:varchar، Priority:int، Visible:bit، PlateEnable:bit، HasPlate:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- ریسک: دارای ستون باینری/تصویر؛ در Snapshot و Queryهای تحلیلی انتخاب نشود.

**`dbo.CheckTypes`** (TABLE) — 2 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، Title:nvarchar
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.ClientMenus`** (TABLE) — 5 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، Title:nvarchar، MenuCode:varchar، Active:bit، Priority:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.ClientUpdates`** (TABLE) — 5 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، Title:nvarchar، SetupType:int، Version:varchar، CreateDate:datetime
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.Controllers`** (TABLE) — 7 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، ControllerIP:varchar، ControllerPort:int، TypeID:int، IsCheck:bit، GateId:int، IsNedap:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- پیشنهاد بخش مستقل: گیت‌ها/تجهیزات: ستون GateId دارد ولی FK ثبت نشده؛ صفحه مستقل «وضعیت تجهیزات» پس از تأیید رابطه

**`dbo.Customer_Messages`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، CustomerId:int، MessageId:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.CustomerCards`** (TABLE) — 8 ستون · PK: نامشخص · FK خروجی: CustomerGoupID → dbo.CustomerGroups.ID؛ CustomerID → dbo.Customers.ID · FK ورودی: —
- ستون‌ها: ID:int، CustomerID:int، CustomerGoupID:int، TagID:varchar، StartDate:datetime، EndDate:datetime، IsExpire:bit؟، MaxHour:int؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.CustomerGroups`** (TABLE) — 12 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: dbo.CustomerCards.CustomerGoupID
- ستون‌ها: ID:int، Title:nvarchar، Price:int، Color:varchar؟، Icon:varbinary؟، Visible:bit، IsDeleted:bit، CheckPlate:bit، PlateCount:int، DailyCount:int؟، MonthlyCount:int؟، UsageCapacity:int؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- ریسک: دارای ستون باینری/تصویر؛ در Snapshot و Queryهای تحلیلی انتخاب نشود.

**`dbo.CustomerLogStatus`** (TABLE) — 2 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، StatusTitle:nvarchar
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.CustomerMessages`** (TABLE) — 5 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، UserId:int، CreateDate:datetime، Message:nvarchar، Send:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.CustomerPlates`** (TABLE) — 10 ستون · PK: نامشخص · FK خروجی: CustomerID → dbo.Customers.ID · FK ورودی: —
- ستون‌ها: ID:int، CustomerID:int، P1:nvarchar، P2:nvarchar، P3:nvarchar، P4:nvarchar، IsActive:bit، UpdateUserId:int، UpdateDate:datetime، CarDescription:nvarchar؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.Customers`** (TABLE) — 9 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: dbo.CustomerCards.CustomerID؛ dbo.CustomerPlates.CustomerID
- ستون‌ها: ID:int، FullName:nvarchar، Mobile:varchar؟، ProfilePicture:varbinary؟، Active:bit، Isdeleted:bit، RegisterDate:datetime، DailyCount:int؟، MonthlyCount:int؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- ریسک: دارای ستون باینری/تصویر؛ در Snapshot و Queryهای تحلیلی انتخاب نشود.

**`dbo.Days`** (TABLE) — 4 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، Title:nvarchar، DayNumber:int، DayTypeID:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.DayTypes`** (TABLE) — 2 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، Title:nvarchar
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.DBVersion`** (TABLE) — 2 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، Version:varchar
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.DiscountCategorys`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، Title:nvarchar، Visible:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.DiscountCodes`** (TABLE) — 9 ستون · PK: نامشخص · FK خروجی: DiscountID → dbo.Discounts.ID · FK ورودی: —
- ستون‌ها: ID:bigint، DiscountID:int، Code:varchar، Active:bit، Count:int، UsedCount:int، DailyCount:int؟، MaxCount:int؟، OrderId:int؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.Discounts`** (TABLE) — 6 ستون · PK: نامشخص · FK خروجی: DiscountTypeID → dbo.DiscountTypes.ID · FK ورودی: dbo.DiscountCodes.DiscountID
- ستون‌ها: ID:int، DiscountTypeID:int، CreateDate:datetime، ExpireDate:datetime، StartDate:datetime، DiscountCategoryID:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.DiscountTypes`** (TABLE) — 7 ستون · PK: نامشخص · FK خروجی: PricingProfileID → dbo.PricingProfiles.ID · FK ورودی: dbo.Discounts.DiscountTypeID
- ستون‌ها: ID:int، Title:nvarchar، Amount:int، Minutes:int، Observeble:bit، DiscountPrice:int؟، PricingProfileID:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.ExtendDays`** (TABLE) — 8 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، GroupID:int، Title:nvarchar، Days:int، Hours:int؟، Coffiecent:int، DiscountPercent:int، Visible:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.FailurePayReasons`** (TABLE) — 5 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، ReasonDescription:nvarchar؟، Priority:int، IsActive:bit، DynamicPrice:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.FreeTime`** (TABLE) — 4 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، InputGateID:int، OutputGateID:int، Minute:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- پیشنهاد بخش مستقل: تحلیل فرجه‌ی رایگان به‌تفکیک گیت ورود/خروج (InputGateID/OutputGateID) پس از تأیید معنای Minute

**`dbo.FreeURL`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: FreeURLID:int، Controller:nvarchar؟، Action:nvarchar؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.Gate_CarTypes`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، GateId:int، CarTypeId:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- پیشنهاد بخش مستقل: صفحه مستقل پیکربندی گیت (GateId/CarTypeId) پس از تأیید FK

**`dbo.Gate_CustomerGroups`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، GateId:int، CustomerGroupId:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- پیشنهاد بخش مستقل: همان

**`dbo.Gates`** (TABLE) — 41 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، Title:nvarchar؟، DeviceIDs:varchar؟، ComputerName:nvarchar؟، ComputerIP:nvarchar؟، GateTypeID:int، CameraIP:nvarchar؟، CameraSnapshotUrl:nvarchar؟، RelayBoardTypeId:int، ControllerIP:varchar؟، ControllerPort:int؟، RelayNumber:int، CameraUser:nvarchar؟، CameraPass:nvarchar؟، PCPOSEnable:bit، PosDeviceId:int، LoopEnable:bit، LoopPort:int، ButtonWidth:int، ButtonLenght:int، Culomns:int، FontSize:int، IconWidth:int، IconLenght:int، RoadblockEnable:bit، AutoPrint:bit، Kiosk:bit، ANPRComputerPort:int، ANPREnable:bit، VerificationComputerPort:int، ReaderEnable:bit، ReaderTypeId:int، SerialPort:varchar؟، BarrierMonitoringEnable:bit، BarrierPortNumber:int، RecieptPortNumber:int، LoopPortNumber:int، LoopInputNumber:int، BarrierLogInputNumber:int، BarrierOutputNumber:int، TicketInputNumber:int
- مصرف در داشبورد: جدید: مرجع نام و فهرست گیت‌ها (فیلتر گیت خروج، نمودارها، جدول‌ها، ANPR/راهبند) — ضروری برای نام‌گذاری گیت
- ریسک/امنیت: ستون‌های `CameraUser`, `CameraPass`, `ControllerIP` وجود دارد؛ عمداً هرگز Select/Snapshot نمی‌شوند. فقط ID, Title, GateTypeID و چند فلگ فعال‌سازی خوانده می‌شود.
- ریسک: دارای ستون باینری/تصویر؛ در Snapshot و Queryهای تحلیلی انتخاب نشود.

**`dbo.GateTypes`** (TABLE) — 2 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، Title:nvarchar
- مصرف در داشبورد: جدید: عنوان نوع گیت در جدول «گیت‌ها و کیفیت داده» (بدون تفسیر ورود/خروج) — اختیاری (نمایشی)

**`dbo.Group_Days`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، CustomerGroupID:int، DayID:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.Group_Gates`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، CustomerGroupID:int، GateID:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- پیشنهاد بخش مستقل: همان

**`dbo.HoliDays`** (TABLE) — 4 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، Date:date، IsEnable:bit، DayTypeID:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.Locations`** (TABLE) — 2 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، LocationName:nvarchar
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.Messages`** (TABLE) — 6 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، Message:nvarchar، Priority:int؟، MessageColor:varchar، IsActive:bit، GateTypeID:int؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.ModbusMappings`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، Number:int، String:varchar
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.OutPutFreeTime`** (TABLE) — 4 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، Output1GateID:int، Output2GateID:int، FreeTime:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- پیشنهاد بخش مستقل: همان (Output1GateID/Output2GateID)

**`dbo.PartitionTimes`** (TABLE) — 7 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، Title:nvarchar، StartTime:time، EndTime:time، Price:int، CarTypeID:int، PricingProfileID:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.PaymentMethods`** (TABLE) — 9 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، Title:nvarchar، ShortKey:nvarchar؟، Icon:varbinary؟، IconURL:nvarchar؟، Priority:int؟، Visible:bit؟، ExitDeadline:int، DeviceIDs:nvarchar
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- ریسک: دارای ستون باینری/تصویر؛ در Snapshot و Queryهای تحلیلی انتخاب نشود.

**`dbo.PcPosCompanies`** (TABLE) — 2 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: dbo.PosDevices.PcPosCompanyId
- ستون‌ها: ID:int، Company:nvarchar
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.PlateMappings`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Number:int، Letter:nvarchar، Description:nvarchar؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.PosDevices`** (TABLE) — 8 ستون · PK: نامشخص · FK خروجی: PcPosCompanyId → dbo.PcPosCompanies.ID · FK ورودی: —
- ستون‌ها: Id:int، Title:nvarchar، PcPosCompanyId:int، IP:varchar، Port:int، SerialNumber:varchar؟، AcceptorId:varchar؟، TerminalId:varchar؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.PricingMethods`** (TABLE) — 15 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، PricingProfileID:int، CarTypeID:int، DayTypeID:int، CheckType:int، MinCount:int، MaxCount:int، CheckDays:int، PlateCodes:varchar؟، OperationType:int، TimeOperationType:int، ParkTime:int، StartTime:varchar؟، EndTime:varchar؟، Priority:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.PricingProfiles`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: dbo.DiscountTypes.PricingProfileID
- ستون‌ها: ID:int، ProfileTitle:nvarchar، IsDefault:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.PublishPartitionTimes`** (TABLE) — 6 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، StartDateTime:datetime، EndDateTime:datetime، PartitionTimeID:int، DayTypeID:int، CarTypeID:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.ReaderTypes`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، Title:nvarchar، ReadType:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.RelayBoardTypes`** (TABLE) — 4 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، Title:nvarchar، Username:nvarchar؟، Password:nvarchar؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.SerialBoards`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، PortNumber:int، TextValue:varchar
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.Settings`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، SettingKey:nvarchar، SettingValue:nvarchar
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.SharedCameraGates`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، ParentGateId:int، SecondaryGateId:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.SpecialCodeReasons`** (TABLE) — 4 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، ReasonDescription:nvarchar؟، Priority:int، IsActive:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)


### جداول مالی

**`dbo.ApiDiscountCodes`** (TABLE) — 5 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، ApiLogID:bigint، DiscountCode:nvarchar، Price:int، Time:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.ApiLogs`** (TABLE) — 16 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، DeviceID:int، TagID:nvarchar؟، TotalPrice:int، FinalPrice:int، Iscomplete:bit، PaymentStatus:varchar؟، CreateDate:datetime؟، CompleteDate:datetime؟، AppUserID:int، PaymentTypeID:int، ReserveDate:datetime؟، ReserveHours:int؟، ReserveCode:varchar؟، ReservePrice:int، IsUsed:bit؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.CustomerCardLogs`** (TABLE) — 12 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، CustomerID:int، Price:int، CreateDate:datetime، EndDate:datetime، IsDeposit:bit، UserID:int، TagID:varchar، GroupID:int، IsRefund:bit، TaradodID:bigint، SendToServer:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.DiscountAlertLogs`** (TABLE) — 5 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، DiscountTypeID:int، DiscountCode:varchar، UsedPercent:int، CreateDate:datetime
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.DiscountLogs`** (TABLE) — 5 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، DiscountCode:varchar، TaradodID:bigint، InsertDate:datetime، SendToServer:bit
- مصرف در داشبورد: بخش کد تخفیف (discount_logs) و پشتیبان view — اختیاری

**`dbo.DiscountLogTemp`** (TABLE) — 4 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، Code:varchar، TaradodID:bigint، CodeType:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.PaymentRequests`** (TABLE) — 7 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، RequsetDateTime:datetime، PaymentMethodID:int؟، UserID:int، Price:money، Description:nvarchar؟، Status:bigint
- مصرف در داشبورد: تعریف‌شده در db.py (pay_requests) ولی در app.py فراخوانی نمی‌شود — فعلاً مصرف‌نشده

**`dbo.PosDetails`** (TABLE) — 5 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، InsertDateTime:datetime، ResponseCode:nvarchar؟، Descriptin:nvarchar؟، Amount:nvarchar؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.PosTransactions`** (TABLE) — 8 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، UserId:int، MerchantName:nvarchar؟، PosTerminalNo:bigint، Amount:int، TransactionDateTime:datetime، Pan:varchar، Status:nvarchar
- مصرف در داشبورد: pos_tx (با پشتیبان از Taradods) — اختیاری

**`dbo.RealIncomes`** (TABLE) — 14 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، CreateDateTime:datetime، DateTime:datetime، Pecuniary:int، Pos:int، CreatorUserID:int، UpdateUserID:int؟، Description:nvarchar؟، IsEdited:int؟، UserDescription:nvarchar؟، SupervisorID:int، SendToServer:bit، SetPosTransaction:bit، Closed:bit
- مصرف در داشبورد: real_income — اختیاری

**`dbo.SpecialCodes`** (TABLE) — 10 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، Code:varchar، StartDate:datetime، ExpireDate:datetime، Count:int؟، Price:int، UsedCount:int، Title:nvarchar؟، DynamicPrice:bit، VATEnable:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`org.CustomerOrders`** (TABLE) — 10 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، OrderKey:varchar، CreateDate:datetime، UserId:int، CustomerCount:int، ExtendDayId:int، UnitPrice:int، UnitVATPrice:int، Price:int، OrderStateId:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`org.DiscountOrders`** (TABLE) — 10 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، OrderKey:varchar، CreateDate:datetime، UserId:int، DiscountCodeId:bigint، DiscountCount:int، UnitPrice:int، UnitVATPrice:int، Price:int، OrderStateId:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`org.Factors`** (TABLE) — 10 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، OrderType:int، OrderId:int، Fullname:nvarchar، EconomicCode:varchar؟، Address:nvarchar، RegisterNumber:varchar؟، PostalCode:varchar؟، PhoneNumbers:varchar؟، NationalId:varchar؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`org.Order_Customers`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، OrderId:int، CustomerId:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`org.OrderStates`** (TABLE) — 2 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، Title:nvarchar
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`org.PaymentDetails`** (TABLE) — 6 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، InsertDateTime:datetime، OrderKey:varchar، RefId:bigint؟، Authority:varchar؟، Amount:int؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)


### جداول لاگ

**`dbo.ActivityLogs`** (TABLE) — 6 ستون · PK: نامشخص · FK خروجی: ActivityId → dbo.Activities.Id · FK ورودی: —
- ستون‌ها: Id:bigint، UserId:int، CreateDateTime:datetime، ActivityId:int، EventId:bigint؟، Description:nvarchar؟
- مصرف در داشبورد: تعریف‌شده در db.py (activity، JOIN با Activities) ولی در app.py فراخوانی نمی‌شود — فعلاً مصرف‌نشده

**`dbo.BarrierLogs`** (TABLE) — 4 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، GateId:int، LogDateTime:datetime، Message:varchar؟
- مصرف در داشبورد: صفحه فنی: راهبند (barrier) — اختیاری (صفحه فنی)

**`dbo.ClientUpdateRequests`** (TABLE) — 5 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، ClientIp:varchar؟، ClientAppVersion:varchar؟، ClientServiceVersion:varchar؟، RequestDatetime:datetime
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.CustomerLogs`** (TABLE) — 11 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، UserID:int، TagID:varchar؟، Datetime:datetime، Type:int؟، IsValid:bit؟، IsCheck:bit؟، GateId:int؟، ANPRPlateId:bigint، PlateStatus:int؟، StatusId:int؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- پیشنهاد بخش مستقل: بخش مشتریان/اشتراک (GateId, TagID) پس از تأیید

**`dbo.EventLogs`** (TABLE) — 6 ستون · PK: نامشخص · FK خروجی: UserSessionID → dbo.UserSessions.ID · FK ورودی: —
- ستون‌ها: ID:bigint، UserSessionID:bigint، ActionFullname:nvarchar، SPName:nvarchar؟، Description:nvarchar؟، ServerTime:datetime
- مصرف در داشبورد: events (ممیزی سیستم) — JOIN با UserSessions (FK ثبت‌شده) — اختیاری

**`dbo.UserSessions`** (TABLE) — 10 ستون · PK: نامشخص · FK خروجی: UserLogoffReasonID → dbo.UserLogoffReasons.ID · FK ورودی: dbo.EventLogs.UserSessionID
- ستون‌ها: ID:bigint، UserLogoffReasonID:int؟، UserGuid:uniqueidentifier، UserName:nvarchar، ClientIP:nvarchar، ClientPort:int، ServerLoginTime:datetime، ServerLogoffTime:datetime؟، LastActivity:datetime؟، AppType:nvarchar؟
- مصرف در داشبورد: sessions/events — JOIN با UserLogoffReasons (FK ثبت‌شده) — اختیاری

**`dbo.VerificationLogs`** (TABLE) — 4 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، CSN:varchar، LogType:int، IsValid:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)


### جداول کاربران/دسترسی

**`dbo.UserLogoffReasons`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: dbo.UserSessions.UserLogoffReasonID
- ستون‌ها: ID:int، Title:nvarchar، Description:nvarchar؟
- مصرف در داشبورد: sessions (LEFT JOIN) — اختیاری

**`org.User_CustomerGroups`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، UserId:int، CustomerGroupId:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`org.User_DiscountCodes`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Id:int، UserId:int، DiscountCodeId:bigint
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`rbac.Menus`** (TABLE) — 10 ستون · PK: نامشخص · FK خروجی: ParentID → rbac.Menus.ID · FK ورودی: rbac.Menus.ParentID؛ rbac.Role_Menus.MenuID
- ستون‌ها: ID:int، Title:nvarchar، Area:nvarchar؟، Controller:nvarchar، Action:nvarchar؟، ParentID:int؟، Visible:bit، Priority:int، IconClass:varchar؟، IsAjax:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- ریسک: دارای ستون باینری/تصویر؛ در Snapshot و Queryهای تحلیلی انتخاب نشود.

**`rbac.Permission_Roles`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: PermissionID → rbac.Permissions.ID؛ RoleID → rbac.Roles.ID · FK ورودی: —
- ستون‌ها: ID:int، RoleID:int، PermissionID:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`rbac.Permissions`** (TABLE) — 7 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: rbac.Permission_Roles.PermissionID
- ستون‌ها: ID:int، MenuId:int، Title:nvarchar؟، Area:varchar؟، Controller:varchar؟، Action:varchar؟، IsActive:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`rbac.Role_Menus`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: MenuID → rbac.Menus.ID؛ RoleID → rbac.Roles.ID · FK ورودی: —
- ستون‌ها: ID:int، RoleID:int، MenuID:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`rbac.Roles`** (TABLE) — 4 ستون · PK: نامشخص · FK خروجی: ParentID → rbac.Roles.ID · FK ورودی: rbac.Permission_Roles.RoleID؛ rbac.Role_Menus.RoleID؛ rbac.Roles.ParentID؛ rbac.User_Roles.RoleID
- ستون‌ها: ID:int، Title:nvarchar؟، ParentID:int؟، IsAdmin:bit
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`rbac.User_Roles`** (TABLE) — 3 ستون · PK: نامشخص · FK خروجی: RoleID → rbac.Roles.ID؛ UserID → rbac.Users.ID · FK ورودی: —
- ستون‌ها: ID:int، UserID:int، RoleID:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`rbac.Users`** (TABLE) — 14 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: rbac.User_Roles.UserID
- ستون‌ها: ID:int، UserName:nvarchar، Password:nvarchar، FirstName:nvarchar، LastName:nvarchar، StartDate:datetime، ExpireDate:datetime، IsActive:bit، ProfilePicture:image؟، ReportRate:float، GuestLimitMinute:int، GuestLimitCountDay:int؟، GuestLimitCountMonth:int؟، PosTerminalNo:bigint؟
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
- ریسک: دارای ستون باینری/تصویر؛ در Snapshot و Queryهای تحلیلی انتخاب نشود.
- ریسک: ستون `Password` وجود دارد؛ هرگز خوانده/ذخیره نمی‌شود.


### Viewها

**`dbo.View_Customer_Cards_Detials`** (VIEW) — 12 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: FullName:nvarchar، Mobile:varchar؟، Active:bit، TagID:varchar، StartDate:datetime، EndDate:datetime، Title:nvarchar، P1:nvarchar، P2:nvarchar، P3:nvarchar، P4:nvarchar، CarDescription:nvarchar؟
- مصرف در داشبورد: cards (کارت‌های مشتری/اشتراکی) — اختیاری

**`dbo.View_FinancialReports`** (VIEW) — 4 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: Date_Farsi:nvarchar؟، TaradodCount:int؟، TotalFinalPrice:int؟، TotalWebIncome:int؟
- مصرف در داشبورد: تعریف‌شده در db.py (financial) ولی در app.py فراخوانی نمی‌شود — اختیاری/فعلاً مصرف‌نشده

**`dbo.View_Taradods_PBI`** (VIEW) — 19 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: id:bigint، GuestsTaradodID:bigint؟، DiscountLogsTaradodID:bigint؟، CustomerTaradodID:bigint؟، ExitUser:nvarchar، CarTypeID:int، CardID:varchar، EnrtyDateTime:datetime، InputGateID:int، ExitDateTime:datetime؟، OutputGateID:int، TotalPrice:money؟، FinalPrice:int؟، Cash:int؟، Pos:int؟، VATPrice:int؟، PlateID:int؟، OutputGate:nvarchar؟، InputGate:nvarchar؟
- مصرف در داشبورد: ردیف‌های View (view)، تجمیع تخفیف/معافیت (fin_view)، پشتیبان discount_logs/guests — ضروری (منبع تحلیل معافیت/مهمان/تخفیف)

**`dbo.ViewCarPrice`** (VIEW) — 22 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، PricingProfileID:int، CarTypeID:int، DayTypeID:int، InputCost:int، InputTime:int، PeriodSize:int، PeriodPrice:int، FirstPeriodTime:int؟، FirstPeriodTimeCoefficient:float؟، PeriodTimeCoefficient:float، CarTypeTitle:nvarchar، DayTypeTitle:nvarchar، ProfileTitle:nvarchar، CalculationMethod:int، NightBoardingParkPrice:int، BoardingParkInputPriceEnable:bit، BoardingHours:int، Night2IncreasePrice:int، Night3IncreasePrice:int، Night4IncreasePrice:int، Night5IncreasePrice:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.ViewDiscountCodes`** (VIEW) — 13 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، DiscountTypeID:int، DiscountCategoryID:int، DiscountType:nvarchar، CategoryType:nvarchar، Amount:int، Code:varchar؟، Active:bit، StartDate:datetime، ExpireDate:datetime، UsedCount:int، DailyCount:int؟، MaxCount:int؟
- مصرف در داشبورد: فهرست کدهای تخفیف (discount_codes) — اختیاری

**`dbo.ViewPartitionTimes`** (VIEW) — 9 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، Title:nvarchar، StartTime:time، EndTime:time، Price:int، CarType:nvarchar، CarTypeID:int، ProfileTitle:nvarchar، ProfileID:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.ViewPricingMethod`** (VIEW) — 19 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:int، ProfileTitle:nvarchar، CheckTypeTitle:nvarchar، ProfileID:int، CarTypeID:int، DayTypeID:int، MinCount:int، MaxCount:int، CheckDays:int، CarTypeTitle:nvarchar، DayTypeTitle:nvarchar، PlateCodes:varchar؟، OperationType:int، TimeOperationType:int، CheckType:int، ParkTime:int، StartTime:varchar؟، EndTime:varchar؟، Priority:int
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)

**`dbo.ViewPublishPartitionTimes`** (VIEW) — 10 ستون · PK: نامشخص · FK خروجی: — · FK ورودی: —
- ستون‌ها: ID:bigint، StartDateTime:datetime، EndDateTime:datetime، PartitionTimeID:int، DayTypeID:int، CarTypeID:int، PartitionTimeTitle:nvarchar، DayTypeTitle:nvarchar، CarTypeTitle:nvarchar، ProfileTitle:nvarchar
- مصرف در داشبورد: فقط بررسی شد؛ در نسخه‌ی فعلی مصرف نمی‌شود (نیاز به داده/تأیید بیشتر)
