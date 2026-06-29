# ============================================================
# PitchCoach 企業中控台 v3
# ============================================================

import streamlit as st
import streamlit.components.v1 as components
import re
import json
import random
import os
from dotenv import load_dotenv
load_dotenv()

from config import *
from database import get_supabase, load_settings, get_or_create_company, save_settings
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

# ── 登入 Gate ──────────────────────────────────
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
    st.session_state["role"] = None
    st.session_state["current_company"] = ""

if not st.session_state["authenticated"]:
    st.title("🎯 PitchCoach 登入")
    role = st.radio("請選擇身份", ["🏢 主管（Admin）", "👤 員工（Employee）"])
    company_input = st.text_input("公司名稱", placeholder="例如：智云健康股份有限公司")

    if role == "🏢 主管（Admin）":
        pwd = st.text_input("管理員密碼", type="password")
        if st.button("登入"):
            _admin_pwd = st.secrets.get("ADMIN_PASSWORD", ADMIN_PASSWORD)
            if pwd == _admin_pwd and company_input.strip():
                st.session_state["authenticated"] = True
                st.session_state["role"] = "admin"
                st.session_state["current_company"] = company_input.strip()
                st.rerun()
            else:
                st.error("密碼錯誤或公司名稱為空")
    else:
        name_input   = st.text_input("您的姓名")
        invite_input = st.text_input("員工邀請碼", type="password")
        if st.button("進入訓練"):
            _invite_code = st.secrets.get("EMPLOYEE_CODE",
                           os.environ.get("EMPLOYEE_CODE", "employee123"))
            if not company_input.strip() or not name_input.strip():
                st.error("請填寫公司名稱與姓名")
            elif invite_input != _invite_code:
                st.error("❌ 邀請碼錯誤，請向主管確認")
            else:
                st.session_state["authenticated"] = True
                st.session_state["role"] = "employee"
                st.session_state["current_company"] = company_input.strip()
                st.session_state["employee_name"] = name_input.strip()
                st.rerun()
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
    .main { background-color: #f8f9fa; }

    /* 品牌標題 */
    .hero-title { font-size:2.2rem; font-weight:800; color:#1a1a2e; margin-bottom:0.2rem; }
    .hero-subtitle { font-size:1rem; color:#6c757d; margin-bottom:1.5rem; }

    /* 任務簡報橫幅 */
    .mission-banner {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        color: white;
        border-radius: 12px;
        padding: 1.4rem 2rem;
        margin-bottom: 1.5rem;
    }
    .mission-banner h3 { color: #ffd700; margin-bottom: 0.4rem; font-size: 1.1rem; }
    .mission-banner p  { margin: 0; font-size: 0.95rem; line-height: 1.7; }

    /* 鎖定提示 */
    .locked-box {
        background: #f1f3f5;
        border: 2px dashed #ced4da;
        border-radius: 12px;
        text-align: center;
        padding: 4rem 2rem;
        color: #868e96;
    }

    /* 進度條外框 */
    .progress-label { font-size:0.85rem; color:#495057; margin-bottom:0.3rem; }

    /* 考題卡片 */
    .question-card {
        background:white; border-radius:10px; padding:1rem 1.2rem;
        border:1.5px solid #e9ecef; margin-bottom:0.8rem;
    }
    .question-card-selected {
        background:#f0f7ff; border-radius:10px; padding:1rem 1.2rem;
        border:1.5px solid #4361ee; margin-bottom:0.8rem;
    }
    .selection-hint {
        background:#fff8e1; border-left:4px solid #ffc107;
        border-radius:6px; padding:0.7rem 1rem; margin-bottom:1rem;
        font-size:0.92rem; color:#5d4037;
    }

    /* 對話完成橫幅 */
    .completion-banner {
        background: linear-gradient(135deg, #0f5132, #198754);
        color: white; border-radius: 12px;
        padding: 1.2rem 1.8rem; margin: 1rem 0; text-align: center;
    }

    .upload-hint { font-size:0.88rem; color:#868e96; margin-top:0.4rem; }
    hr { border-color:#dee2e6; }
    footer { visibility:hidden; }
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
    st.markdown("### 🎯 PitchCoach")
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
        st.markdown("### 📂 上傳教材文件")
        uploaded_file = st.file_uploader(
            label="選擇 PDF 檔案",
            type=["pdf"],
            help="支援標準 PDF 格式。掃描版圖片 PDF 可能無法萃取文字。",
            label_visibility="collapsed"
        )
        st.markdown('<p class="upload-hint">📎 支援格式：PDF　｜　建議大小：10MB 以內</p>', unsafe_allow_html=True)
        st.markdown("---")
        st.markdown("#### 🎯 出題模式設定")

        mode = st.radio(
            label="選擇出題方式",
            options=["🎯 主管精選模式", "🎲 隨機挑戰模式"],
            key="question_mode",
            horizontal=True,
            help="主管精選：手動勾選2題｜隨機挑戰：系統每次自動隨機抽2題"
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
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("💾 儲存企業專屬設定", use_container_width=True):
                save_settings()
                st.success("✅ 設定已寫入 company_settings.json，重整頁面後自動還原！")
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
                        # 第二階段：按 5 大類別各生成 5 道題，共 25 道（三層防護解析）
                        questions_dict = generate_questions_json(document_text)

                        # 合併為扁平 list，供現有的 UI/邏輯向下相容
                        all_questions: list[str] = []
                        for cat_questions in questions_dict.values():
                            all_questions.extend(cat_questions)

                        st.session_state["main_analysis"]        = main_analysis
                        st.session_state["questions"]            = all_questions
                        st.session_state["questions_by_category"] = questions_dict
                        st.session_state["analyzed_filename"] = uploaded_file.name
                        st.session_state["task_published"]    = False

                        # 動態提取各章節，供客戶 AI 與教練 AI 動態注入
                        # 以 emoji 為錨點，不依賴中文標題，對格式變動有高容錯率
                        raw_name     = extract_section(main_analysis, "🏷️")
                        # 產品名稱只取第一行（避免 Claude 多說描述文字），並清理 Markdown 符號
                        product_name = re.sub(r'[*_#`]', '', raw_name.split("\n")[0]).strip()
                        st.session_state["product_name"]     = product_name
                        st.session_state["product_benefits"] = extract_section(main_analysis, "📌")
                        st.session_state["target_audience"]  = extract_section(main_analysis, "🎯")

                        # 清除舊的考題 widget 狀態
                        for i in range(15):
                            st.session_state.pop(f"q_text_{i}", None)
                            st.session_state.pop(f"q_check_{i}", None)
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

            st.caption(f"📄 分析來源：{st.session_state.get('analyzed_filename', '上傳的文件')}")

            # 三大重點展示
            st.markdown("### 📊 三大分析重點")
            with st.container(border=True):
                st.markdown(st.session_state["main_analysis"])

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
                        # ── 有分類資料：按 5 大類別用 expander 展開顯示 ──
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
                    questions_by_category = st.session_state.get("questions_by_category", {})

                    if questions_by_category:
                        # ── 有分類資料：按類別摘要顯示（可折疊）────────────
                        total_count = sum(len(qs) for qs in questions_by_category.values())
                        st.success(
                            f"✅ 題庫共 {total_count} 題，分為 5 大類別，"
                            f"系統每次從不同類別各抽 1 題（共 2 題）。"
                        )
                        cat_labels_short = {
                            "cat_1_product":     "🔍 產品理解類",
                            "cat_2_price":       "💰 價格異議類",
                            "cat_3_trust":       "🛡️ 信任疑慮類",
                            "cat_4_competition": "⚔️ 競品比較類",
                            "cat_5_decision":    "🚪 決策障礙類",
                        }
                        for cat_key, cat_label in cat_labels_short.items():
                            cat_qs = questions_by_category.get(cat_key, [])
                            if not cat_qs:
                                continue
                            with st.expander(f"{cat_label}（{len(cat_qs)} 題）", expanded=False):
                                for q in cat_qs:
                                    st.markdown(f"• {q[:100]}{'...' if len(q) > 100 else ''}")
                    else:
                        # ── 舊版相容：無分類資料，顯示扁平列表 ────────────
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
            # 隨機挑戰模式：每次新對話開始時跨類別各抽 1 題，共抽 2 題
            if not st.session_state.get("chat_history"):
                questions_by_category = st.session_state.get("questions_by_category", {})

                if questions_by_category:
                    # 從有題目的類別中隨機選 2 個不同類別，各抽 1 題
                    available_cats = [
                        cat for cat, qs in questions_by_category.items() if qs
                    ]
                    if len(available_cats) >= 2:
                        selected_cats = random.sample(available_cats, 2)
                        randomly_selected = [
                            random.choice(questions_by_category[cat])
                            for cat in selected_cats
                        ]
                    else:
                        # 類別不足時，從所有題目中隨機抽 2 題（舊版相容）
                        all_q = st.session_state.get("questions", [])
                        randomly_selected = random.sample(all_q, min(2, len(all_q)))
                else:
                    # 無分類資料（舊版）：從扁平列表中隨機抽 2 題
                    all_q = st.session_state.get("questions", [])
                    randomly_selected = random.sample(all_q, min(2, len(all_q)))

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
                    _bonus   = _rpt.get("bonus_unlocked", False)
                    _closing = _rpt.get("closing_result", "")
                    _color   = "#28a745" if _score >= 80 else ("#ffc107" if _score >= 60 else "#dc3545")
                    st.markdown(
                        f'<div style="background:rgba(255,255,255,0.05);border:2px solid {_color};'
                        f'border-radius:12px;padding:1.2rem;text-align:center;margin:1rem 0;">'
                        f'<div style="font-size:0.85rem;color:#adb5bd;margin-bottom:0.3rem;">你的成績</div>'
                        f'<div style="font-size:2.5rem;font-weight:800;color:{_color};">{_score} 分</div>'
                        f'<div style="font-size:0.9rem;margin-top:0.3rem;">'
                        f'{"🏅 獎金門檻達標！" if _bonus else "未達獎金門檻"} ・ {_closing}'
                        f'</div>'
                        f'<div style="font-size:0.8rem;color:#adb5bd;margin-top:0.5rem;">'
                        f'完整分析請切換到「模塊三：戰後報表台」'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True
                    )

                if st.button("📊 結束對話，查看報告", type="primary", key="end_session_btn"):
                    with st.spinner("🤖 AI 正在分析你的表現，請稍候..."):
                        try:
                            auto_report = get_evaluation_report(
                                chat_history        = st.session_state.get("chat_history", []),
                                published_questions = published_questions,
                                customer_scenario   = st.session_state.get("customer_scenario", ""),
                                product_benefits    = st.session_state.get("product_benefits", ""),
                                training_mode       = st.session_state.get("training_mode", "speed")
                            )
                            st.session_state["evaluation_report"] = auto_report

                            # 自動儲存到 Supabase
                            try:
                                sb            = get_supabase()
                                company_id    = get_or_create_company(st.session_state.get("current_company", ""))
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
                                            "bonus_unlocked": auto_report.get("bonus_unlocked", False),
                                            "left_brain":     auto_report.get("left_brain", ""),
                                            "right_brain":    auto_report.get("right_brain", ""),
                                            "action_item":    auto_report.get("action_item", ""),
                                            "closing_result": auto_report.get("closing_result", ""),
                                            "strength":       auto_report.get("strength", ""),
                                            "improvement_tips": json.dumps(auto_report.get("improvement_tips", []), ensure_ascii=False),
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
                recognized = speech_to_text(audio_value, hint_text=_hint)
            if recognized:
                st.session_state["pending_voice_text"] = recognized
                st.session_state["voice_key_counter"]  = voice_key + 1
                st.rerun()
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
            company_id = get_or_create_company(st.session_state.get("current_company", ""))

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
                    "scores": [], "bonus_count": 0, "last_training": ""
                })
                for s in scores_data:
                    name  = s.get("employee_name", "匿名員工")
                    score = s.get("score", 0)
                    employee_stats[name]["scores"].append(score)
                    if s.get("bonus_unlocked"):
                        employee_stats[name]["bonus_count"] += 1
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

                    if emp_records:
                        latest = emp_records[0]
                        st.markdown(f"### 📋 {selected_employee} 的最近一次訓練")

                        col_a, col_b = st.columns(2)
                        with col_a:
                            score_v = latest.get("score", 0)
                            color = "#28a745" if score_v >= 80 else ("#ffc107" if score_v >= 60 else "#dc3545")
                            st.markdown(
                                f'<div style="background:rgba(255,255,255,0.05);border-radius:12px;'
                                f'padding:1.2rem;text-align:center;">'
                                f'<div style="font-size:0.85rem;color:#adb5bd;">綜合分數</div>'
                                f'<div style="font-size:2.5rem;font-weight:700;color:{color};">{score_v}</div>'
                                f'<div style="font-size:0.85rem;color:#adb5bd;">{latest.get("closing_result","")}</div>'
                                f'</div>',
                                unsafe_allow_html=True
                            )
                        with col_b:
                            strength = latest.get("strength", "")
                            if strength:
                                st.markdown(
                                    f'<div style="background:rgba(40,167,69,0.1);border-left:4px solid #28a745;'
                                    f'border-radius:0 12px 12px 0;padding:1rem 1.2rem;">'
                                    f'<div style="font-size:0.8rem;color:#28a745;font-weight:700;">✨ 本次亮點</div>'
                                    f'<div style="font-size:0.9rem;color:#e9ecef;margin-top:0.3rem;">{strength}</div>'
                                    f'</div>',
                                    unsafe_allow_html=True
                                )

                        tips = latest.get("improvement_tips", [])
                        if tips:
                            st.markdown("**🎯 改善建議**")
                            for i, tip in enumerate(tips, 1):
                                st.markdown(
                                    f'<div style="background:rgba(255,193,7,0.08);border-left:4px solid #ffc107;'
                                    f'border-radius:0 12px 12px 0;padding:0.8rem 1.2rem;margin-bottom:0.5rem;">'
                                    f'<span style="font-size:0.8rem;color:#ffc107;">改善點 {i}</span><br>'
                                    f'<span style="font-size:0.9rem;color:#e9ecef;">{tip}</span>'
                                    f'</div>',
                                    unsafe_allow_html=True
                                )

                        col_lb, col_rb = st.columns(2)
                        with col_lb:
                            st.markdown(
                                f'<div style="background:rgba(0,123,255,0.08);border-left:4px solid #007bff;'
                                f'border-radius:0 12px 12px 0;padding:1rem 1.2rem;">'
                                f'<div style="font-size:0.8rem;color:#4dabf7;font-weight:700;">🔵 左腦分析</div>'
                                f'<div style="font-size:0.88rem;color:#e9ecef;margin-top:0.3rem;">'
                                f'{latest.get("left_brain","—")}</div></div>',
                                unsafe_allow_html=True
                            )
                        with col_rb:
                            st.markdown(
                                f'<div style="background:rgba(220,53,69,0.08);border-left:4px solid #e05c6e;'
                                f'border-radius:0 12px 12px 0;padding:1rem 1.2rem;">'
                                f'<div style="font-size:0.8rem;color:#f783ac;font-weight:700;">🔴 右腦分析</div>'
                                f'<div style="font-size:0.88rem;color:#e9ecef;margin-top:0.3rem;">'
                                f'{latest.get("right_brain","—")}</div></div>',
                                unsafe_allow_html=True
                            )

                        st.markdown(f"**💡 培訓建議：** {latest.get('action_item','—')}")

                        if len(emp_records) > 1:
                            st.markdown("**📅 歷史訓練記錄**")
                            for rec in emp_records[1:6]:
                                rec_score = rec.get("score", 0)
                                rec_color = "#28a745" if rec_score >= 80 else ("#ffc107" if rec_score >= 60 else "#dc3545")
                                rec_date = rec.get("created_at", "")[:10]
                                st.markdown(
                                    f'<div style="background:rgba(255,255,255,0.03);border-left:3px solid {rec_color};'
                                    f'border-radius:0 8px 8px 0;padding:0.5rem 1rem;margin-bottom:0.4rem;">'
                                    f'{rec_date}　<span style="color:{rec_color};font-weight:700;">{rec_score} 分</span>'
                                    f'　{rec.get("closing_result","")}'
                                    f'</div>',
                                    unsafe_allow_html=True
                                )

                st.markdown("---")
                st.markdown("### 🚨 需要主管關注")

                from datetime import datetime, timedelta

                # 計算行動信號
                alert_no_practice = []   # 超過 7 天沒練習
                alert_declining   = []   # 最新分數低於個人平均
                alert_low_score   = []   # 平均分持續低於 60

                today = datetime.utcnow()

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

                    # 最新分數低於個人平均（有超過 1 次訓練才比較）
                    if len(scores_list) >= 2:
                        personal_avg = sum(scores_list) / len(scores_list)
                        latest_score = scores_list[0]  # scores_data 已按時間降序排列
                        if latest_score < personal_avg - 5:
                            alert_declining.append(
                                f"{name}（最新 {latest_score} 分 vs 平均 {personal_avg:.0f} 分）"
                            )

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
                        st.markdown(f"　・{a}")

                if alert_low_score:
                    has_alert = True
                    st.warning("⚠️ **持續低於 60 分**（建議安排一對一輔導）")
                    for a in alert_low_score:
                        st.markdown(f"　・{a}")

                if not has_alert:
                    st.success("✅ 目前沒有需要特別關注的成員，整體訓練狀況良好。")

                st.markdown("---")

                # ── 團隊指導建議 ──────────────────────────
                st.markdown("### 🧭 團隊指導建議")

                # 計算關鍵數據
                closing_results = [s.get("closing_result", "") for s in scores_data]
                close_count     = closing_results.count("當場成交")
                delay_count     = closing_results.count("有條件延遲")
                reject_count    = closing_results.count("拒絕成交")
                close_rate      = (close_count / total_sessions * 100) if total_sessions > 0 else 0

                low_score_count  = sum(1 for s in scores_data if s.get("score", 0) < 60)
                high_score_count = sum(1 for s in scores_data if s.get("score", 0) >= 80)

                # 顯示關鍵指標
                col_c1, col_c2, col_c3 = st.columns(3)
                with col_c1:
                    st.metric("✅ 當場成交率", f"{close_rate:.0f}%")
                with col_c2:
                    st.metric("⏳ 延遲決策次數", f"{delay_count} 次")
                with col_c3:
                    st.metric("❌ 拒絕成交次數", f"{reject_count} 次")

                st.markdown("<br>", unsafe_allow_html=True)

                # 根據數據產生具體指導建議
                suggestions = []

                if avg_score < 60:
                    suggestions.append(("🔴 立即行動",
                        "整體分數偏低，建議本週安排基礎銷售技巧培訓，重點訓練產品知識表達與開場白設計。"))
                elif avg_score < 70:
                    suggestions.append(("🟡 近期行動",
                        "整體表現尚可但未達標，建議安排異議處理專項訓練，加強回應客戶常見疑慮的話術。"))
                else:
                    suggestions.append(("🟢 維持精進",
                        "整體表現良好，建議安排進階情境演練，針對高難度客戶類型（如強烈比價型）進行專項訓練。"))

                if close_rate < 30:
                    suggestions.append(("🔴 成交能力",
                        f"當場成交率僅 {close_rate:.0f}%，建議重點加強促成技巧與現場引導決策的話術，"
                        f"可安排角色扮演練習「如何在客戶猶豫時推進成交」。"))
                elif delay_count > total_sessions * 0.5:
                    suggestions.append(("🟡 促成技巧",
                        "超過一半的演練結果為「有條件延遲」，客戶傾向不當場決定。"
                        "建議加強緊迫感製造與即時解除疑慮的能力。"))

                if low_score_count > 0:
                    low_names = [
                        s.get("employee_name", "匿名員工")
                        for s in scores_data if s.get("score", 0) < 60
                    ]
                    unique_low = list(dict.fromkeys(low_names))[:3]
                    suggestions.append(("🔴 個別輔導",
                        f"以下成員有低於 60 分的訓練記錄，建議安排一對一輔導：{', '.join(unique_low)}"))

                for title, content in suggestions:
                    color = "#dc3545" if "🔴" in title else ("#ffc107" if "🟡" in title else "#28a745")
                    st.markdown(
                        f'<div style="background:rgba(255,255,255,0.04);border-left:4px solid {color};'
                        f'border-radius:0 12px 12px 0;padding:1rem 1.4rem;margin-bottom:0.8rem;">'
                        f'<div style="font-size:0.85rem;font-weight:700;color:{color};margin-bottom:0.4rem;">'
                        f'{title}</div>'
                        f'<div style="font-size:0.92rem;line-height:1.7;color:#e9ecef;">{content}</div>'
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
                    bonus_ic = "🏅" if s.get("bonus_unlocked") else ""
                    closing  = s.get("closing_result", "")
                    color    = "#28a745" if score_v >= 80 else ("#ffc107" if score_v >= 60 else "#dc3545")
                    st.markdown(
                        f'<div style="background:rgba(255,255,255,0.04);border-left:4px solid {color};'
                        f'border-radius:0 8px 8px 0;padding:0.6rem 1rem;margin-bottom:0.5rem;">'
                        f'<span style="font-weight:700;">{name}</span> {bonus_ic}'
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
                bonus          = cached_report.get("bonus_unlocked", False)
                left_brain     = cached_report.get("left_brain", "—")
                right_brain    = cached_report.get("right_brain", "—")
                action_item    = cached_report.get("action_item", "—")
                closing_result = cached_report.get("closing_result", "未完成成交環節")

                st.markdown("---")

                # ── 頂部核心指標 ──────────────────────────────
                st.markdown("### 🏅 核心指標")
                col_score, col_bonus, col_level, col_closing = st.columns(4)

                with col_score:
                    delta_label = "🔥 高表現" if score >= 80 else ("📈 需加強" if score >= 60 else "⚠️ 需培訓")
                    st.metric(
                        label="📊 綜合戰力分數",
                        value=f"{score} 分",
                        delta=delta_label,
                        delta_color="normal" if score >= 80 else "inverse"
                    )

                with col_bonus:
                    if bonus:
                        st.success("🏅 獎金分潤門檻\n\n**✅ 已解鎖**（≥ 80 分）", icon="🎉")
                    else:
                        st.error(
                            f"🏅 獎金分潤門檻\n\n**❌ 未達標**（差 {80 - score} 分）",
                            icon="⚠️"
                        )

                with col_level:
                    if score >= 90:
                        level_text, level_color = "🥇 頂尖業務", "#ffd700"
                    elif score >= 80:
                        level_text, level_color = "🥈 優質業務", "#c0c0c0"
                    elif score >= 70:
                        level_text, level_color = "🥉 發展中業務", "#cd7f32"
                    elif score >= 60:
                        level_text, level_color = "📋 需要輔導", "#6c757d"
                    else:
                        level_text, level_color = "🔴 強制培訓", "#dc3545"
                    st.markdown(
                        f'<div style="background:rgba(255,255,255,0.05);border-radius:12px;'
                        f'padding:1.1rem 1rem;text-align:center;border:1px solid {level_color}40;">'
                        f'<div style="font-size:0.8rem;color:#adb5bd;margin-bottom:0.3rem;">業務等級</div>'
                        f'<div style="font-size:1.15rem;font-weight:700;color:{level_color};">{level_text}</div>'
                        f'</div>',
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

                # 分數進度條
                bar_color = "#28a745" if score >= 80 else ("#ffc107" if score >= 60 else "#dc3545")
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
                        '<div style="background:rgba(0,123,255,0.08);border-left:4px solid #007bff;'
                        'border-radius:0 12px 12px 0;padding:1.2rem 1.4rem;">'
                        '<div style="font-size:1rem;font-weight:700;color:#4dabf7;margin-bottom:0.6rem;">'
                        '🔵 左腦：邏輯 & 賣點掌握</div>'
                        f'<div style="font-size:0.93rem;line-height:1.7;color:#e9ecef;">{left_brain}</div>'
                        '</div>',
                        unsafe_allow_html=True
                    )

                with col_right:
                    st.markdown(
                        '<div style="background:rgba(220,53,69,0.08);border-left:4px solid #e05c6e;'
                        'border-radius:0 12px 12px 0;padding:1.2rem 1.4rem;">'
                        '<div style="font-size:1rem;font-weight:700;color:#f783ac;margin-bottom:0.6rem;">'
                        '🔴 右腦：同理心 & 溝通溫度</div>'
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
                        f'<div style="background:rgba(40,167,69,0.1);border-left:4px solid #28a745;'
                        f'border-radius:0 12px 12px 0;padding:1.2rem 1.4rem;margin-bottom:1rem;">'
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
                            f'<div style="background:rgba(255,193,7,0.08);border-left:4px solid #ffc107;'
                            f'border-radius:0 12px 12px 0;padding:1rem 1.4rem;margin-bottom:0.6rem;">'
                            f'<div style="font-size:0.85rem;font-weight:700;color:#ffc107;margin-bottom:0.3rem;">'
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
                        bg = "rgba(255,255,255,0.04)" if msg["role"] == "user" else "rgba(0,123,255,0.06)"
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
    'PitchCoach 企業中控台 v3　｜　Powered by Claude AI & Streamlit'
    '</div>',
    unsafe_allow_html=True
)
