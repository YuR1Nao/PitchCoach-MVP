# ============================================================
# PitchCoach 企業中控台 v3
# ============================================================

import streamlit as st
import streamlit.components.v1 as components
import anthropic
import re
import json
import random
import os
import bcrypt
import secrets
import string
from dotenv import load_dotenv
load_dotenv()

from config import *
from database import (get_supabase, load_settings, get_or_create_company, save_settings,
                      save_training_set_file, update_training_set_question, toggle_question_included,
                      select_next_questions,
                      get_all_training_sets, delete_training_set, toggle_training_set_active,
                      get_company_by_access_code, get_employee_by_username, get_company_name_by_id,
                      set_company_credentials, create_employee_account, list_all_companies)
from ai_services import (
    clean_text_for_tts, speech_to_text,
    get_coach_hint, get_evaluation_report, extract_text_from_bytes,
    analyze_with_claude, generate_questions_json, parse_analysis_and_questions,
    extract_section, get_customer_response
)

# ──────────────────────────────────────────────
# 網頁基本設定
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="PitchCoach 企業中控台",
    page_icon="🎯",
    layout="wide",
)

# ── 登入 Gate（依網址參數 ?view= 決定顯示哪個登入畫面）─────
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
    st.session_state["role"] = None
    st.session_state["current_company"] = ""
    st.session_state["company_id"] = ""

# custom_questions（主管自訂考題）在多個地方會被讀取/寫入（企業中控台、
# 員工實戰沙盒都有用到），原本的初始化只寫在員工端那個區塊裡，如果主管
# 在還沒進過員工分頁的全新session就先按「新增」，會因為這個key還不存在
# 而觸發 KeyError。搬到這裡確保腳本一開始就一定會初始化好，不管哪個分頁
# 先執行到都不會出錯。
if "custom_questions" not in st.session_state:
    st.session_state["custom_questions"] = []

if not st.session_state["authenticated"]:
    _view = st.query_params.get("view", "")

    if _view == "manager":
        st.markdown(
            '<div style="font-size:2rem; font-weight:800; color:#e8e8e6; margin-bottom:0.2rem;">'
            'Pitch<span style="font-weight:400; color:#a8a8a6;">Coach</span></div>'
            '<div style="font-size:1.1rem; color:#c4c4c2; margin-bottom:1.2rem;">企業主管登入</div>',
            unsafe_allow_html=True
        )
        access_code_input = st.text_input("公司帳號")
        pwd = st.text_input("管理員密碼", type="password")
        if st.button("登入"):
            company = get_company_by_access_code(access_code_input.strip())
            if not company or not company.get("admin_password_hash"):
                st.error("公司帳號不存在，請向系統管理者確認")
            elif not bcrypt.checkpw(pwd.encode(), company["admin_password_hash"].encode()):
                st.error("密碼錯誤")
            else:
                st.session_state["authenticated"] = True
                st.session_state["role"] = "admin"
                st.session_state["current_company"] = company["name"]
                st.session_state["company_id"] = company["id"]
                st.rerun()

    elif _view == "employee":
        st.markdown(
            '<div style="font-size:2rem; font-weight:800; color:#e8e8e6; margin-bottom:0.2rem;">'
            'Pitch<span style="font-weight:400; color:#a8a8a6;">Coach</span></div>'
            '<div style="font-size:1.1rem; color:#c4c4c2; margin-bottom:1.2rem;">員工訓練登入</div>',
            unsafe_allow_html=True
        )
        username_input = st.text_input("帳號")
        pwd = st.text_input("密碼", type="password")
        if st.button("進入訓練"):
            employee = get_employee_by_username(username_input.strip())
            if not employee:
                st.error("帳號不存在，請向主管確認")
            elif not bcrypt.checkpw(pwd.encode(), employee["password_hash"].encode()):
                st.error("密碼錯誤")
            else:
                st.session_state["authenticated"] = True
                st.session_state["role"] = "employee"
                st.session_state["company_id"] = employee["company_id"]
                st.session_state["current_company"] = get_company_name_by_id(employee["company_id"])
                st.session_state["employee_name"] = employee["employee_name"]
                st.rerun()

    elif _view == "platform":
        st.title("🛠️ PitchCoach 平台管理")
        st.caption("僅供系統管理者新增公司與員工帳號使用")
        super_pwd = st.text_input("系統管理密碼", type="password")
        if super_pwd and SUPER_ADMIN_PASSWORD and super_pwd == SUPER_ADMIN_PASSWORD:
            st.success("✅ 已驗證，以下帳密只會顯示這一次，請立即記下並交給對方")

            st.markdown("#### 🏢 設定 / 更新公司登入帳密")
            existing = list_all_companies()
            existing_names = [c["name"] for c in existing]
            company_name_input = st.text_input("公司名稱（若已存在會沿用同一間，不會重複建立）")
            if st.button("產生 / 重設此公司的管理員帳密"):
                if company_name_input.strip():
                    cid = get_or_create_company(company_name_input.strip())
                    new_access_code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
                    new_password    = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
                    pwd_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
                    if set_company_credentials(cid, new_access_code, pwd_hash):
                        st.success(f"公司帳號：`{new_access_code}`　管理員密碼：`{new_password}`")
                        st.warning("這組密碼只會顯示這一次，請立刻複製給對方。")
                else:
                    st.error("請輸入公司名稱")

            st.markdown("#### 👤 新增員工帳號")
            if existing:
                target_company = st.selectbox("這位員工屬於哪間公司", existing_names)
                emp_name_input  = st.text_input("員工姓名")
                if st.button("產生此員工的帳密"):
                    if emp_name_input.strip():
                        cid = next(c["id"] for c in existing if c["name"] == target_company)
                        new_username = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
                        new_password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
                        pwd_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
                        if create_employee_account(cid, emp_name_input.strip(), new_username, pwd_hash):
                            st.success(f"員工帳號：`{new_username}`　密碼：`{new_password}`")
                            st.warning("這組密碼只會顯示這一次，請立刻複製給對方。")
                    else:
                        st.error("請輸入員工姓名")
            else:
                st.info("目前還沒有任何公司，請先在上面新增一間公司。")
        elif super_pwd:
            st.error("密碼錯誤")

    else:
        st.markdown(
            '<div style="font-size:2rem; font-weight:800; color:#e8e8e6; margin-bottom:0.8rem;">'
            'Pitch<span style="font-weight:400; color:#a8a8a6;">Coach</span></div>',
            unsafe_allow_html=True
        )
        st.info("請使用您收到的登入連結進入系統。")

    st.stop()

# 每個瀏覽器 Session 只執行一次：自動讀取上次儲存的主管設定
if "settings_loaded" not in st.session_state:
    load_settings()
    st.session_state["settings_loaded"] = True

# ──────────────────────────────────────────────
# 全域 CSS
# ──────────────────────────────────────────────
st.markdown("""
<style>
    .main { background-color: #121212; }

    /* 品牌標題 */
    .hero-title { font-size:2.2rem; font-weight:800; color:#e8e8e6; margin-bottom:0.2rem; }
    .hero-subtitle { font-size:1rem; color:#a8a8a6; margin-bottom:1.5rem; }

    /* 任務簡報橫幅 — 深黑底 + 金色點綴，僅用於「重要指示」情境 */
    .mission-banner {
        background: linear-gradient(135deg, #1a1a1a 0%, #2c2c2c 100%);
        color: white;
        border-radius: 12px;
        padding: 1.4rem 2rem;
        margin-bottom: 1.5rem;
    }
    .mission-banner h3 { color: #c9a227; margin-bottom: 0.4rem; font-size: 1.1rem; }
    .mission-banner p  { margin: 0; font-size: 0.95rem; line-height: 1.7; }

    /* 鎖定提示 */
    .locked-box {
        background: #242424;
        border: 2px dashed #4a4a48;
        border-radius: 12px;
        text-align: center;
        padding: 4rem 2rem;
        color: #8a8a88;
    }

    /* 進度條外框 */
    .progress-label { font-size:0.85rem; color:#a8a8a6; margin-bottom:0.3rem; }

    /* 考題卡片 */
    .question-card {
        background:#242424; border-radius:10px; padding:1rem 1.2rem;
        border:1.5px solid #3a3a38; margin-bottom:0.8rem;
    }
    .question-card-selected {
        background:rgba(201,162,39,0.12); border-radius:10px; padding:1rem 1.2rem;
        border:1.5px solid #c9a227; margin-bottom:0.8rem;
    }
    .selection-hint {
        background:rgba(201,162,39,0.12); border-left:4px solid #c9a227;
        border-radius:0; padding:0.7rem 1rem; margin-bottom:1rem;
        font-size:0.92rem; color:#e8d9a8;
    }

    /* 對話完成橫幅 — 深灰底 + 金色文字，慶祝完成這個時刻 */
    .completion-banner {
        background: linear-gradient(135deg, #2c2c2c, #1a1a1a);
        color: #ffd966; border-radius: 12px;
        padding: 1.2rem 1.8rem; margin: 1rem 0; text-align: center;
    }

    .upload-hint { font-size:0.88rem; color:#8a8a88; margin-top:0.4rem; }
    hr { border-color:#3a3a38; }

    /* 隱藏 Streamlit 預設 footer、選單與工具列，去除平台識別痕跡 */
    footer { visibility:hidden; }
    #MainMenu { visibility:hidden; }
    header[data-testid="stHeader"] { background: transparent; }
    [data-testid="stToolbar"] { visibility:hidden; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════
# 頁面頂部 Logo
# ══════════════════════════════════════════════
# ── 側邊欄：登出 + 登入資訊 ──────────────────────
with st.sidebar:
    _role    = st.session_state.get("role", "")
    _company = st.session_state.get("current_company", "")
    _emp     = st.session_state.get("employee_name", "")
    role_label = "🏢 主管" if _role == "admin" else "👤 員工"
    st.markdown(f"**{role_label}**")
    st.caption(f"公司：{_company}")
    if _emp:
        st.caption(f"姓名：{_emp}")
    st.markdown("---")
    if st.button("🚪 登出"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

col_logo, _ = st.columns([1, 4])
with col_logo:
    st.markdown(
        '<div style="font-size:1.6rem; font-weight:800; color:#e8e8e6;">'
        'Pitch<span style="font-weight:400; color:#a8a8a6;">Coach</span></div>',
        unsafe_allow_html=True
    )
st.markdown("---")
st.markdown('<p class="hero-title">企業中控台 v3</p>', unsafe_allow_html=True)
st.markdown('<p class="hero-subtitle">主管中控台負責設定訓練劇本；員工實戰沙盒提供 AI 客戶角色扮演練習。</p>', unsafe_allow_html=True)


# ══════════════════════════════════════════════
# 雙頁籤主架構
# ══════════════════════════════════════════════
current_role = st.session_state.get("role", "employee")

if current_role == "admin":
    tab1, tab3 = st.tabs([
        "　⚙️　模塊一：企業中控台　",
        "　📊　模塊三：戰後報表台　",
    ])
    tab2 = None   # 員工沙盒對主管隱藏
else:
    tab2, tab3 = st.tabs([
        "　🎮　模塊二：實戰沙盒　",
        "　📊　模塊三：我的訓練報告　",
    ])
    tab1 = None   # 中控台對員工隱藏


# ╔══════════════════════════════════════════════╗
# ║  TAB 1：主管中控台                            ║
# ╚══════════════════════════════════════════════╝
if tab1 is not None:
 with tab1:

    col_left, col_right = st.columns([1, 2], gap="large")

    # ── 左欄：上傳與操作 ──────────────────────
    with col_left:
        # ── 已上傳的 PDF 管理列表 ──────────────────────
        _company_id_for_list = get_or_create_company(
            st.session_state.get("current_company", "")
        )
        _existing_pdfs = get_all_training_sets(_company_id_for_list)
        if _existing_pdfs:
            st.markdown("### 📚 已上傳的教材文件")
            st.caption("關閉開關可暫時停用該文件的題目，不刪除資料，可隨時重新啟用。")
            for _pdf in _existing_pdfs:
                _q_count = sum(
                    len(v) for v in (_pdf.get("questions_by_category") or {}).values()
                )
                _is_active = _pdf.get("is_active", True)
                _col_name, _col_count, _col_toggle, _col_del = st.columns([0.50, 0.15, 0.20, 0.15])
                with _col_name:
                    _status_icon = "✅" if _is_active else "⬜"
                    st.markdown(f"{_status_icon} 📄 **{_pdf.get('filename', '未命名')}**")
                with _col_count:
                    st.caption(f"{_q_count} 題")
                with _col_toggle:
                    _new_state = st.toggle(
                        "啟用中" if _is_active else "已停用",
                        value=_is_active,
                        key=f"toggle_pdf_{_pdf['id']}"
                    )
                    if _new_state != _is_active:
                        if toggle_training_set_active(_pdf["id"], _new_state):
                            # 連 settings_loaded 一起清掉，強制下一次重新執行時
                            # 重新從 Supabase 完整同步，而不是只清空記憶體卻沒有
                            # 任何機制補回正確資料
                            for _k in ["questions", "questions_by_category",
                                       "analyzed_filename", "task_published",
                                       "published_questions", "settings_loaded"]:
                                st.session_state.pop(_k, None)
                            st.rerun()
                with _col_del:
                    if st.button("🗑️", key=f"del_pdf_{_pdf['id']}"):
                        if delete_training_set(_pdf["id"]):
                            for _k in ["questions", "questions_by_category",
                                       "analyzed_filename", "task_published",
                                       "published_questions", "settings_loaded"]:
                                st.session_state.pop(_k, None)
                            st.success("✅ 已刪除，題庫已更新")
                            st.rerun()

            st.markdown("---")

        st.markdown("### 📂 上傳教材文件")
        uploaded_file = st.file_uploader(
            label="選擇 PDF 檔案",
            type=["pdf"],
            help="支援標準 PDF 格式。掃描版圖片 PDF 可能無法萃取文字。",
            label_visibility="collapsed"
        )
        st.markdown('<p class="upload-hint">📎 支援格式：PDF　｜　建議大小：10MB 以內</p>', unsafe_allow_html=True)
        st.caption(
            f"💡 單次上傳最多生成 {TOTAL_QUESTION_LIMIT} 道題目（不分類別個別上限，"
            f"AI 會依材料豐富度智能分配）。若您的產品資料非常豐富，建議拆分成多份文件"
            f"分次上傳，以確保題目品質與涵蓋度。"
        )
        st.markdown("---")
        st.markdown("#### 🎯 出題模式設定")

        mode = st.radio(
            label="選擇出題方式",
            options=["🎲 隨機挑戰模式", "🎯 主管精選模式"],
            key="question_mode",
            horizontal=True,
            help="隨機挑戰：系統每次自動隨機抽2題｜主管精選：手動勾選2題"
        )

        if mode == "🎲 隨機挑戰模式":
            st.info("🎲 系統將從所有題目中每次隨機抽取 2 題，員工每次練習題目都不同。主管無需手動選題。")
        else:
            st.info("🎯 AI萃取後，主管從題目中勾選 2 題作為本次訓練任務。")

        st.markdown("---")

        if uploaded_file is not None:
            st.success(f"✅ 已上傳：**{uploaded_file.name}**")
            st.caption(f"檔案大小：{len(uploaded_file.getvalue())/1024:.1f} KB")
            st.markdown("<br>", unsafe_allow_html=True)
            start_button = st.button("🚀 開始 AI 萃取", type="primary", use_container_width=True)
        else:
            st.info("👆 請先上傳 PDF 教材，再點擊下方按鈕。")
            start_button = False

        st.markdown("---")
        with st.expander("📖 使用說明"):
            st.markdown(f"""
            1. **上傳** 業務培訓 PDF 教材
            2. 點擊 **「🚀 開始 AI 萃取」**
            3. 在右側**編輯**考題並**勾選 {REQUIRED_SELECTION} 題**
            4. 填寫下方「自訂客戶情境」
            5. 按下 **「💾 儲存並發布訓練任務」**
            6. 切換到 **員工實戰沙盒** 頁籤進行練習

            > 教材文字越清晰，AI 分析越精準。
            """)

        # ── 自訂客戶情境輸入 ──────────────────────
        st.markdown("---")
        st.markdown("#### 👤 自訂客戶情境與狀態")
        st.caption("設定 AI 客戶的背景，讓每次演練更貼近真實場景。")
        customer_scenario_input = st.text_area(
            label="自訂客戶情境",
            value=st.session_state.get("customer_scenario", ""),
            placeholder="例如：已使用產品兩個月的老客，對效果半信半疑。\n或：完全沒聽過這個品牌的新客，預算有限。",
            height=120,
            key="customer_scenario_widget",
            label_visibility="collapsed"
        )
        # 即時同步到 session_state（不需按鈕）
        st.session_state["customer_scenario"] = customer_scenario_input

        # 顯示目前任務狀態
        st.markdown("---")
        st.markdown("#### 📡 任務發布狀態")
        if st.session_state.get("task_published", False):
            st.success(f"✅ 任務已發布　｜　{REQUIRED_SELECTION} 題已設定")
            st.caption(f"來源教材：{st.session_state.get('analyzed_filename', '—')}")
        else:
            st.warning("⏳ 尚未發布任務")

    # ── 右欄：分析結果 ────────────────────────
    with col_right:

        # 觸發 AI 分析
        if start_button and uploaded_file is not None:
            with st.spinner("🤖 AI 正在深度分析教材，請稍候（約 30～60 秒，分兩階段執行）..."):
                try:
                    pdf_bytes = uploaded_file.getvalue()
                    document_text = extract_text_from_bytes(pdf_bytes)

                    if len(document_text.strip()) < 100:
                        st.warning("⚠️ 萃取到的文字量極少，此 PDF 可能是掃描版圖片 PDF。")
                    else:
                        # 第一階段：產出三大重點分析（Markdown）
                        main_analysis = analyze_with_claude(document_text)

                        # 抓取目前已存在的題目（只抓題目本身），傳給AI避免生成
                        # 重複題目。用新鮮查詢直接問Supabase，不依賴session state，
                        # 確保拿到的是當下真實、完整的題庫（不受session快取影響）。
                        _existing_rows_for_dedup = get_all_training_sets(
                            get_or_create_company(st.session_state.get("current_company", ""))
                        )
                        _existing_questions_flat = []
                        for _erow in _existing_rows_for_dedup:
                            for _eqs in (_erow.get("questions_by_category") or {}).values():
                                _existing_questions_flat.extend(_eqs)

                        # 第二階段：按 7 大類別智能生成題目，加總最多 TOTAL_QUESTION_LIMIT 題（三層防護解析）
                        questions_dict, question_gen_meta = generate_questions_json(
                            document_text, existing_questions=_existing_questions_flat
                        )

                        # 合併為扁平 list，供現有的 UI/邏輯向下相容
                        all_questions: list[str] = []
                        for cat_questions in questions_dict.values():
                            all_questions.extend(cat_questions)

                        # 累積合併：新 PDF 的題目加入現有題庫，不覆蓋
                        _existing_qbc = st.session_state.get("questions_by_category") or {}
                        for _cat, _qs in questions_dict.items():
                            if _cat not in _existing_qbc:
                                _existing_qbc[_cat] = []
                            _existing_qbc[_cat].extend(_qs)

                        _existing_qs = st.session_state.get("questions") or []
                        _existing_fn = st.session_state.get("analyzed_filename", "")
                        _new_fn = ((_existing_fn + "、") if _existing_fn else "") + uploaded_file.name

                        st.session_state["main_analysis"]         = main_analysis
                        st.session_state["questions"]             = _existing_qs + all_questions
                        st.session_state["questions_by_category"] = _existing_qbc
                        st.session_state["analyzed_filename"]     = _new_fn
                        st.session_state["task_published"]        = False

                        # 動態提取各章節，供客戶 AI 與教練 AI 動態注入
                        # 以 emoji 為錨點，不依賴中文標題，對格式變動有高容錯率
                        raw_name     = extract_section(main_analysis, "🏷️")
                        # 產品名稱只取第一行（避免 Claude 多說描述文字），並清理 Markdown 符號
                        product_name = re.sub(r'[*_#`]', '', raw_name.split("\n")[0]).strip()
                        st.session_state["product_name"]     = product_name
                        st.session_state["product_benefits"] = extract_section(main_analysis, "📌")
                        st.session_state["target_audience"]  = extract_section(main_analysis, "🎯")

                        # 立即把「這份文件自己的」萃取結果存成一筆獨立的資料庫紀錄，
                        # 只包含這次上傳的內容，不是累積後的整包題庫——避免上傳第2、
                        # 第3份文件時，把前面文件的題目也一併重複存進來造成資料膨脹。
                        # 「累積顯示」這件事只應該發生在讀取端。
                        save_training_set_file(
                            company_id=st.session_state.get("company_id", ""),
                            filename=uploaded_file.name,
                            questions_by_category=questions_dict,
                            questions=all_questions,
                            product_name=product_name,
                            main_analysis=main_analysis,
                            product_benefits=st.session_state["product_benefits"],
                            target_audience=st.session_state["target_audience"],
                        )

                        # AI 萃取完成提示：讓使用者知道這次實際生成了幾題，
                        # 若材料豐富度超過單次上限，也要讓使用者知道發生了裁切，而不是無聲少題
                        st.success(f"✅ AI 萃取完成，本次共生成 {question_gen_meta['total_kept']} 道題目")
                        if question_gen_meta["was_trimmed"]:
                            st.warning(
                                f"⚠️ 這份文件的材料非常豐富，AI 原本想生成 "
                                f"{question_gen_meta['total_generated']} 題，但單次上傳上限是 "
                                f"{TOTAL_QUESTION_LIMIT} 題，已為您保留前 {TOTAL_QUESTION_LIMIT} 題。"
                                f"建議將這份文件剩餘的內容拆成下一份文件另外上傳，"
                                f"以涵蓋更完整的題目。"
                            )

                        # 清除舊的考題 widget 狀態（動態掃描，不寫死題數上限）
                        # 題庫不管累積到多大（30題、90題、上百題）都能完整清除，
                        # 避免舊的勾選/編輯狀態殘留、意外附著到新題目上。
                        # 這裡順便把「隨機挑戰模式」編輯框（q_text_random_*）也一併
                        # 納入清除範圍，因為它也是同一種「舊題目換新後殘留舊狀態」的問題。
                        _stale_widget_keys = [
                            k for k in st.session_state.keys()
                            if k.startswith("q_text_") or k.startswith("q_check_")
                        ]
                        for k in _stale_widget_keys:
                            st.session_state.pop(k, None)
                        # 清除員工端舊的對話記錄
                        st.session_state.pop("chat_history", None)
                        st.session_state.pop("current_q_idx", None)

                except anthropic.AuthenticationError:
                    st.error("❌ API Key 驗證失敗，請確認 main.py 第 15 行的 Key 是否正確。")
                except anthropic.RateLimitError:
                    st.error("❌ API 請求過於頻繁，請稍等 1 分鐘後再試。")
                except Exception as e:
                    st.error(f"❌ 發生錯誤：{str(e)}")

        # 顯示分析結果
        if "main_analysis" in st.session_state and "questions" in st.session_state:

            # 三大分析重點（main_analysis）仍會照常生成，並繼續作為AI客戶
            # 角色扮演的背景資料使用，只是不在這裡顯示給主管看，讓主管能
            # 更快看到題目本身。
            st.caption(f"📄 分析來源：{st.session_state.get('analyzed_filename', '上傳的文件')}")
            st.markdown("---")

            # 互動考題編輯區（依出題模式分流）
            questions = st.session_state["questions"]
            total_q   = len(questions)
            current_mode = st.session_state.get("question_mode", "🎯 主管精選模式")

            if current_mode == "🎯 主管精選模式":
                # ── 主管精選模式：勾選 2 題後發布 ──────────────────
                st.markdown("### 💬 實戰刁難考題 — 主管審核區（10 選 2 極速對決）")
                st.markdown(
                    f'<div class="selection-hint">'
                    f'✏️ &nbsp;可直接編輯每題內容。'
                    f'　☑️ &nbsp;<strong>請剛好勾選 {REQUIRED_SELECTION} 題</strong>作為本次訓練任務。'
                    f'</div>',
                    unsafe_allow_html=True
                )

                if total_q == 0:
                    st.info("ℹ️ 無法自動解析考題格式，請重新執行 AI 萃取。")
                else:
                    questions_by_category = st.session_state.get("questions_by_category", {})
                    global_q_idx = 0  # 跨類別統一題號索引

                    if questions_by_category:
                        # ── 有分類資料：按類別用 expander 展開顯示（分類標籤來自 config.CATEGORY_LABELS，
                        #     會自動反映新增/移除的分類，不寫死數字）──
                        for cat_key, cat_label in CATEGORY_LABELS.items():
                            cat_questions = questions_by_category.get(cat_key, [])
                            if not cat_questions:
                                continue
                            with st.expander(f"{cat_label}（共 {len(cat_questions)} 題）", expanded=True):
                                for _, q_text in enumerate(cat_questions):
                                    is_checked = st.session_state.get(f"q_check_{global_q_idx}", False)
                                    card_class = "question-card-selected" if is_checked else "question-card"
                                    st.markdown(f'<div class="{card_class}">', unsafe_allow_html=True)
                                    col_cb, col_ti = st.columns([0.07, 0.93])
                                    with col_cb:
                                        st.markdown("<br>", unsafe_allow_html=True)
                                        st.checkbox("選取", key=f"q_check_{global_q_idx}",
                                                    label_visibility="collapsed")
                                    with col_ti:
                                        st.text_input(
                                            label=f"題目 {global_q_idx + 1}",
                                            value=q_text,
                                            key=f"q_text_{global_q_idx}",
                                        )
                                    st.markdown('</div>', unsafe_allow_html=True)
                                    global_q_idx += 1
                        total_q = global_q_idx

                    else:
                        # ── 舊版相容：沒有分類資料時用原本的扁平列表 ──
                        for i, q_text in enumerate(questions):
                            is_checked = st.session_state.get(f"q_check_{i}", False)
                            card_class = "question-card-selected" if is_checked else "question-card"
                            st.markdown(f'<div class="{card_class}">', unsafe_allow_html=True)
                            col_cb, col_ti = st.columns([0.07, 0.93])
                            with col_cb:
                                st.markdown("<br>", unsafe_allow_html=True)
                                st.checkbox("選取", key=f"q_check_{i}", label_visibility="collapsed")
                            with col_ti:
                                st.text_input(
                                    label=f"第 {i+1} 題",
                                    value=q_text,
                                    key=f"q_text_{i}",
                                )
                            st.markdown('</div>', unsafe_allow_html=True)

                    # 勾選計數（10 選 2 防呆，total_q 已在上方更新）
                    checked_count = sum(
                        st.session_state.get(f"q_check_{i}", False)
                        for i in range(total_q)
                    )
                    st.markdown("<br>", unsafe_allow_html=True)

                    if checked_count == 0:
                        st.warning(f"⚠️ 請剛好勾選 {REQUIRED_SELECTION} 題作為本次訓練任務")
                    elif checked_count < REQUIRED_SELECTION:
                        st.warning(f"⚠️ 請剛好勾選 {REQUIRED_SELECTION} 題作為本次訓練任務（目前 {checked_count} 題，還差 {REQUIRED_SELECTION - checked_count} 題）")
                    elif checked_count == REQUIRED_SELECTION:
                        st.success(f"✅ 已勾選 {checked_count} 題，可以發布任務了！")
                    else:
                        st.warning(f"⚠️ 請剛好勾選 {REQUIRED_SELECTION} 題作為本次訓練任務（目前 {checked_count} 題，請取消 {checked_count - REQUIRED_SELECTION} 題）")

                    # ── 主管自訂考題 ──────────────────────
                    st.markdown("---")
                    st.markdown("#### ✏️ 主管自訂考題")
                    st.caption(
                        "AI 沒有出到的問題，可以在這裡手動新增。「建議回答方向」欄位可留空，"
                        "但填寫後這道題才會有教練提示與評分依據，品質才會跟 AI 出的題一致，"
                        "強烈建議填寫。"
                    )

                    new_q = st.text_input(
                        label="題目",
                        placeholder="例如：你們的產品跟市面上的有什麼不同？",
                        key="new_custom_q_input",
                    )
                    new_q_hint = st.text_area(
                        label="建議回答方向（選填，但強烈建議填寫）",
                        placeholder="例如：說明產品的獨家成分與認證，並舉出跟競品的具體差異",
                        key="new_custom_q_hint_input",
                        height=80,
                    )
                    if st.button("➕ 新增", key="add_custom_main"):
                        if new_q.strip():
                            _combined_q = new_q.strip()
                            if new_q_hint.strip():
                                _combined_q += f" 👉 建議回答方向：{new_q_hint.strip()}"
                            st.session_state["custom_questions"].append(_combined_q)
                            st.rerun()

                    if st.session_state.get("custom_questions"):
                        st.caption("以下題目歸類為「✏️ 主管自訂類」，會與 AI 題目一起發布並可被隨機抽中。")
                        for ci, cq in enumerate(st.session_state["custom_questions"]):
                            col_cq, col_del = st.columns([0.88, 0.12])
                            with col_cq:
                                st.info(f"✏️ {cq}")
                            with col_del:
                                if st.button("🗑️", key=f"del_custom_{ci}"):
                                    st.session_state["custom_questions"].pop(ci)
                                    st.rerun()

                    st.markdown("<br>", unsafe_allow_html=True)

                    publish_button = st.button(
                        "🚀 儲存並發布訓練任務",
                        type="primary",
                        use_container_width=True,
                        disabled=(checked_count != REQUIRED_SELECTION)
                    )

                    if publish_button:
                        selected_qs = [
                            st.session_state[f"q_text_{i}"]
                            for i in range(total_q)
                            if st.session_state.get(f"q_check_{i}", False)
                        ]
                        st.session_state["published_questions"] = selected_qs
                        # 合併主管自訂問題
                        if st.session_state.get("custom_questions"):
                            st.session_state["published_questions"].extend(
                                st.session_state["custom_questions"]
                            )
                        st.session_state["task_published"]      = True
                        st.session_state.pop("chat_history", None)
                        st.session_state.pop("current_q_idx", None)
                        st.session_state.pop("is_completed", None)
                        st.session_state.pop("evaluation_report", None)
                        st.rerun()

                    if st.session_state.get("task_published", False):
                        st.success("🎉 **任務已成功發布！** 請通知員工切換到「員工實戰沙盒」頁籤開始練習。")
                        with st.expander(f"📋 已發布的 {REQUIRED_SELECTION} 道考題（預覽）"):
                            for idx, q in enumerate(st.session_state.get("published_questions", []), 1):
                                st.markdown(f"**第 {idx} 題：**\n{q}")
                                st.markdown("---")

            else:
                # ── 隨機挑戰模式：唯讀預覽全部題目，系統自動抽題 ────
                st.markdown("### 💬 題庫預覽（隨機挑戰模式）")
                st.markdown(
                    '<div class="selection-hint">'
                    '🎲 &nbsp;以下是所有題目，員工每次練習系統將自動隨機抽取 2 題。'
                    '</div>',
                    unsafe_allow_html=True
                )

                if total_q == 0:
                    st.info("ℹ️ 無法自動解析考題格式，請重新執行 AI 萃取。")
                else:
                    # 即時向 Supabase 查詢目前所有「啟用中」文件的題目，依「類別 →
                    # 來源文件」兩層分組顯示，不再依賴瀏覽器記憶體裡的
                    # questions_by_category（那份資料在切換啟用狀態、刪除文件、
                    # 或多次上傳之間可能沒有即時同步，改成每次都直接問資料庫拿
                    # 當下真實狀態，畫面就不會再跟實際資料不一致）。
                    _company_id_for_preview = get_or_create_company(
                        st.session_state.get("current_company", "")
                    )
                    _all_rows_for_preview = get_all_training_sets(_company_id_for_preview)
                    _active_rows_for_preview = [
                        r for r in _all_rows_for_preview if r.get("is_active", True)
                    ]
                    _row_excluded_lookup = {
                        r.get("id"): set(r.get("excluded_questions") or [])
                        for r in _active_rows_for_preview
                    }

                    cat_labels_short = {
                        "cat_1_product":     "🔍 產品理解類",
                        "cat_2_price":       "💰 價格異議類",
                        "cat_3_trust":       "🛡️ 信任疑慮類",
                        "cat_4_competition": "⚔️ 競品比較類",
                        "cat_5_decision":    "🚪 決策障礙類",
                        "cat_org_trust":     "🏢 組織與商業模式疑慮類",
                        "cat_rules_info":    "📖 規則與資格說明類",
                    }

                    if not _active_rows_for_preview and not st.session_state.get("custom_questions"):
                        # ── 舊版相容：查無分類資料時，顯示扁平列表 ────────────
                        for i, q_text in enumerate(questions):
                            st.markdown(f'<div class="question-card">', unsafe_allow_html=True)
                            col_num, col_content = st.columns([0.07, 0.93])
                            with col_num:
                                st.markdown(f"**{i+1}**")
                            with col_content:
                                st.text_input(
                                    label=f"第 {i+1} 題",
                                    value=q_text,
                                    key=f"q_text_random_{i}",
                                )
                            st.markdown('</div>', unsafe_allow_html=True)
                        st.markdown("<br>", unsafe_allow_html=True)
                        st.success(f"✅ 共 {total_q} 題已載入題庫，系統將每次隨機抽取 2 題。")
                    else:
                        total_count = 0
                        active_cat_count = 0
                        for _cat_key in cat_labels_short:
                            _cat_total = sum(
                                len((r.get("questions_by_category") or {}).get(_cat_key, []))
                                for r in _active_rows_for_preview
                            )
                            total_count += _cat_total
                            if _cat_total > 0:
                                active_cat_count += 1
                        if st.session_state.get("custom_questions"):
                            total_count += len(st.session_state["custom_questions"])
                            active_cat_count += 1

                        st.success(
                            f"✅ 題庫共 {total_count} 題，分為 {active_cat_count} 大類別，"
                            f"系統每次從不同類別各抽 1 題（共 2 題）。"
                        )

                        for cat_key, cat_label in cat_labels_short.items():
                            _cat_file_groups = [
                                (r.get("id"), r.get("filename", "未命名"),
                                 (r.get("questions_by_category") or {}).get(cat_key, []))
                                for r in _active_rows_for_preview
                            ]
                            _cat_file_groups = [(rid, fn, qs) for rid, fn, qs in _cat_file_groups if qs]
                            if not _cat_file_groups:
                                continue
                            _cat_total_qs = sum(len(qs) for _, _, qs in _cat_file_groups)
                            # 判斷這個分類「目前是否全部題目都納入」，作為批次打勾的初始狀態
                            _cat_all_included = all(
                                _qq not in _row_excluded_lookup.get(_rrid, set())
                                for _rrid, _fn2, _qqs in _cat_file_groups
                                for _qq in _qqs
                            )
                            _cat_col_check, _cat_col_label = st.columns([0.06, 0.94])
                            with _cat_col_check:
                                _cat_new_included = st.checkbox(
                                    "分類批次", value=_cat_all_included,
                                    key=f"catbatch_{cat_key}_{int(_cat_all_included)}",
                                    label_visibility="collapsed"
                                )
                            with _cat_col_label:
                                st.markdown(f"**{cat_label}（{_cat_total_qs} 題）**")
                            if _cat_new_included != _cat_all_included:
                                for _rrid, _fn2, _qqs in _cat_file_groups:
                                    for _qq in _qqs:
                                        toggle_question_included(_rrid, _qq, _cat_new_included)
                                st.rerun()
                            with st.expander("查看／編輯題目", expanded=False):
                                for _rid, _fn, _qs in _cat_file_groups:
                                    st.markdown(f"**── 📄 來自：{_fn} ──**")
                                    for _qi, _q in enumerate(_qs):
                                        if "👉" in _q:
                                            _q_disp, _q_hint = _q.split("👉", 1)
                                            _q_hint = re.sub(r'^\s*建議回答方向[：:]\s*', '', _q_hint.strip())
                                        else:
                                            _q_disp, _q_hint = _q, ""

                                        _edit_key = f"qedit_mode_{_rid}_{cat_key}_{_qi}"
                                        _delc_key = f"qdel_confirm_{_rid}_{cat_key}_{_qi}"

                                        if st.session_state.get(_edit_key, False):
                                            # ── 編輯模式 ──
                                            _new_q_disp = st.text_input(
                                                "題目", value=_q_disp.strip(),
                                                key=f"qedit_text_{_rid}_{cat_key}_{_qi}"
                                            )
                                            _new_q_hint = st.text_area(
                                                "建議回答方向", value=_q_hint.strip(),
                                                key=f"qedit_hint_{_rid}_{cat_key}_{_qi}", height=68
                                            )
                                            _col_save, _col_cancel = st.columns(2)
                                            with _col_save:
                                                if st.button("💾 儲存", key=f"qedit_save_{_rid}_{cat_key}_{_qi}",
                                                             use_container_width=True):
                                                    _combined = _new_q_disp.strip()
                                                    if _new_q_hint.strip():
                                                        _combined += f" 👉 建議回答方向：{_new_q_hint.strip()}"
                                                    if update_training_set_question(_rid, cat_key, _qi, new_text=_combined):
                                                        st.session_state[_edit_key] = False
                                                        st.success("✅ 已更新")
                                                        st.rerun()
                                            with _col_cancel:
                                                if st.button("✖️ 取消", key=f"qedit_cancel_{_rid}_{cat_key}_{_qi}",
                                                             use_container_width=True):
                                                    st.session_state[_edit_key] = False
                                                    st.rerun()
                                        elif st.session_state.get(_delc_key, False):
                                            # ── 刪除確認模式 ──
                                            st.warning(f"⚠️ 確定要刪除這一題嗎？「{_q_disp.strip()[:40]}」")
                                            _col_confirm, _col_cancel2 = st.columns(2)
                                            with _col_confirm:
                                                if st.button("🗑️ 確認刪除", key=f"qdel_confirmbtn_{_rid}_{cat_key}_{_qi}",
                                                             use_container_width=True):
                                                    if update_training_set_question(_rid, cat_key, _qi, delete=True):
                                                        st.session_state[_delc_key] = False
                                                        st.success("✅ 已刪除")
                                                        st.rerun()
                                            with _col_cancel2:
                                                if st.button("取消", key=f"qdel_cancelbtn_{_rid}_{cat_key}_{_qi}",
                                                             use_container_width=True):
                                                    st.session_state[_delc_key] = False
                                                    st.rerun()
                                        else:
                                            # ── 一般顯示模式 ──
                                            _col_check, _col_q, _col_edit, _col_del = st.columns([0.08, 0.74, 0.09, 0.09])
                                            with _col_check:
                                                _is_included = _q not in _row_excluded_lookup.get(_rid, set())
                                                _new_included = st.checkbox(
                                                    "納入", value=_is_included,
                                                    key=f"qinc_{_rid}_{cat_key}_{_qi}_{int(_is_included)}",
                                                    label_visibility="collapsed"
                                                )
                                                if _new_included != _is_included:
                                                    toggle_question_included(_rid, _q, _new_included)
                                                    st.rerun()
                                            with _col_q:
                                                st.markdown(f"- {_q_disp.strip()}")
                                                if _q_hint.strip():
                                                    st.caption(f"　👉{_q_hint.strip()}")
                                            with _col_edit:
                                                if st.button("✏️", key=f"qedit_btn_{_rid}_{cat_key}_{_qi}"):
                                                    st.session_state.pop(f"qedit_text_{_rid}_{cat_key}_{_qi}", None)
                                                    st.session_state.pop(f"qedit_hint_{_rid}_{cat_key}_{_qi}", None)
                                                    st.session_state[_edit_key] = True
                                                    st.rerun()
                                            with _col_del:
                                                if st.button("🗑️", key=f"qdel_btn_{_rid}_{cat_key}_{_qi}"):
                                                    st.session_state[_delc_key] = True
                                                    st.rerun()
                                    st.markdown("")

                        if st.session_state.get("custom_questions"):
                            with st.expander(
                                f"✏️ 主管自訂類（{len(st.session_state['custom_questions'])} 題）",
                                expanded=False
                            ):
                                for _q in st.session_state["custom_questions"]:
                                    if "👉" in _q:
                                        _q_disp, _q_hint = _q.split("👉", 1)
                                        _q_hint = re.sub(r'^\s*建議回答方向[：:]\s*', '', _q_hint.strip())
                                    else:
                                        _q_disp, _q_hint = _q, ""
                                    st.markdown(f"- {_q_disp.strip()}")
                                    if _q_hint.strip():
                                        st.caption(f"　👉{_q_hint.strip()}")

                    # ── 主管自訂考題 ──────────────────────
                    st.markdown("---")
                    st.markdown("#### ✏️ 主管自訂考題")
                    st.caption(
                        "AI 沒有出到的問題，可以在這裡手動新增。「建議回答方向」欄位可留空，"
                        "但填寫後這道題才會有教練提示與評分依據，品質才會跟 AI 出的題一致，"
                        "強烈建議填寫。"
                    )

                    new_q_r = st.text_input(
                        label="題目（隨機模式）",
                        placeholder="例如：你們的產品跟市面上的有什麼不同？",
                        key="new_custom_q_input_random",
                    )
                    new_q_hint_r = st.text_area(
                        label="建議回答方向（選填，但強烈建議填寫）",
                        placeholder="例如：說明產品的獨家成分與認證，並舉出跟競品的具體差異",
                        key="new_custom_q_hint_input_random",
                        height=80,
                    )
                    if st.button("➕ 新增", key="add_custom_random"):
                        if new_q_r.strip():
                            _combined_q_r = new_q_r.strip()
                            if new_q_hint_r.strip():
                                _combined_q_r += f" 👉 建議回答方向：{new_q_hint_r.strip()}"
                            st.session_state["custom_questions"].append(_combined_q_r)
                            st.rerun()

                    if st.session_state.get("custom_questions"):
                        for ci, cq in enumerate(st.session_state["custom_questions"]):
                            col_cq_r, col_del_r = st.columns([0.88, 0.12])
                            with col_cq_r:
                                st.info(f"✏️ {cq}")
                            with col_del_r:
                                if st.button("🗑️", key=f"del_custom_r_{ci}"):
                                    st.session_state["custom_questions"].pop(ci)
                                    st.rerun()

                    publish_random_btn = st.button(
                        "🚀 儲存並啟用隨機挑戰模式",
                        type="primary",
                        use_container_width=True
                    )

                    if publish_random_btn:
                        # 儲存編輯後的所有題目；published_questions 留空，由員工端隨機抽取
                        all_questions = [
                            st.session_state.get(f"q_text_random_{i}", q)
                            for i, q in enumerate(st.session_state.get("questions", []))
                        ]
                        # 合併主管自訂問題進 cat_6_custom，確保能被隨機抽中
                        _qbc_to_save = dict(st.session_state.get("questions_by_category", {}))
                        if st.session_state.get("custom_questions"):
                            _qbc_to_save["cat_6_custom"] = st.session_state["custom_questions"]
                        st.session_state["questions_by_category"] = _qbc_to_save

                        st.session_state["questions"]           = all_questions
                        st.session_state["published_questions"] = []  # 員工端動態抽取
                        st.session_state["task_published"]      = True
                        st.session_state.pop("chat_history", None)
                        st.session_state.pop("current_q_idx", None)
                        st.session_state.pop("is_completed", None)
                        st.session_state.pop("evaluation_report", None)
                        save_settings()
                        st.rerun()

                    if st.session_state.get("task_published", False):
                        st.success("🎉 **隨機挑戰模式已啟用！** 員工每次練習將自動隨機抽取 2 題。")

        elif not start_button:
            st.markdown("""
            <div style="text-align:center;color:#adb5bd;padding:5rem 0;">
                <div style="font-size:3.5rem;">📋</div>
                <div style="margin-top:1rem;font-size:1.05rem;font-weight:600;">分析結果將顯示於此</div>
                <div style="font-size:0.88rem;margin-top:0.5rem;">請在左側上傳 PDF 並點擊「開始 AI 萃取」</div>
            </div>
            """, unsafe_allow_html=True)


# ══════════════════════════════════════════════
# WebCam 心理壓力鏡 HTML（嵌入 iframe，純前端顯示，影像不傳後端）
# 注意：鏡頭存取需要瀏覽器授權；Streamlit 在 localhost 執行時通常可正常取得權限
# ══════════════════════════════════════════════
WEBCAM_HTML = """
<style>
  body { margin:0; padding:0; background:transparent; font-family:system-ui,sans-serif; }
  .mirror-wrap {
    background: linear-gradient(160deg,#1a1a2e,#16213e);
    border-radius: 14px;
    padding: 14px 12px 10px;
    text-align: center;
  }
  .mirror-title {
    color: #ffd700;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.5px;
    margin-bottom: 10px;
  }
  #webcam {
    width: 100%;
    height: 175px;
    border-radius: 10px;
    background: #0d0d0d;
    transform: scaleX(-1);   /* 水平翻轉，讓畫面像真實鏡子 */
    object-fit: cover;
    display: block;
  }
  .cam-status {
    color: #adb5bd;
    font-size: 11px;
    margin: 7px 0 4px;
    min-height: 16px;
  }
  .cam-btn {
    padding: 5px 16px;
    border-radius: 20px;
    border: none;
    background: #4361ee;
    color: white;
    cursor: pointer;
    font-size: 12px;
    margin-top: 2px;
  }
  .cam-btn:hover { background: #3451d1; }
</style>
<div class="mirror-wrap">
  <div class="mirror-title">🪞 心理壓力鏡 — 你的臨場狀態</div>
  <video id="webcam" autoplay playsinline muted></video>
  <div class="cam-status" id="camStatus">正在啟動鏡頭...</div>
  <button class="cam-btn" id="camBtn" onclick="toggleCam()">⏹ 關閉鏡頭</button>
</div>
<script>
  let stream = null;

  async function startCam() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' } });
      document.getElementById('webcam').srcObject = stream;
      document.getElementById('camStatus').textContent = '🟢 鏡頭啟動 — 保持自信的眼神與表情';
      document.getElementById('camStatus').style.color = '#6fcf97';
      document.getElementById('camBtn').textContent = '⏹ 關閉鏡頭';
    } catch(e) {
      document.getElementById('camStatus').textContent = '❌ 無法存取鏡頭（請在瀏覽器允許相機權限後重整）';
      document.getElementById('camStatus').style.color = '#eb5757';
      document.getElementById('camBtn').textContent = '🔄 重試';
    }
  }

  function toggleCam() {
    if (stream) {
      stream.getTracks().forEach(t => t.stop());
      stream = null;
      document.getElementById('webcam').srcObject = null;
      document.getElementById('camStatus').textContent = '⏸ 鏡頭已關閉';
      document.getElementById('camStatus').style.color = '#adb5bd';
      document.getElementById('camBtn').textContent = '📷 重新開啟';
    } else {
      document.getElementById('camStatus').textContent = '正在啟動鏡頭...';
      startCam();
    }
  }

  startCam();  // 進入頁面自動啟動
</script>
"""

# ╔══════════════════════════════════════════════╗
# ║  TAB 2：員工實戰沙盒                          ║
# ╚══════════════════════════════════════════════╝
if tab2 is not None:
 with tab2:

    # ── 任務未發布：鎖定畫面 ──────────────────
    if not st.session_state.get("task_published", False):
        st.markdown("""
        <div class="locked-box">
            <div style="font-size:3rem;">🔒</div>
            <div style="font-size:1.2rem;font-weight:700;margin:1rem 0 0.5rem;">訓練任務尚未發布</div>
            <div style="font-size:0.95rem;">請等待主管在「主管中控台」頁籤完成教材分析並發布任務後，再進入此沙盒練習。</div>
        </div>
        """, unsafe_allow_html=True)

    # ── 任務已發布：解鎖沙盒 ──────────────────
    else:
        current_mode = st.session_state.get("question_mode", "🎯 主管精選模式")

        if current_mode == "🎲 隨機挑戰模式":
            # 隨機挑戰模式：強制順序覆蓋法
            # 階段A（覆蓋模式）：只要還有任何啟用中的分類存在「沒練過的題目」，
            # 就按固定順序（config.CATEGORY_LABELS的順序）輪替分類，優先把每個
            # 分類的題目都摸過一輪，確保新人不會漏練任何一種疑慮。
            # 階段B（分數優先模式）：所有分類都覆蓋完畢後，改成排除「上一次剛
            # 練過的分類」，剩下的分類用「平均分數越低、被選中機率越高」的
            # 加權隨機去選，弱點會更常被抽到，但不會每次都卡在同一類。
            # 兩個階段選定分類後，分類底下的題目一律用「沒看過優先、其次練習
            # 次數最少優先、都差不多才隨機」去挑，最多抽2題，分類本身只有1題
            # 就只出1題，不勉強湊數。
            if not st.session_state.get("chat_history"):
                questions_by_category = st.session_state.get("questions_by_category", {})
                _company_id_now = st.session_state.get("company_id", "")
                _employee_now = st.session_state.get("employee_name", "匿名員工")

                randomly_selected, selected_cat = select_next_questions(
                    _company_id_now, _employee_now, questions_by_category
                )
                if not randomly_selected:
                    # 完全沒有分類資料：從扁平列表隨機抽 2 題（舊版相容）
                    all_q = st.session_state.get("questions", [])
                    randomly_selected = random.sample(all_q, min(2, len(all_q)))
                    selected_cat = ""

                st.session_state["current_random_category"] = selected_cat
                st.session_state["current_random_questions"] = randomly_selected

            published_questions = st.session_state.get(
                "current_random_questions",
                st.session_state.get("questions", [])[:2]
            )
        else:
            published_questions = st.session_state.get("published_questions", [])
            # 若 published_questions 是空的，從完整題庫隨機抽 2 題當備援
            if not published_questions:
                all_q = st.session_state.get("questions", [])
                if all_q:
                    published_questions = random.sample(all_q, min(2, len(all_q)))
                else:
                    # 題庫也是空的，從 questions_by_category 裡抽
                    qbc = st.session_state.get("questions_by_category", {})
                    all_from_cat = [q for qs in qbc.values() for q in qs]
                    if all_from_cat:
                        published_questions = random.sample(
                            all_from_cat, min(2, len(all_from_cat))
                        )

        main_analysis       = st.session_state.get("main_analysis", "")
        total_q             = len(published_questions)

        # 初始化聊天 session state（只在第一次進入時執行）
        if "chat_history" not in st.session_state:
            st.session_state["chat_history"]  = []
            st.session_state["current_q_idx"] = 0
            st.session_state["q_turn_count"]  = 0  # 急速模式每題回合計數器
        if "custom_questions" not in st.session_state:
            st.session_state["custom_questions"] = []

        current_q_idx = st.session_state["current_q_idx"]
        chat_history  = st.session_state["chat_history"]

        # 自動從登入時的 session_state 帶入姓名，不需要再輸入
        if not st.session_state.get("employee_name"):
            st.warning("請先登入後再進入訓練。")
            st.stop()

        # 顯示已確認的姓名（對話進行中全程可見）
        if st.session_state.get("employee_name"):
            st.success(f"👤 練習者：{st.session_state['employee_name']}")

        # ── 訓練模式選擇（只在對話尚未開始時顯示）──
        if not st.session_state.get("chat_history"):
            st.markdown("### ⚡ 選擇今日訓練模式")

            col_m1, col_m2 = st.columns(2)
            with col_m1:
                if st.button(
                    "⚡ 急速模式\n\n3-5分鐘・每題最多1次追問・適合日常練習",
                    use_container_width=True,
                    type="secondary",
                    key="select_speed_mode"
                ):
                    st.session_state["training_mode"] = "speed"
                    st.rerun()
            with col_m2:
                if st.button(
                    "🔥 深度模式\n\n10-15分鐘・嚴格追問・適合重要考核前",
                    use_container_width=True,
                    type="secondary",
                    key="select_deep_mode"
                ):
                    st.session_state["training_mode"] = "deep"
                    st.rerun()

            # 如果還沒選模式，停止顯示後面內容
            if not st.session_state.get("training_mode"):
                st.stop()

        # 顯示已選模式
        training_mode = st.session_state.get("training_mode", "speed")
        if training_mode == "speed":
            st.info("⚡ 急速模式：每題最多追問一次，快速決勝負")
        else:
            st.warning("🔥 深度模式：AI嚴格追問，答不好絕不放過")

        # ── 任務簡報橫幅（全寬）──
        st.markdown("""
        <div class="mission-banner">
            <h3>🎯 訓練任務簡報</h3>
            <p>
                請向接下來出現的 <strong>AI 消費者</strong> 進行推銷。對方是對健康產品有興趣、但抱有生活化疑慮的普通客戶。<br>
                共有 <strong>2 道關卡</strong>，全部通過後 AI 客戶將給出最終購買決定。
            </p>
        </div>
        """, unsafe_allow_html=True)

        # ── 頂部工具列（全寬）──
        col_ref, col_prog, col_toggle, col_reset = st.columns([2, 1.5, 1.5, 1])
        with col_ref:
            with st.expander("📖 查看主管設定的培訓劇本"):
                st.markdown(main_analysis)
        with col_prog:
            questions_asked = min(current_q_idx, total_q)
            st.markdown(f'<p class="progress-label">訓練進度：已通過 {questions_asked} / {total_q} 關</p>', unsafe_allow_html=True)
            st.progress(questions_asked / total_q if total_q > 0 else 0)
        with col_toggle:
            st.markdown("<br>", unsafe_allow_html=True)
            # Toggle 狀態由 Streamlit 自動儲存在 session_state["coach_mode_toggle"]
            # 關閉時清除殘留提示，讓切換回純壓力測試模式時畫面乾淨
            coach_mode = st.toggle(
                "💡 教練輔助模式",
                key="coach_mode_toggle",
                help="開啟後，每次 AI 客戶回覆完畢，教練會自動給你一句戰術提示"
            )
            if not coach_mode:
                st.session_state["coach_hint"] = None
        with col_reset:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🔄 重新練習", use_container_width=True):
                st.session_state["chat_history"]  = []
                st.session_state["current_q_idx"] = 0
                st.session_state["q_turn_count"]  = 0
                st.session_state["coach_hint"]    = None
                st.session_state.pop("employee_name", None)
                st.session_state.pop("training_mode", None)
                st.rerun()

        st.markdown("---")

        # ══════════════════════════════════════════
        # 主區域：左欄（壓力鏡 + 語音輸入）| 右欄（對話）
        # ══════════════════════════════════════════
        col_tools, col_chat = st.columns([3, 7], gap="large")

        # ── 左欄：工具面板 ────────────────────────
        with col_tools:

            # 1. 心理壓力鏡（WebCam）
            st.markdown("#### 🪞 心理壓力鏡")
            st.caption("看著自己的臉說話，感受真實的臨場壓力")
            # 嵌入 WebCam HTML/JS，純前端顯示，影像完全不傳後端
            components.html(WEBCAM_HTML, height=270, scrolling=False)

            st.markdown("---")

            # 小提示卡：溝通技巧提醒
            st.markdown("""
            <div style="background:#e8f4fd;border-left:3px solid #2196f3;
                        border-radius:6px;padding:0.7rem 0.9rem;font-size:0.82rem;color:#1565c0;">
            <strong>💡 溝通技巧</strong><br>
            用生活化語言說話<br>
            具體回答客戶疑慮<br>
            → AI 客戶滿意才跳下一題<br>
            <em>同理心 > 術語</em>
            </div>
            """, unsafe_allow_html=True)

        # ── 右欄：對話區 ──────────────────────────
        with col_chat:

            if not chat_history:
                st.markdown("""
                <div style="text-align:center;color:#adb5bd;padding:3rem 0 1.5rem;">
                    <div style="font-size:2.5rem;">💬</div>
                    <div style="font-size:1rem;margin-top:0.8rem;font-weight:600;">對話尚未開始</div>
                    <div style="font-size:0.88rem;margin-top:0.5rem;">
                        在下方輸入開場白，AI 消費者將立即回應並提出第 1 道疑慮。<br>
                        <em>開場範例：「您好，我想介紹一款對肩頸很有幫助的產品...」</em>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                for i, msg in enumerate(chat_history):
                    if msg["role"] == "user":
                        with st.chat_message("user", avatar="🧑‍💼"):
                            st.markdown(msg["content"])
                    else:
                        with st.chat_message("assistant", avatar="🧑"):
                            # 顯示時移除暗號標籤（系統內部信號，不呈現給使用者）
                            display_content = (
                                msg["content"]
                                .replace("[TEST_COMPLETE]", "")
                                .replace("[NEXT_Q]", "")
                                .strip()
                            )
                            st.markdown(display_content)

            # 教練提示顯示區（只在教練模式開啟 & 有提示時出現）
            # 此處只顯示，不存入 chat_history，客戶 AI 完全看不到
            if coach_mode and st.session_state.get("coach_hint"):
                st.info(st.session_state["coach_hint"], icon="💡")

            # 對話完成橫幅
            # 使用 [TEST_COMPLETE] 暗號判斷（由 AI 在最後回覆末尾觸發），比 q_idx 計數更可靠
            all_done = any(
                "[TEST_COMPLETE]" in m["content"]
                for m in chat_history
                if m["role"] == "assistant"
            )
            if all_done:
                st.markdown("""
                <div class="completion-banner">
                    <div style="font-size:1.8rem;">🏆</div>
                    <div style="font-size:1.1rem;font-weight:700;margin:0.5rem 0 0.3rem;">核心考題已全部通過！</div>
                    <div style="font-size:0.9rem;">繼續與客戶對話嘗試完成成交，或點擊下方按鈕進入報表台查看分析。</div>
                </div>
                """, unsafe_allow_html=True)

                # 迷你成績卡（報告已自動產生時顯示）
                if st.session_state.get("evaluation_report") and st.session_state.get("is_completed"):
                    _rpt     = st.session_state["evaluation_report"]
                    _score   = _rpt.get("score", 0)
                    _mode    = st.session_state.get("training_mode", "speed")
                    _color   = "#c9a227" if _score >= 80 else ("#6c6c6a" if _score >= 60 else "#a8a8a6")

                    if _mode == "speed":
                        _sub_line = "急速模式：僅評估話術能力，不評估成交"
                    else:
                        _closing  = _rpt.get("closing_result", "")
                        _sub_line = f"🤝 本次成交結果：{_closing}"

                    st.markdown(
                        f'<div style="background:rgba(255,255,255,0.05);border:2px solid {_color};'
                        f'border-radius:12px;padding:1.2rem;text-align:center;margin:1rem 0;">'
                        f'<div style="font-size:0.85rem;color:#adb5bd;margin-bottom:0.3rem;">你的成績</div>'
                        f'<div style="font-size:2.5rem;font-weight:800;color:{_color};">{_score} 分</div>'
                        f'<div style="font-size:0.9rem;margin-top:0.3rem;">{_sub_line}</div>'
                        f'<div style="font-size:0.8rem;color:#adb5bd;margin-top:0.5rem;">'
                        f'完整分析請切換到「模塊三：戰後報表台」'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )

                if st.session_state.get("is_completed") and st.session_state.get("evaluation_report"):
                    # 報告已產生過，直接提示切換查看，不重新評分
                    st.success("✅ 報告已產生，請切換到「模塊三：戰後報表台」查看完整分析。")
                elif st.button("📊 結束對話，查看報告", type="primary", key="end_session_btn"):
                    with st.spinner("🤖 AI 正在分析你的表現，請稍候..."):
                        try:
                            # 只評分到 [TEST_COMPLETE] 為止，不包含通關後的額外對話
                            _full_history = st.session_state.get("chat_history", [])
                            _cut_idx = None
                            for _i, _msg in enumerate(_full_history):
                                if "[TEST_COMPLETE]" in _msg.get("content", ""):
                                    _cut_idx = _i
                                    break
                            _eval_history = _full_history[:_cut_idx + 1] if _cut_idx is not None else _full_history

                            auto_report = get_evaluation_report(
                                chat_history        = _eval_history,
                                published_questions = published_questions,
                                customer_scenario   = st.session_state.get("customer_scenario", ""),
                                product_benefits    = st.session_state.get("product_benefits", ""),
                                training_mode       = st.session_state.get("training_mode", "speed")
                            )
                            st.session_state["evaluation_report"] = auto_report

                            # 自動儲存到 Supabase
                            try:
                                sb            = get_supabase()
                                company_id    = st.session_state.get("company_id", "")
                                employee_name = st.session_state.get("employee_name", "匿名員工")

                                if not company_id:
                                    st.session_state["_save_error"] = "無法取得公司 ID，報告未儲存"
                                else:
                                    # 步驟一：寫入 sessions（失敗不影響 scores）
                                    session_id = None
                                    try:
                                        session_result = sb.table("sessions").insert({
                                            "company_id":    company_id,
                                            "employee_name": employee_name,
                                            "is_completed":  True,
                                        }).execute()
                                        session_id = session_result.data[0]["id"] if session_result.data else None
                                    except Exception as e1:
                                        st.session_state["_save_error"] = f"sessions 寫入失敗：{e1}"

                                    # 步驟二：寫入 scores（不依賴 session_id，確保一定寫入）
                                    try:
                                        scores_payload = {
                                            "company_id":     company_id,
                                            "employee_name":  employee_name,
                                            "score":          auto_report.get("score", 0),
                                            "training_mode":  st.session_state.get("training_mode", "speed"),
                                            "left_brain_score":  auto_report.get("left_brain_score", 0),
                                            "right_brain_score": auto_report.get("right_brain_score", 0),
                                            "closing_score":     auto_report.get("closing_score", 0),
                                            "left_brain":     auto_report.get("left_brain", ""),
                                            "right_brain":    auto_report.get("right_brain", ""),
                                            "action_item":    auto_report.get("action_item", ""),
                                            "closing_result": auto_report.get("closing_result", ""),
                                            "strength":       auto_report.get("strength", ""),
                                            "improvement_tips": json.dumps(auto_report.get("improvement_tips", []), ensure_ascii=False),
                                            "practiced_questions": json.dumps(
                                                (
                                                    [
                                                        {"category": st.session_state.get("current_random_category", ""), "question": q}
                                                        for q in published_questions
                                                    ]
                                                    if st.session_state.get("question_mode", "🎯 主管精選模式") == "🎲 隨機挑戰模式"
                                                    else []
                                                ),
                                                ensure_ascii=False
                                            ),
                                        }
                                        if session_id:
                                            scores_payload["session_id"] = session_id
                                        sb.table("scores").insert(scores_payload).execute()
                                        st.session_state.pop("_save_error", None)
                                        print("[Supabase] scores 儲存成功")
                                    except Exception as e2:
                                        st.session_state["_save_error"] = f"scores 寫入失敗：{e2}"
                            except Exception as e:
                                st.session_state["_save_error"] = f"Supabase 連線失敗：{e}"

                            st.session_state["is_completed"] = True

                        except Exception as e:
                            st.error(f"報告產生失敗：{str(e)}")
                            st.session_state["is_completed"] = True

                    st.rerun()

        # ── 底部輸入區（語音 + 文字，全寬置底，通關後仍可繼續對話）──
        # 語音輸入區（固定在底部，文字輸入框正上方）
        voice_key   = st.session_state.get("voice_key_counter", 0)
        audio_value = st.audio_input(
            label="🎙️ 點擊說話",
            key=f"voice_recorder_{voice_key}",
        )
        if audio_value is not None:
            # 自動從已分析的 PDF 內容提取專有名詞提示
            _product  = st.session_state.get("product_name", "")
            _filename = st.session_state.get("analyzed_filename", "")
            _benefits = st.session_state.get("product_benefits", "")[:50]
            _hint = "，".join(filter(None, [_product, _filename, _benefits]))
            with st.spinner("🔄 辨識中..."):
                recognized = speech_to_text(audio_value, hint_text=_hint, product_name=_product)
            if recognized:
                st.session_state["pending_voice_text"] = recognized
                st.session_state["voice_key_counter"]  = voice_key + 1
                st.rerun()
            elif recognized is None:
                st.error("❌ 語音辨識失敗，請重新錄音或改用文字輸入")
            else:
                st.warning("⚠️ 無法辨識，請重試")

        user_input = st.chat_input(
            "繼續與客戶對話（嘗試成交）..."
            if all_done
            else (
                "輸入您的回應話術..."
                if current_q_idx > 0
                else "輸入您的開場白，例如：您好，我想跟您介紹一款對健康很有幫助的產品..."
            )
        )

        # 語音輸入與文字輸入合流：優先使用語音辨識結果
        voice_text = st.session_state.get("pending_voice_text")
        if voice_text:
            st.session_state["pending_voice_text"] = None
        effective_input = voice_text or user_input

        if effective_input:
            # 員工送出話術後先清除舊提示，讓教練在新一輪重新分析
            st.session_state["coach_hint"] = None

            # 急速模式計數器：記錄本題已收到幾次使用者回應
            st.session_state["q_turn_count"] = st.session_state.get("q_turn_count", 0) + 1

            # 步驟 1：將員工輸入加入對話記錄
            st.session_state["chat_history"].append(
                {"role": "user", "content": effective_input}
            )

            # 步驟 2：呼叫 Claude 取得客戶回應
            with st.spinner("🧑 客戶正在思考回覆..."):
                try:
                    ai_reply, new_idx = get_customer_response(
                        chat_history        = st.session_state["chat_history"],
                        published_questions = published_questions,
                        current_q_idx       = st.session_state["current_q_idx"],
                        analysis_context    = main_analysis,
                        product_name        = st.session_state.get("product_name", ""),
                        customer_scenario   = st.session_state.get("customer_scenario", ""),
                        training_mode       = st.session_state.get("training_mode", "speed")
                    )
                    # ── 急速模式補償：防止考題階段與結局階段無限循環 ──
                    if training_mode == "speed" and "[TEST_COMPLETE]" not in ai_reply:
                        _q_count = st.session_state.get("q_turn_count", 0)
                        _q_now   = st.session_state["current_q_idx"]
                        if (_q_now < total_q
                                and new_idx == _q_now
                                and _q_count >= 2):
                            # 考題階段：用戶已回答 2 次，強制推進到下一題
                            ai_reply = ai_reply.rstrip() + "[NEXT_Q]"
                            new_idx  = min(_q_now + 1, total_q)
                        elif (_q_now >= total_q
                                and _q_count >= 2):
                            # 結局階段：急速模式最多 2 輪成交談判，強制結束
                            ai_reply = ai_reply.rstrip() + "[TEST_COMPLETE]"
                except Exception as e:
                    ai_reply = f"（系統錯誤：{str(e)}，請重新整理後再試）"
                    new_idx  = st.session_state["current_q_idx"]

            # 步驟 3：將 AI 回覆加入記錄，並更新題目索引
            st.session_state["chat_history"].append(
                {"role": "assistant", "content": ai_reply}
            )
            # 題目推進時重置回合計數器
            if new_idx != st.session_state["current_q_idx"]:
                st.session_state["q_turn_count"] = 0
            st.session_state["current_q_idx"] = new_idx

            # 步驟 4：若教練輔助模式已開啟，自動在背景呼叫教練取得戰術提示
            # 教練提示絕不存入 chat_history，客戶 AI 完全看不到這段內容
            if coach_mode:
                try:
                    hint = get_coach_hint(
                        chat_history        = st.session_state["chat_history"],
                        published_questions = published_questions,
                        current_q_idx       = new_idx,
                        product_benefits    = st.session_state.get("product_benefits", ""),
                        target_audience     = st.session_state.get("target_audience",  "")
                    )
                    st.session_state["coach_hint"] = hint
                except Exception:
                    st.session_state["coach_hint"] = None
            else:
                st.session_state["coach_hint"] = None

            # 步驟 6：立即重新渲染，顯示 AI 文字回覆（語音按需點播）
            st.rerun()


# ╔══════════════════════════════════════════════╗
# ║  TAB 3：企業數據報表台 (Manager Analytics)    ║
# ╚══════════════════════════════════════════════╝
with tab3:
    st.markdown("## 📊 企業戰力分析報表")
    st.markdown("---")

    # 根據角色強制視角，不顯示 radio 切換元件
    if st.session_state.get("role") == "admin":
        view_mode = "🏢 管理者總覽（全隊成績）"
    else:
        view_mode = "📋 個人報告（本次訓練）"
    st.markdown("---")

    # ══════════════════════════════════════════════════
    # 視角一：管理者總覽
    # ══════════════════════════════════════════════════
    if view_mode == "🏢 管理者總覽（全隊成績）":
        try:
            from collections import defaultdict
            import pandas as pd

            sb = get_supabase()
            company_id = st.session_state.get("company_id", "")

            all_scores = sb.table("scores").select("*").eq(
                "company_id", company_id
            ).order("created_at", desc=True).execute()

            scores_data = all_scores.data if all_scores.data else []

            if not scores_data:
                st.info("📭 尚無訓練記錄。員工完成第一次訓練後，數據將自動出現在這裡。")
            else:
                # ── KPI 頂部指標 ──────────────────────────────
                st.markdown("### 📈 團隊訓練概覽")

                total_sessions = len(scores_data)
                avg_score      = sum(s.get("score", 0) for s in scores_data) / total_sessions

                # 每人取最新一次分數（scores_data 已按 created_at desc 排序）
                latest_by_employee = {}
                for s in scores_data:
                    name = s.get("employee_name", "匿名員工")
                    if name not in latest_by_employee:
                        latest_by_employee[name] = s.get("score", 0)
                latest_avg   = sum(latest_by_employee.values()) / len(latest_by_employee) if latest_by_employee else 0
                active_count = len(latest_by_employee)

                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("📚 總訓練次數", f"{total_sessions} 次")
                with col2:
                    st.metric("📊 團隊平均分", f"{avg_score:.1f} 分")
                with col3:
                    st.metric("📈 最新平均分", f"{latest_avg:.1f} 分",
                              help="每位員工取最新一次分數後平均，反映團隊現況而非歷史累積")
                with col4:
                    st.metric("👥 活躍員工數", f"{active_count} 人",
                              help="至少完成過一次訓練的員工人數")

                st.markdown("---")

                # ── 員工排行榜 ──────────────────────────────
                st.markdown("### 🏆 員工成績排行榜")

                employee_stats = defaultdict(lambda: {
                    "scores": [], "last_training": ""
                })
                for s in scores_data:
                    name  = s.get("employee_name", "匿名員工")
                    score = s.get("score", 0)
                    employee_stats[name]["scores"].append(score)
                    if s.get("created_at", "") > employee_stats[name]["last_training"]:
                        employee_stats[name]["last_training"] = s.get("created_at", "")[:10]

                leaderboard = []
                for name, stats in employee_stats.items():
                    sl = stats["scores"]
                    leaderboard.append({
                        "員工姓名":      name,
                        "最新分數":      sl[0],
                        "80分以上次數":  sum(1 for s in sl if s >= 80),
                        "訓練次數":      len(sl),
                        "最後訓練日期":  stats["last_training"],
                    })
                leaderboard.sort(key=lambda x: x["最新分數"], reverse=True)

                medals = ["🥇", "🥈", "🥉"]
                for i, row in enumerate(leaderboard):
                    row["名次"] = medals[i] if i < 3 else f"#{i+1}"

                df = pd.DataFrame(leaderboard)
                cols = ["名次", "員工姓名", "最新分數", "80分以上次數", "訓練次數", "最後訓練日期"]
                st.dataframe(df[cols], use_container_width=True, hide_index=True)

                # ── 主管刪除員工測試資料 ──────────────────────
                st.markdown("---")
                with st.expander("🗑️ 刪除員工資料（清除測試帳號用）"):
                    st.caption("⚠️ 此操作會永久刪除該員工所有訓練記錄，無法復原，僅建議用於清除測試資料。")
                    _del_target = st.selectbox(
                        "選擇要刪除的員工",
                        options=["（請選擇）"] + [row["員工姓名"] for row in leaderboard],
                        key="del_employee_select"
                    )
                    if _del_target != "（請選擇）":
                        _confirm_del = st.checkbox(f"我確認要永久刪除「{_del_target}」的所有訓練記錄", key="confirm_del_employee")
                        if st.button("🗑️ 確認刪除", type="primary", disabled=not _confirm_del):
                            try:
                                sb.table("scores").delete().eq(
                                    "company_id", company_id
                                ).eq(
                                    "employee_name", _del_target
                                ).execute()
                                sb.table("sessions").delete().eq(
                                    "company_id", company_id
                                ).eq(
                                    "employee_name", _del_target
                                ).execute()
                                st.success(f"✅ 已刪除「{_del_target}」的所有訓練記錄")
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ 刪除失敗：{str(e)}")

                st.markdown("<br>", unsafe_allow_html=True)
                selected_employee = st.selectbox(
                    "👤 查看員工詳細訓練紀錄",
                    options=["（請選擇）"] + [row["員工姓名"] for row in leaderboard]
                )

                if selected_employee != "（請選擇）":
                    emp_records = [
                        s for s in scores_data
                        if s.get("employee_name") == selected_employee
                    ]

                    def _parse_tips(raw_tips):
                        """improvement_tips 存進 Supabase 時是 json.dumps 過的字串，
                        這裡統一還原成 list，避免對字串做逐字元迴圈。"""
                        if isinstance(raw_tips, list):
                            return raw_tips
                        if isinstance(raw_tips, str) and raw_tips.strip():
                            try:
                                parsed = json.loads(raw_tips)
                                return parsed if isinstance(parsed, list) else []
                            except (json.JSONDecodeError, ValueError):
                                return []
                        return []

                    if emp_records:
                        st.markdown(f"### 📋 {selected_employee} 的訓練紀錄（共 {len(emp_records)} 次）")

                        for idx, rec in enumerate(emp_records):
                            rec_score = rec.get("score", 0)
                            rec_color = "#c9a227" if rec_score >= 80 else ("#6c6c6a" if rec_score >= 60 else "#a8a8a6")
                            rec_date  = rec.get("created_at", "")[:16].replace("T", " ")
                            header_label = f"{'📌 最近一次' if idx == 0 else '📅 ' + rec_date} ・ {rec_score} 分"

                            with st.expander(header_label, expanded=(idx == 0)):
                                col_a, col_b = st.columns(2)
                                with col_a:
                                    st.markdown(
                                        f'<div style="background:rgba(255,255,255,0.05);border-radius:12px;'
                                        f'padding:1.2rem;text-align:center;">'
                                        f'<div style="font-size:0.85rem;color:#adb5bd;">綜合分數</div>'
                                        f'<div style="font-size:2.5rem;font-weight:700;color:{rec_color};">{rec_score}</div>'
                                        f'<div style="font-size:0.85rem;color:#adb5bd;">{rec.get("closing_result","")}</div>'
                                        f'</div>',
                                        unsafe_allow_html=True
                                    )
                                with col_b:
                                    strength = rec.get("strength", "")
                                    if strength:
                                        st.markdown(
                                            f'<div style="background:rgba(201,162,39,0.1);border-left:4px solid #c9a227;'
                                            f'border-radius:0;padding:1rem 1.2rem;">'
                                            f'<div style="font-size:0.8rem;color:#c9a227;font-weight:700;">✨ 本次表現亮點</div>'
                                            f'<div style="font-size:0.9rem;color:#e9ecef;margin-top:0.3rem;">{strength}</div>'
                                            f'</div>',
                                            unsafe_allow_html=True
                                        )

                                tips = _parse_tips(rec.get("improvement_tips", []))
                                if tips:
                                    st.markdown("**🎯 下次練習重點**")
                                    for i, tip in enumerate(tips, 1):
                                        st.markdown(
                                            f'<div style="background:rgba(255,255,255,0.04);border-left:4px solid #8a8a88;'
                                            f'border-radius:0;padding:0.8rem 1.2rem;margin-bottom:0.5rem;">'
                                            f'<span style="font-size:0.8rem;color:#c4c4c2;">改善點 {i}</span><br>'
                                            f'<span style="font-size:0.9rem;color:#e9ecef;">{tip}</span>'
                                            f'</div>',
                                            unsafe_allow_html=True
                                        )

                                col_lb, col_rb = st.columns(2)
                                with col_lb:
                                    st.markdown(
                                        f'<div style="background:rgba(255,255,255,0.04);border-left:4px solid #8a8a88;'
                                        f'border-radius:0;padding:1rem 1.2rem;">'
                                        f'<div style="font-size:0.8rem;color:#c4c4c2;font-weight:700;">左腦分析</div>'
                                        f'<div style="font-size:0.88rem;color:#e9ecef;margin-top:0.3rem;">'
                                        f'{rec.get("left_brain","—")}</div></div>',
                                        unsafe_allow_html=True
                                    )
                                with col_rb:
                                    st.markdown(
                                        f'<div style="background:rgba(255,255,255,0.04);border-left:4px solid #6c6c6a;'
                                        f'border-radius:0;padding:1rem 1.2rem;">'
                                        f'<div style="font-size:0.8rem;color:#a8a8a6;font-weight:700;">右腦分析</div>'
                                        f'<div style="font-size:0.88rem;color:#e9ecef;margin-top:0.3rem;">'
                                        f'{rec.get("right_brain","—")}</div></div>',
                                        unsafe_allow_html=True
                                    )

                                st.markdown(f"**💡 培訓建議：** {rec.get('action_item','—')}")

                st.markdown("---")
                st.markdown("### 🚨 需要主管關注")

                from datetime import datetime, timedelta

                # 計算行動信號
                alert_no_practice = []   # 超過 7 天沒練習
                alert_declining   = []   # 最新分數低於個人平均
                alert_low_score   = []   # 平均分持續低於 60

                today = datetime.utcnow()

                # 依員工分組完整紀錄（含左右腦分數與改善建議），供下滑警示使用
                emp_full_records = defaultdict(list)
                for s in scores_data:
                    emp_full_records[s.get("employee_name", "匿名員工")].append(s)

                def _rec_mode(s):
                    if s.get("training_mode"):
                        return s["training_mode"]
                    return "deep" if s.get("closing_result", "") in ("當場成交", "有條件延遲", "拒絕成交") else "speed"

                def _parse_tips_for_alert(raw_tips):
                    if isinstance(raw_tips, list):
                        return raw_tips
                    if isinstance(raw_tips, str) and raw_tips.strip():
                        try:
                            parsed = json.loads(raw_tips)
                            return parsed if isinstance(parsed, list) else []
                        except (json.JSONDecodeError, ValueError):
                            return []
                    return []

                for name, stats in employee_stats.items():
                    scores_list   = stats["scores"]
                    last_date_str = stats["last_training"]

                    # 超過 7 天沒練習
                    if last_date_str:
                        try:
                            last_date = datetime.strptime(last_date_str, "%Y-%m-%d")
                            if (today - last_date).days >= 7:
                                alert_no_practice.append(f"{name}（最後練習：{last_date_str}）")
                        except Exception:
                            pass

                    # 最新分數低於「先前」平均（排除最新這筆，避免被自己稀釋掉下滑幅度）
                    if len(scores_list) >= 2:
                        latest_score    = scores_list[0]  # scores_data 已按時間降序排列
                        previous_scores = scores_list[1:]
                        previous_avg    = sum(previous_scores) / len(previous_scores)
                        if latest_score < previous_avg - 5:
                            latest_rec = emp_full_records[name][0]

                            # 找同模式的先前紀錄，比較左右腦哪邊掉比較多（換算百分比才能公平比較）
                            same_mode_prev = [
                                r for r in emp_full_records[name][1:]
                                if _rec_mode(r) == _rec_mode(latest_rec)
                            ]
                            dim_note = ""
                            if same_mode_prev:
                                _max   = 35 if _rec_mode(latest_rec) == "deep" else 50
                                l_prev = sum(r.get("left_brain_score", 0)  for r in same_mode_prev) / len(same_mode_prev)
                                r_prev = sum(r.get("right_brain_score", 0) for r in same_mode_prev) / len(same_mode_prev)
                                l_drop = (l_prev - latest_rec.get("left_brain_score", 0))  / _max * 100
                                r_drop = (r_prev - latest_rec.get("right_brain_score", 0)) / _max * 100
                                if max(l_drop, r_drop) >= 10:
                                    dim_note = "，左腦下滑較明顯" if l_drop > r_drop else "，右腦下滑較明顯"

                            # 抓這次的第一條改善建議，讓主管一眼看到具體問題點
                            tips = _parse_tips_for_alert(latest_rec.get("improvement_tips", []))
                            issue_note = tips[0] if tips else ""

                            alert_declining.append({
                                "headline": f"{name}（最新 {latest_score} 分 vs 先前平均 {previous_avg:.0f} 分{dim_note}）",
                                "issue": issue_note,
                            })

                    # 持續低分
                    if len(scores_list) >= 2:
                        avg = sum(scores_list) / len(scores_list)
                        if avg < 60:
                            alert_low_score.append(f"{name}（平均 {avg:.0f} 分）")

                # 顯示行動信號
                has_alert = False

                if alert_no_practice:
                    has_alert = True
                    st.error("📵 **超過 7 天未練習**（建議主動聯繫）")
                    for a in alert_no_practice:
                        st.markdown(f"　・{a}")

                if alert_declining:
                    has_alert = True
                    st.warning("📉 **成績下滑中**（建議了解原因）")
                    for a in alert_declining:
                        st.markdown(f"　・{a['headline']}")
                        if a["issue"]:
                            st.markdown(f"　　　└ 這次問題：{a['issue']}")

                if alert_low_score:
                    has_alert = True
                    st.warning("⚠️ **持續低於 60 分**（建議安排一對一輔導）")
                    for a in alert_low_score:
                        st.markdown(f"　・{a}")

                if not has_alert:
                    st.success("✅ 目前沒有需要特別關注的成員，整體訓練狀況良好。")

                st.markdown("---")

                # ── 團隊指導建議（急速/深度模式分開呈現）──────
                st.markdown("### 🧭 團隊指導建議")

                # 判斷每筆記錄屬於哪個模式：優先讀取明確欄位 training_mode，
                # 若無此欄位（舊資料）則以 closing_result 反推，確保向下相容
                def _infer_mode(s):
                    if s.get("training_mode"):
                        return s["training_mode"]
                    return "deep" if s.get("closing_result", "") in ("當場成交", "有條件延遲", "拒絕成交") else "speed"

                speed_records = [s for s in scores_data if _infer_mode(s) == "speed"]
                deep_records  = [s for s in scores_data if _infer_mode(s) == "deep"]

                speed_count = len(speed_records)
                deep_count  = len(deep_records)

                speed_avg = (sum(s.get("score", 0) for s in speed_records) / speed_count) if speed_count else 0
                deep_avg  = (sum(s.get("score", 0) for s in deep_records) / deep_count) if deep_count else 0

                speed_pass_count = sum(1 for s in speed_records if s.get("score", 0) >= 70)
                speed_pass_rate  = (speed_pass_count / speed_count * 100) if speed_count else 0

                close_count  = sum(1 for s in deep_records if s.get("closing_result") == "當場成交")
                delay_count  = sum(1 for s in deep_records if s.get("closing_result") == "有條件延遲")
                reject_count = sum(1 for s in deep_records if s.get("closing_result") == "拒絕成交")
                close_rate   = (close_count / deep_count * 100) if deep_count else 0

                speed_left_avg  = (sum(s.get("left_brain_score", 0)  for s in speed_records) / speed_count) if speed_count else 0
                speed_right_avg = (sum(s.get("right_brain_score", 0) for s in speed_records) / speed_count) if speed_count else 0

                deep_left_avg    = (sum(s.get("left_brain_score", 0)  for s in deep_records) / deep_count) if deep_count else 0
                deep_right_avg   = (sum(s.get("right_brain_score", 0) for s in deep_records) / deep_count) if deep_count else 0
                deep_closing_avg = (sum(s.get("closing_score", 0)     for s in deep_records) / deep_count) if deep_count else 0

                # 三個維度滿分不同（左腦35/右腦35/成交30），換算成百分比才能公平比較誰偏弱
                deep_left_pct    = (deep_left_avg    / 35 * 100) if deep_count else 0
                deep_right_pct   = (deep_right_avg   / 35 * 100) if deep_count else 0
                deep_closing_pct = (deep_closing_avg / 30 * 100) if deep_count else 0

                # ── ⚡ 急速模式總結區塊 ────────────────────
                st.markdown(
                    '<div style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.12);'
                    'border-radius:12px;padding:1.2rem 1.4rem;margin-bottom:1rem;">'
                    '<div style="font-size:1rem;font-weight:700;color:#c4c4c2;margin-bottom:0.8rem;">'
                    '⚡ 急速模式總結</div>',
                    unsafe_allow_html=True
                )

                if speed_count == 0:
                    st.info("ℹ️ 尚無「急速模式」訓練記錄。")
                else:
                    col_s1, col_s2 = st.columns(2)
                    with col_s1:
                        st.metric("話術達標率", f"{speed_pass_rate:.0f}%", help="評分 ≥70 分視為話術達標")
                    with col_s2:
                        st.metric("平均分數", f"{speed_avg:.1f} 分")

                    _speed_gap = speed_right_avg - speed_left_avg  # 正值代表右腦較弱，負值代表左腦較弱

                    if speed_avg < 60:
                        _s_title, _s_content = "🔴 立即行動", "急速模式整體分數偏低，建議本週安排基礎銷售技巧培訓，重點訓練產品知識表達與開場白設計。"
                    elif _speed_gap <= -5:
                        _s_title, _s_content = "🟡 近期行動", (
                            f"團隊左腦邏輯（平均 {speed_left_avg:.0f}/50）明顯弱於右腦溝通"
                            f"（平均 {speed_right_avg:.0f}/50），建議加強產品知識背誦與賣點覆蓋率訓練。"
                        )
                    elif _speed_gap >= 5:
                        _s_title, _s_content = "🟡 近期行動", (
                            f"團隊右腦溝通（平均 {speed_right_avg:.0f}/50）明顯弱於左腦邏輯"
                            f"（平均 {speed_left_avg:.0f}/50），建議加強同理心表達與語氣訓練。"
                        )
                    elif speed_avg < 70:
                        _s_title, _s_content = "🟡 近期行動", "急速模式表現尚可但未達標，左右腦能力發展平均，建議安排異議處理專項訓練，加強回應客戶常見疑慮的話術。"
                    else:
                        _s_title, _s_content = "🟢 維持精進", "急速模式表現良好，左右腦能力發展平均，建議安排進階情境演練，針對高難度客戶類型進行專項訓練。"

                    st.markdown(
                        f'<div style="background:rgba(255,255,255,0.04);border-left:4px solid #8a8a88;'
                        f'border-radius:0;padding:1rem 1.4rem;margin-top:0.6rem;">'
                        f'<div style="font-size:0.85rem;font-weight:700;color:#c4c4c2;margin-bottom:0.4rem;">{_s_title}</div>'
                        f'<div style="font-size:0.92rem;line-height:1.7;color:#e9ecef;">{_s_content}</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )

                st.markdown('</div>', unsafe_allow_html=True)

                # ── 📊 分類訓練狀況總覽（急速模式）─────────────────
                st.markdown("### 📊 分類訓練狀況（急速模式）")

                speed_only_for_cat = [s for s in scores_data if _infer_mode(s) == "speed"]

                if not speed_only_for_cat:
                    st.info("ℹ️ 目前尚無「急速模式」的訓練記錄。")
                else:
                    emp_cat_stats: dict = {}
                    for s in speed_only_for_cat:
                        _name = s.get("employee_name", "匿名員工")
                        _score_v = s.get("score", 0)
                        _pq = s.get("practiced_questions") or []
                        _cats_in_session = set(
                            item.get("category", "") for item in _pq if item.get("category")
                        )
                        for _cat in _cats_in_session:
                            emp_cat_stats.setdefault(_name, {}).setdefault(_cat, {"scores": [], "count": 0})
                            emp_cat_stats[_name][_cat]["scores"].append(_score_v)
                            emp_cat_stats[_name][_cat]["count"] += 1

                    if not emp_cat_stats:
                        st.info("ℹ️ 目前的訓練記錄還沒有分類資訊（分類統計僅適用於「隨機挑戰模式」"
                                "累積的新資料，主管精選模式或更早期的舊資料不會有分類標記）。")
                    else:
                        _cat_keys = list(CATEGORY_LABELS.keys())
                        _cat_short_labels = {
                            k: (v.split("：")[-1] if "：" in v else v) for k, v in CATEGORY_LABELS.items()
                        }

                        _table_rows = []
                        for _name in sorted(emp_cat_stats.keys()):
                            _row = {"員工": _name}
                            for _cat in _cat_keys:
                                _stat = emp_cat_stats[_name].get(_cat)
                                if _stat and _stat["count"] > 0:
                                    _avg = sum(_stat["scores"]) / len(_stat["scores"])
                                    _row[_cat_short_labels[_cat]] = f"{_avg:.0f}分({_stat['count']}次)"
                                else:
                                    _row[_cat_short_labels[_cat]] = "—"
                            _table_rows.append(_row)

                        st.dataframe(pd.DataFrame(_table_rows).set_index("員工"), use_container_width=True)

                        st.markdown("<br>", unsafe_allow_html=True)

                        _selected_emp = st.selectbox(
                            "🔍 選擇員工查看詳細分類表現",
                            options=sorted(emp_cat_stats.keys()),
                            key="cat_detail_emp_select"
                        )
                        if _selected_emp:
                            _emp_stats = emp_cat_stats[_selected_emp]
                            _detail_rows = []
                            for _cat in _cat_keys:
                                _stat = _emp_stats.get(_cat)
                                if _stat and _stat["count"] > 0:
                                    _avg = sum(_stat["scores"]) / len(_stat["scores"])
                                    _detail_rows.append({
                                        "分類": CATEGORY_LABELS[_cat],
                                        "平均分數": f"{_avg:.0f} 分",
                                        "練習次數": _stat["count"],
                                    })
                                else:
                                    _detail_rows.append({
                                        "分類": CATEGORY_LABELS[_cat],
                                        "平均分數": "尚未練習",
                                        "練習次數": 0,
                                    })
                            st.dataframe(pd.DataFrame(_detail_rows), use_container_width=True, hide_index=True)

                            _practiced_cats = [r for r in _detail_rows if r["練習次數"] > 0]
                            if _practiced_cats:
                                _weakest_row = min(
                                    _practiced_cats,
                                    key=lambda r: float(r["平均分數"].replace(" 分", ""))
                                )
                                st.caption(
                                    f"💡 {_selected_emp} 目前表現最弱的分類："
                                    f"**{_weakest_row['分類']}**（{_weakest_row['平均分數']}）"
                                )

                # ── 🎯 深度模式分析區塊 ────────────────────
                st.markdown(
                    '<div style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.12);'
                    'border-radius:12px;padding:1.2rem 1.4rem;margin-bottom:1rem;">'
                    '<div style="font-size:1rem;font-weight:700;color:#c4c4c2;margin-bottom:0.8rem;">'
                    '🎯 深度模式分析</div>',
                    unsafe_allow_html=True
                )

                if deep_count == 0:
                    st.info("ℹ️ 目前尚無「深度模式」的訓練記錄，需要團隊完成深度模式訓練後才會顯示。")
                else:
                    col_d1, col_d2, col_d3, col_d4 = st.columns(4)
                    with col_d1:
                        st.metric("當場成交率", f"{close_rate:.0f}%")
                    with col_d2:
                        st.metric("延遲決策次數", f"{delay_count} 次")
                    with col_d3:
                        st.metric("拒絕成交次數", f"{reject_count} 次")
                    with col_d4:
                        st.metric("平均分數", f"{deep_avg:.1f} 分")

                    _deep_dims   = {"左腦邏輯": deep_left_pct, "右腦溝通": deep_right_pct, "成交能力": deep_closing_pct}
                    _weakest     = min(_deep_dims, key=_deep_dims.get)
                    _weakest_v   = _deep_dims[_weakest]
                    _strongest_v = max(_deep_dims.values())
                    _dim_advice  = {
                        "左腦邏輯": "產品知識背誦與賣點覆蓋率",
                        "右腦溝通": "同理心表達與語氣訓練",
                        "成交能力": "識別購買信號、主動創造成交條件的技巧",
                    }

                    if close_rate < 30:
                        _d_title, _d_content = "🔴 成交能力", (
                            f"當場成交率僅 {close_rate:.0f}%，建議重點加強促成技巧與現場引導決策的話術，"
                            f"可安排角色扮演練習「如何在客戶猶豫時推進成交」。"
                        )
                    elif _strongest_v - _weakest_v >= 10:
                        _d_title, _d_content = "🟡 促成技巧", (
                            f"團隊在「{_weakest}」這個維度相對偏弱（換算約 {_weakest_v:.0f}%，"
                            f"其他維度約 {_strongest_v:.0f}%），建議優先加強{_dim_advice[_weakest]}。"
                        )
                    elif delay_count > deep_count * 0.5:
                        _d_title, _d_content = "🟡 促成技巧", (
                            "超過一半的演練結果為「有條件延遲」，客戶傾向不當場決定。"
                            "建議加強緊迫感製造與即時解除疑慮的能力。"
                        )
                    else:
                        _d_title, _d_content = "🟢 維持精進", "深度模式成交表現穩定，三大維度發展平均，持續保持並可挑戰更高難度的客戶情境。"

                    st.markdown(
                        f'<div style="background:rgba(255,255,255,0.04);border-left:4px solid #8a8a88;'
                        f'border-radius:0;padding:1rem 1.4rem;margin-top:0.6rem;">'
                        f'<div style="font-size:0.85rem;font-weight:700;color:#c4c4c2;margin-bottom:0.4rem;">{_d_title}</div>'
                        f'<div style="font-size:0.92rem;line-height:1.7;color:#e9ecef;">{_d_content}</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )

                st.markdown('</div>', unsafe_allow_html=True)

                st.markdown("---")

                # ── 個別輔導提醒（跨模式，明確標示模式來源）─────
                st.markdown("<br>", unsafe_allow_html=True)
                _low_flags, _seen = [], set()
                for s in scores_data:
                    if s.get("score", 0) < 60:
                        name = s.get("employee_name", "匿名員工")
                        if name in _seen:
                            continue
                        _seen.add(name)
                        mode_label = "急速模式" if _infer_mode(s) == "speed" else "深度模式"
                        _low_flags.append(f"{name}（{mode_label} {s.get('score', 0)} 分）")
                        if len(_low_flags) >= 3:
                            break

                if _low_flags:
                    st.markdown(
                        f'<div style="background:rgba(255,255,255,0.04);border-left:4px solid #8a8a88;'
                        f'border-radius:0;padding:1rem 1.4rem;">'
                        f'<div style="font-size:0.85rem;font-weight:700;color:#c4c4c2;margin-bottom:0.4rem;">🔴 個別輔導</div>'
                        f'<div style="font-size:0.92rem;line-height:1.7;color:#e9ecef;">'
                        f'以下成員有低於 60 分的訓練記錄，建議安排一對一輔導：{"、".join(_low_flags)}</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )

                st.markdown("---")

                # ── 最近訓練記錄 ──────────────────────────
                st.markdown("### 📅 最近訓練記錄")

                for s in scores_data[:10]:
                    score_v  = s.get("score", 0)
                    name     = s.get("employee_name", "匿名員工")
                    date_str = s.get("created_at", "")[:16].replace("T", " ")
                    closing  = s.get("closing_result", "")
                    color    = "#c9a227" if score_v >= 80 else ("#6c6c6a" if score_v >= 60 else "#a8a8a6")
                    st.markdown(
                        f'<div style="background:rgba(255,255,255,0.04);border-left:4px solid {color};'
                        f'border-radius:0 8px 8px 0;padding:0.6rem 1rem;margin-bottom:0.5rem;">'
                        f'<span style="font-weight:700;">{name}</span>'
                        f'<span style="float:right;color:{color};font-weight:700;">{score_v} 分</span><br>'
                        f'<span style="font-size:0.82rem;color:#adb5bd;">{date_str}</span>'
                        f'{f" ・ {closing}" if closing else ""}'
                        f'</div>',
                        unsafe_allow_html=True
                    )

        except Exception as e:
            st.error(f"載入管理報表失敗：{str(e)}")
            st.info("請確認 Supabase 連線正常。")

    # ══════════════════════════════════════════════════
    # 視角二：個人報告（本次訓練）
    # ══════════════════════════════════════════════════
    else:
        if st.session_state.get("_save_error"):
            st.warning(f"⚠️ 儲存失敗（請截圖回報）：{st.session_state['_save_error']}")

        is_completed = st.session_state.get("is_completed", False)
        chat_history_for_report = st.session_state.get("chat_history", [])
        published_questions_for_report = (
            st.session_state.get("published_questions")
            or st.session_state.get("current_random_questions", [])
        )

        if not is_completed or not chat_history_for_report:
            st.markdown("""
            <div style="text-align:center;color:#adb5bd;padding:5rem 2rem;">
                <div style="font-size:3.5rem;">📋</div>
                <div style="margin-top:1rem;font-size:1.1rem;font-weight:600;color:#ced4da;">
                    尚無可分析的對話紀錄
                </div>
                <div style="font-size:0.92rem;margin-top:0.6rem;">
                    請先至 <strong>🎮 模塊二：實戰沙盒</strong> 完成通關演練，<br>
                    點擊「📊 結束對話，查看報告」後即可返回此頁查看戰後分析報告。
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            # ── 讀取快取報告（由 Tab 2「結束對話」按鈕自動產生並存入）──
            cached_report = st.session_state.get("evaluation_report")

            st.caption(
                f"✅ 演練已完成｜對話共 {len(chat_history_for_report)} 則｜"
                f"考題 {len(published_questions_for_report)} 道"
            )

            if not cached_report:
                st.info("尚無報告。請先完成模塊二的訓練並點擊「📊 結束對話，查看報告」。")

            # ── Dashboard 視覺呈現 ────────────────────────────
            if cached_report:
                score          = cached_report.get("score", 0)
                left_brain     = cached_report.get("left_brain", "—")
                right_brain    = cached_report.get("right_brain", "—")
                action_item    = cached_report.get("action_item", "—")
                closing_result = cached_report.get("closing_result", "未完成成交環節")

                st.markdown("---")

                # ── 頂部核心指標 ──────────────────────────────
                st.markdown("### 🏅 核心指標")

                _current_mode = st.session_state.get("training_mode", "speed")

                if _current_mode == "speed":
                    # 急速模式：只顯示分數，不評成交與獎金
                    col_score, col_note = st.columns([1, 2])
                    with col_score:
                        delta_label = "🔥 高表現" if score >= 80 else ("📈 需加強" if score >= 60 else "⚠️ 需培訓")
                        st.metric(
                            label="📊 綜合戰力分數",
                            value=f"{score} 分",
                            delta=delta_label,
                            delta_color="off"
                        )
                        if score >= 80:
                            st.markdown(
                                '<div style="border:1px solid #c9a227; border-radius:8px; '
                                'padding:0.4rem 0.8rem; text-align:center; color:#c9a227; '
                                'font-size:0.85rem; font-weight:700; margin-top:-0.5rem;">'
                                '✨ 表現亮眼，繼續保持！</div>',
                                unsafe_allow_html=True
                            )
                    with col_note:
                        st.info("⚡ 急速模式僅評估左腦邏輯與右腦溝通，不評估成交結果。", icon="ℹ️")
                else:
                    # 深度模式：分數 + 成交結果
                    col_score, col_closing = st.columns(2)
                    with col_score:
                        delta_label = "🔥 高表現" if score >= 80 else ("📈 需加強" if score >= 60 else "⚠️ 需培訓")
                        st.metric(
                            label="📊 綜合戰力分數",
                            value=f"{score} 分",
                            delta=delta_label,
                            delta_color="off"
                        )
                        if score >= 80:
                            st.markdown(
                                '<div style="border:1px solid #c9a227; border-radius:8px; '
                                'padding:0.4rem 0.8rem; text-align:center; color:#c9a227; '
                                'font-size:0.85rem; font-weight:700; margin-top:-0.5rem;">'
                                '✨ 表現亮眼，繼續保持！</div>',
                                unsafe_allow_html=True
                            )
                    with col_closing:
                        closing_icon = (
                            "✅" if closing_result == "當場成交"
                            else "⏳" if closing_result == "有條件延遲"
                            else "❌"
                        )
                        st.metric(
                            label="🤝 本次成交結果",
                            value=f"{closing_icon} {closing_result}"
                        )

                st.markdown("<br>", unsafe_allow_html=True)

                # 分數進度條 — 高分金色，其餘灰階深淺表現
                bar_color = "#c9a227" if score >= 80 else ("#6c6c6a" if score >= 60 else "#a8a8a6")
                st.markdown(
                    f'<div style="background:#2d2d3a;border-radius:8px;height:12px;overflow:hidden;">'
                    f'<div style="width:{score}%;height:100%;background:{bar_color};'
                    f'border-radius:8px;transition:width 0.5s;"></div></div>'
                    f'<div style="text-align:right;font-size:0.75rem;color:#adb5bd;margin-top:4px;">'
                    f'{score}/100</div>',
                    unsafe_allow_html=True
                )

                st.markdown("---")

                # ── 雙腦雷達剖析 ─────────────────────────────
                st.markdown("### 🧠 雙腦能力剖析")
                col_left, col_right = st.columns(2)

                with col_left:
                    st.markdown(
                        '<div style="background:rgba(255,255,255,0.04);border-left:4px solid #8a8a88;'
                        'border-radius:0;padding:1.2rem 1.4rem;">'
                        '<div style="font-size:1rem;font-weight:700;color:#c4c4c2;margin-bottom:0.6rem;">'
                        '左腦：邏輯 & 賣點掌握</div>'
                        f'<div style="font-size:0.93rem;line-height:1.7;color:#e9ecef;">{left_brain}</div>'
                        '</div>',
                        unsafe_allow_html=True
                    )

                with col_right:
                    st.markdown(
                        '<div style="background:rgba(255,255,255,0.04);border-left:4px solid #6c6c6a;'
                        'border-radius:0;padding:1.2rem 1.4rem;">'
                        '<div style="font-size:1rem;font-weight:700;color:#a8a8a6;margin-bottom:0.6rem;">'
                        '右腦：同理心 & 溝通溫度</div>'
                        f'<div style="font-size:0.93rem;line-height:1.7;color:#e9ecef;">{right_brain}</div>'
                        '</div>',
                        unsafe_allow_html=True
                    )

                st.markdown("<br>", unsafe_allow_html=True)

                # ── 總監裁示 ──────────────────────────────────
                st.markdown("### 📋 總監裁示")
                st.info(f"**下一步培訓建議：** {action_item}", icon="💡")

                st.markdown("---")

                # ── 本次亮點 ──────────────────────────────────
                strength = cached_report.get("strength", "")
                if strength:
                    st.markdown("### ✨ 本次表現亮點")
                    st.markdown(
                        f'<div style="background:rgba(201,162,39,0.1);border-left:4px solid #c9a227;'
                        f'border-radius:0;padding:1.2rem 1.4rem;margin-bottom:1rem;">'
                        f'<div style="font-size:0.95rem;line-height:1.7;color:#e9ecef;">👍 {strength}</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )

                # ── 具體改善建議 ───────────────────────────────
                improvement_tips = cached_report.get("improvement_tips", [])
                if improvement_tips:
                    st.markdown("### 🎯 下次練習重點")
                    for i, tip in enumerate(improvement_tips, 1):
                        st.markdown(
                            f'<div style="background:rgba(255,255,255,0.04);border-left:4px solid #8a8a88;'
                            f'border-radius:0;padding:1rem 1.4rem;margin-bottom:0.6rem;">'
                            f'<div style="font-size:0.85rem;font-weight:700;color:#c4c4c2;margin-bottom:0.3rem;">'
                            f'改善點 {i}</div>'
                            f'<div style="font-size:0.93rem;line-height:1.7;color:#e9ecef;">{tip}</div>'
                            f'</div>',
                            unsafe_allow_html=True
                        )

                st.markdown("---")

                # ── 對話紀錄回放 ──────────────────────────────
                with st.expander("📝 查看完整實戰文字紀錄（供主管覆核）"):
                    st.caption(f"本次演練共 {len(chat_history_for_report)} 則對話")
                    for turn_i, msg in enumerate(chat_history_for_report, 1):
                        role_label    = "🧑‍💼 業務員" if msg["role"] == "user" else "🧑 AI 客戶"
                        clean_content = msg["content"].replace("[TEST_COMPLETE]", "").strip()
                        bg = "rgba(255,255,255,0.04)" if msg["role"] == "user" else "rgba(138,138,136,0.08)"
                        st.markdown(
                            f'<div style="background:{bg};border-radius:8px;padding:0.75rem 1rem;'
                            f'margin-bottom:0.5rem;">'
                            f'<span style="font-size:0.78rem;font-weight:700;color:#adb5bd;">'
                            f'{role_label}（第 {turn_i} 則）</span><br>'
                            f'<span style="font-size:0.93rem;line-height:1.6;">{clean_content}</span>'
                            f'</div>',
                            unsafe_allow_html=True
                        )

                # 原始 JSON（debug 用，平時收起）
                if cached_report.get("_raw"):
                    with st.expander("🔧 原始 AI 回傳文字（解析失敗時供除錯）"):
                        st.code(cached_report["_raw"])


# ──────────────────────────────────────────────
# 頁面底部
# ──────────────────────────────────────────────
st.markdown("---")
st.markdown(
    '<div style="text-align:center;color:#adb5bd;font-size:0.8rem;">'
    'PitchCoach 企業中控台'
    '</div>',
    unsafe_allow_html=True
)