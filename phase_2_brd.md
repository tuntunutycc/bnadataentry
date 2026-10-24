# Business Requirements Document (BRD) - Phase 2
# စီးပွားရေး လိုအပ်ချက်စာတမ်း (BRD) - အဆင့် ၂

**Project Name:** Supermarket-Specific Calculation & UI Enhancements
**စီမံကိန်းအမည် -** စူပါမားကတ်အလိုက် တွက်ချက်မှုနှင့် မျက်နှာပြင် တိုးတက်မှုများ

**Target Audience:** Development Team, AI Assistant (Cursor)
**ရည်ရွယ်သူများ -** ဖွံ့ဖြိုးရေးအဖွဲ့၊ AI လက်ထောက် (Cursor)

**Document Status:** Ready for Phase 2 Implementation
**စာတမ်းအခြေအနေ -** အဆင့် ၂ ကို အကောင်အထည်ဖော်ရန် အသင့်ဖြစ်ပြီ

## 1. Project Objectives
## ၁။ စီမံကိန်း ရည်ရွယ်ချက်

The objective of Phase 2 is to implement distinct financial calculation rules tailored to specific supermarket chains (Capital, Makro, One Stop Mart). This includes handling complex, conditional formulas for Tax, Retro (Back Margin), and Delivery Charges (DC). Additionally, this phase will implement recent UI/UX enhancements requested by the client to improve data entry flexibility and reporting.

အဆင့် ၂ ၏ ရည်ရွယ်ချက်မှာ စူပါမားကတ်ဆိုင်ခွဲများ (Capital၊ Makro၊ One Stop Mart) အတွက် သီးခြား ငွေကြေးတွက်ချက်မှု စည်းမျဉ်းများကို အကောင်အထည်ဖော်ရန် ဖြစ်သည်။ ၎င်းတွင် အခွန် (Tax)၊ Retro (Back Margin) နှင့် ပို့ဆောင်ခ (Delivery Charges, DC) အတွက် ရှုပ်ထွေးပြီး အခြေအနေပေါ်မူတည်သော ဖော်မြူလာများ ပါဝင်သည်။ ထို့အပြင် ဒေတာထည့်သွင်းမှု ပိုလွယ်ကူစေရန်နှင့် အစီရင်ခံစာ ပိုကောင်းစေရန် ဖောက်သည် တောင်းဆိုထားသော မျက်နှာပြင် တိုးတက်မှုများကို ဤအဆင့်တွင် ထည့်သွင်းမည်။

## 2. Client Feedback & New Requirements (as of 08.10.2026)
## ၂။ ဖောက်သည် အကြံပြုချက်နှင့် လိုအပ်ချက်အသစ်များ (၀၈.၁၀.၂၀၂၆ အထိ)

*   **REQ-01 (Tax Included Checkbox):** Since items for Capital already include a 5% tax, a new function/checkbox labeled `["Tax included in the invoice amount"]` must be added to the UI when creating a new invoice.

    **REQ-01 (အခွန်ပါပြီးသား အမှန်ခြစ်) -** Capital ပစ္စည်းဈေးနှုန်းများတွင် အခွန် 5% ပါပြီးသား ဖြစ်သောကြောင့် ငွေတောင်းခံလွှာအသစ် ဖန်တီးသည့်အခါ `["Tax included in the invoice amount"]` ဟု အမည်တပ်ထားသော လုပ်ဆောင်ချက်/အမှန်ခြစ်ကို မျက်နှာပြင်တွင် ထည့်ရမည်။

*   **REQ-02 (Adjustable Retro Parameter):** For Capital (Hyper), the Retro percentage (previously fixed at 3%) is variable. The system must provide an adjustable UI parameter (dropdown/input) for the user to select or input the Retro (%), similar to how Delivery Charges are selected.

    **REQ-02 (ပြောင်းလဲနိုင်သော Retro) -** Capital (Hyper) အတွက် Retro ရာခိုင်နှုန်းသည် ယခင်က 3% ဖြင့် ပုံသေထားခဲ့ပြီး ယခု ပြောင်းလဲနိုင်သည်။ ပို့ဆောင်ခကို ရွေးချယ်သကဲ့သို့ အသုံးပြုသူ Retro (%) ကို ရွေးနိုင်သော သို့မဟုတ် ရိုက်ထည့်နိုင်သော ချိန်ညှိနိုင်သည့် အကွက် (dropdown/input) ပေးရမည်။

*   **REQ-03 (Category Summary Feature):** Develop a reporting/summary feature that displays the total Quantity and Total Amount grouped by Category.

    **REQ-03 (အမျိုးအစား အနှစ်ချုပ်) -** အမျိုးအစားအလိုက် စုစုပေါင်း အရေအတွက်နှင့် စုစုပေါင်း ပမာဏကို ပြသည့် အစီရင်ခံစာ/အနှစ်ချုပ် လုပ်ဆောင်ချက် တစ်ခု ပြုလုပ်ရမည်။

*   **REQ-04 (Add New Item Function):** Since a single invoice can contain multiple product types, an "Add New Item" function must be integrated into the category selection UI to allow dynamic appending of items to an ongoing invoice.

    **REQ-04 (ပစ္စည်းအသစ် ထည့်ရန်) -** ငွေတောင်းခံလွှာတစ်စောင်တွင် ပစ္စည်းအမျိုးအစားများစွာ ပါနိုင်သောကြောင့် အမျိုးအစားရွေးချယ်သည့် မျက်နှာပြင်တွင် "Add New Item" လုပ်ဆောင်ချက် ထည့်ရမည်။ ဆက်လက်ရေးနေသော ငွေတောင်းခံလွှာထဲသို့ ပစ္စည်းများကို ထပ်ထည့်နိုင်ရမည်။

## 3. Supermarket-Specific Calculation Rules
## ၃။ စူပါမားကတ်အလိုက် တွက်ချက်မှု စည်းမျဉ်းများ

### 3.1. Capital

*   **Condition:** Tax (5%) is inherently included in the item price. Do not add external tax.

    **အခြေအနေ -** အခွန် (5%) သည် ပစ္စည်းဈေးနှုန်းထဲတွင် ပါပြီးသား ဖြစ်သည်။ ပြင်ပအခွန် ထပ်မပေါင်းရ။

*   **Base Formula (Invoice Balance):**
    `Invoice Balance = Total Invoice Amount - (Total Invoice Amount * Retro %)`

    **အခြေခံဖော်မြူလာ (ငွေတောင်းခံလွှာ လက်ကျန်) -**
    `ငွေတောင်းခံလွှာ လက်ကျန် = စုစုပေါင်း ငွေတောင်းခံပမာဏ - (စုစုပေါင်း ငွေတောင်းခံပမာဏ × Retro %)`

*   **Delivery Charges (DC) Calculation:** DC (0%, 4%, or 6%) is deducted at the final payment stage. Because the item value includes tax, the system must extract the pre-tax base value before applying the DC deduction.

    **ပို့ဆောင်ခ (DC) တွက်ချက်မှု -** DC (0%၊ 4% သို့မဟုတ် 6%) ကို နောက်ဆုံး ငွေပေးချေမှုအဆင့်တွင် နုတ်သည်။ ပစ္စည်းတန်ဖိုးတွင် အခွန်ပါပြီးသား ဖြစ်သောကြောင့် DC နုတ်မည့်အချိန်မတိုင်မီ အခွန်မပါ တန်ဖိုးကို ခွဲထုတ်ရမည်။

*   **Formulas for Final Payment:**
    *   *For Tax-Inclusive Items:*
        `Expected Final Payment = Invoice Balance - (((Invoice Balance / 105) * 100) * DC %)`
    *   *For Tax-Exempt Items:*
        `Expected Final Payment = Invoice Balance - (Invoice Balance * DC %)`

    **နောက်ဆုံး ငွေပေးချေမှု ဖော်မြူလာများ -**
    *   *အခွန်ပါ ပစ္စည်းများအတွက် -*
        `မျှော်မှန်း နောက်ဆုံးပေးချေငွေ = ငွေတောင်းခံလွှာ လက်ကျန် - (((ငွေတောင်းခံလွှာ လက်ကျန် / 105) × 100) × DC %)`
    *   *အခွန်ကင်းလွတ် ပစ္စည်းများအတွက် -*
        `မျှော်မှန်း နောက်ဆုံးပေးချေငွေ = ငွေတောင်းခံလွှာ လက်ကျန် - (ငွေတောင်းခံလွှာ လက်ကျန် × DC %)`

### 3.2. Makro

*   **Condition:** Tax (5%) must be added explicitly to the Total Invoice value.

    **အခြေအနေ -** အခွန် (5%) ကို စုစုပေါင်း ငွေတောင်းခံပမာဏပေါ်တွင် သီးသန့် ထပ်ပေါင်းရမည်။

*   **Base Formula (Grand Total):**
    `Grand Total = Total Invoice Amount + (Total Invoice Amount * Tax 5%)`

    **အခြေခံဖော်မြူလာ (စုစုပေါင်း) -**
    `စုစုပေါင်း = စုစုပေါင်း ငွေတောင်းခံပမာဏ + (စုစုပေါင်း ငွေတောင်းခံပမာဏ × အခွန် 5%)`

*   **Retro Calculation:** Retro (typically 5%) is deducted from the Grand Total at the final payment stage.

    **Retro တွက်ချက်မှု -** Retro (ပုံမှန် 5%) ကို နောက်ဆုံး ငွေပေးချေမှုအဆင့်တွင် စုစုပေါင်းမှ နုတ်သည်။

*   **Formulas for Final Payment:**
    *   *For Taxable Items:*
        `Expected Final Payment = Grand Total - (Grand Total * Retro 5%)`
    *   *For Tax-Exempt Items (Grand Total = Total Invoice):*
        `Expected Final Payment = Grand Total - (Grand Total * Retro 5%)`
    *   *For Items with No Tax & No Retro:*
        `Expected Final Payment = Total Invoice Amount`

    **နောက်ဆုံး ငွေပေးချေမှု ဖော်မြူလာများ -**
    *   *အခွန်ပါ ပစ္စည်းများအတွက် -*
        `မျှော်မှန်း နောက်ဆုံးပေးချေငွေ = စုစုပေါင်း - (စုစုပေါင်း × Retro 5%)`
    *   *အခွန်ကင်းလွတ် ပစ္စည်းများအတွက် (စုစုပေါင်း = စုစုပေါင်း ငွေတောင်းခံပမာဏ) -*
        `မျှော်မှန်း နောက်ဆုံးပေးချေငွေ = စုစုပေါင်း - (စုစုပေါင်း × Retro 5%)`
    *   *အခွန်မရှိ၊ Retro မရှိသော ပစ္စည်းများအတွက် -*
        `မျှော်မှန်း နောက်ဆုံးပေးချေငွေ = စုစုပေါင်း ငွေတောင်းခံပမာဏ`

*   **Data Structure Requirement:** Retro deductions may occur for goods already delivered but not yet due. Therefore, the Makro "sales summary & payment" export/report must include `[Received Date]` and `[Retro Cut Off Date]` columns.

    **ဒေတာဖွဲ့စည်းပုံ လိုအပ်ချက် -** ပို့ပြီးသား ဖြစ်သော်လည်း သတ်မှတ်ရက် မကျသေးသော ကုန်ပစ္စည်းများအတွက် Retro နုတ်ခြင်း ဖြစ်နိုင်သည်။ ထို့ကြောင့် Makro "အရောင်းအနှစ်ချုပ်နှင့် ငွေပေးချေမှု" ထုတ်ယူချက်/အစီရင်ခံစာတွင် `[Received Date]` (လက်ခံရရှိသည့်ရက်) နှင့် `[Retro Cut Off Date]` (Retro ဖြတ်တောက်သည့်ရက်) ကော်လံများ ပါရမည်။

### 3.3. One Stop Mart

*   **Condition:** Tax (5%) is inherently included in the item price. Do not add external tax.

    **အခြေအနေ -** အခွန် (5%) သည် ပစ္စည်းဈေးနှုန်းထဲတွင် ပါပြီးသား ဖြစ်သည်။ ပြင်ပအခွန် ထပ်မပေါင်းရ။

*   **Discount Structure:** Total Invoice is subject to a combined deduction of Back Margin (equivalent to Retro) and Delivery Incentive.

    **လျှော့ဈေး ဖွဲ့စည်းပုံ -** စုစုပေါင်း ငွေတောင်းခံပမာဏမှ Back Margin (Retro နှင့် တူညီသည်) နှင့် Delivery Incentive ကို ပေါင်း၍ နုတ်ရမည်။

*   **Formula for Final Payment:**
    `Expected Final Payment = Total Invoice Amount - (Total Invoice Amount * Combined Discount %)`
    *(Where Combined Discount % = Back Margin % + Delivery Incentive %)*

    **နောက်ဆုံး ငွေပေးချေမှု ဖော်မြူလာ -**
    `မျှော်မှန်း နောက်ဆုံးပေးချေငွေ = စုစုပေါင်း ငွေတောင်းခံပမာဏ - (စုစုပေါင်း ငွေတောင်းခံပမာဏ × ပေါင်းလျှော့ဈေး %)`
    *(ပေါင်းလျှော့ဈေး % = Back Margin % + Delivery Incentive %)*

*   **Note:** The combined percentage (e.g., 5% + 5% = 10%) is variable and will not always be exactly 10%. The system must handle these parameters dynamically.

    **မှတ်ချက် -** ပေါင်းရာခိုင်နှုန်း (ဥပမာ 5% + 5% = 10%) သည် ပြောင်းလဲနိုင်ပြီး အမြဲတမ်း 10% အတိအကျ မဟုတ်ပါ။ စနစ်သည် ဤတန်ဖိုးများကို လိုက်လျောညီထွေ ကိုင်တွယ်ရမည်။

## 4. System Constraints & Notes
## ၄။ စနစ် ကန့်သတ်ချက်များနှင့် မှတ်ချက်များ

*   **Mathematical Precision:** Since these calculations involve currency, percentages, and division (e.g., `/ 105`), the backend logic must handle floating-point arithmetic safely to avoid decimal precision errors.

    **သင်္ချာ တိကျမှု -** ဤတွက်ချက်မှုများတွင် ငွေကြေး၊ ရာခိုင်နှုန်းနှင့် စားခြင်း (ဥပမာ `/ 105`) ပါဝင်သောကြောင့် ဒဿမနေရာ လွဲမှားမှု မဖြစ်စေရန် နောက်ခံတွက်ချက်မှုသည် ဒဿမကိန်းကို လုံခြုံစွာ ကိုင်တွယ်ရမည်။

*   **Dynamic UI Rendering:** The invoice creation UI must conditionally render fields based on the selected Supermarket profile (e.g., showing the Retro dropdown only for Capital/Makro, handling combined discounts for One Stop Mart).

    **မျက်နှာပြင်ကို အခြေအနေအလိုက် ပြသခြင်း -** ရွေးချယ်ထားသော စူပါမားကတ်အလိုက် ငွေတောင်းခံလွှာ ဖန်တီးသည့် မျက်နှာပြင်တွင် အကွက်များကို ပြရမည်။ ဥပမာ Retro dropdown ကို Capital နှင့် Makro အတွက်သာ ပြခြင်း၊ One Stop Mart အတွက် ပေါင်းလျှော့ဈေးများကို ကိုင်တွယ်ခြင်း။

*   **Payment Stage Execution:** Note that specific deductions (DC for Capital, Retro for Makro) are executed at the *payment stage*, meaning the system must store the gross values upon invoice creation and calculate these specific deductions when generating the payment slips or summaries.

    **ငွေပေးချေမှုအဆင့်တွင် တွက်ခြင်း -** သတ်မှတ်ထားသော နုတ်ငွေများ (Capital အတွက် DC၊ Makro အတွက် Retro) ကို ငွေပေးချေမှုအဆင့်တွင် တွက်ရမည်။ ငွေတောင်းခံလွှာ ဖန်တီးချိန်တွင် မူလပမာဏများကို သိမ်းထားပြီး ငွေပေးချေလွှာ သို့မဟုတ် အနှစ်ချုပ် ထုတ်သည့်အခါ ဤနုတ်ငွေများကို တွက်ရမည်။
