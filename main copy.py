# ============================================================
# PitchCoach 企業中控台 v3 — 雙頁籤完整 B2B 系統
# Tab 1：主管中控台（PDF 分析 → 考題審核 → 發布任務）
# Tab 2：員工實戰沙盒（AI 扮演客戶 → 循序刁難 → 即時對話）
# 啟動指令：streamlit run main.py
# ============================================================

import streamlit as st
import streamlit.components.v1 as components   # 用於嵌入 WebCam HTML/JS
import fitz          # pymupdf
import anthropic
import openai        # OpenAI Whisper STT + 未來擴充用
import edge_tts      # Microsoft Edge TTS：台灣口音、語速可調
import asyncio       # 執行 edge_tts 的 async 函式
import concurrent.futures  # 在新執行緒中執行 asyncio，避免與 Streamlit 衝突
import re
import json          # 用於解析 Claude 回傳的 JSON 格式考題
import io            # 用於在記憶體中建立音訊 BytesIO 緩衝區
import emoji         # 用於在 TTS 前移除 Emoji，避免語音引擎唸出表情符號名稱
import pathlib       # 用於 JSON 設定檔的讀寫路徑操作
import random        # 用於隨機挑戰模式抽題
import os
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client, Client   # Supabase 資料庫整合

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")


def get_supabase() -> Client:
    """建立並回傳 Supabase client 實例。"""
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# ──────────────────────────────────────────────
# 設定區
# ──────────────────────────────────────────────
API_KEY        = os.environ.get("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
REQUIRED_SELECTION = 2   # 主管必須勾選的考題數量（極速對決：2 題）

# 五大銷售障礙類別標籤（Tab 1 分類顯示 + Tab 2 跨類抽題共用）
CATEGORY_LABELS = {
    "cat_1_product":     "🔍 第一類：產品理解",
    "cat_2_price":       "💰 第二類：價格異議",
    "cat_3_trust":       "🛡️ 第三類：信任疑慮",
    "cat_4_competition": "⚔️ 第四類：競品比較",
    "cat_5_decision":    "🚪 第五類：決策障礙",
}

# Edge TTS 語音設定
EDGE_TTS_VOICE = "zh-TW-HsiaoChenNeural"   # 台灣口音女聲（曉臻）；可換 zh-TW-YunJheNeural（雲哲，男聲）
EDGE_TTS_RATE  = "+20%"                     # 語速加快 20%，對話節奏更緊湊

# JSON 持久化設定檔路徑（與 main.py 同目錄）
SETTINGS_FILE = pathlib.Path("company_settings.json")

# 需要持久化的 session_state 鍵（子集）
PERSIST_KEYS = [
    "main_analysis", "questions", "analyzed_filename",
    "product_name", "product_benefits", "target_audience",
    "published_questions", "task_published", "customer_scenario",
    "is_completed", "evaluation_report", "question_mode",
    "questions_by_category",
]


def load_settings() -> None:
    """
    程式啟動時還原主管設定到 session_state。

    優先順序：
    1. Supabase training_sets（取 company 下最新一筆 is_published=True 的記錄）
    2. 若 Supabase 失敗，fallback 到 company_settings.json

    只還原 session_state 中尚未存在的鍵，避免覆蓋當次已操作的資料。
    """
    try:
        sb = get_supabase()

        # 取得智云健康的 company_id
        company = sb.table("companies").select("id").eq(
            "name", "智云健康股份有限公司"
        ).execute()
        if not company.data:
            raise ValueError("找不到公司記錄")
        company_id = company.data[0]["id"]

        # 取得最新一筆已發布的訓練設定
        result = sb.table("training_sets").select("*").eq(
            "company_id", company_id
        ).eq(
            "is_published", True
        ).order(
            "created_at", desc=True
        ).limit(1).execute()

        if not result.data:
            raise ValueError("無已發布的訓練設定")

        row = result.data[0]

        # 欄位映射（Supabase 欄位名 → session_state 鍵名）
        mapping = {
            "main_analysis":         row.get("main_analysis"),
            "questions":             row.get("questions"),
            "analyzed_filename":     row.get("filename"),
            "product_name":          row.get("product_name"),
            "product_benefits":      row.get("product_benefits"),
            "target_audience":       row.get("target_audience"),
            "published_questions":   row.get("published_questions"),
            "customer_scenario":     row.get("customer_scenario"),
            "task_published":        row.get("is_published", False),
            "questions_by_category": row.get("questions_by_category"),
        }
        for key, val in mapping.items():
            if key not in st.session_state and val is not None:
                st.session_state[key] = val

        print("[Supabase] load_settings 成功")

    except Exception as e:
        print(f"[Supabase警告] load_settings 失敗：{e}，嘗試從 JSON 備援讀取")
        # Fallback：從本機 company_settings.json 讀取
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                for key, val in data.items():
                    if key not in st.session_state:
                        st.session_state[key] = val
                print("[JSON] load_settings fallback 成功")
            except Exception:
                pass  # JSON 也失敗就靜默略過，不崩潰


def get_or_create_company(name: str = "智云健康股份有限公司") -> str:
    """
    查詢 Supabase companies 資料表中是否已存在指定公司，
    存在則回傳 id；不存在則新增後回傳 id。
    失敗時靜默 print 警告並回傳空字串，不影響主流程。
    """
    try:
        sb = get_supabase()
        result = sb.table("companies").select("id").eq("name", name).execute()
        if result.data:
            return result.data[0]["id"]
        new = sb.table("companies").insert({"name": name}).execute()
        return new.data[0]["id"]
    except Exception as e:
        print(f"[Supabase警告] get_or_create_company 失敗：{e}")
        return ""


def save_settings() -> None:
    """
    將 PERSIST_KEYS 中有值的鍵寫入 company_settings.json。
    主管下次重整頁面後，load_settings() 會自動還原這些設定。
    同時嘗試將訓練集資料同步至 Supabase training_sets 資料表。
    """
    data = {k: st.session_state[k] for k in PERSIST_KEYS if k in st.session_state}
    SETTINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # 同步至 Supabase（失敗只 print 警告，不中斷主流程）
    try:
        sb = get_supabase()
        company_id = get_or_create_company("智云健康股份有限公司")
        if company_id:
            sb.table("training_sets").insert({
                "company_id":           company_id,
                "filename":             st.session_state.get("analyzed_filename", ""),
                "product_name":         st.session_state.get("product_name", ""),
                "main_analysis":        st.session_state.get("main_analysis", ""),
                "product_benefits":     st.session_state.get("product_benefits", ""),
                "target_audience":      st.session_state.get("target_audience", ""),
                "questions":            st.session_state.get("questions", []),
                "published_questions":  st.session_state.get("published_questions", []),
                "customer_scenario":    st.session_state.get("customer_scenario", ""),
                "questions_by_category": st.session_state.get("questions_by_category", {}),
                "is_published":         True
            }).execute()
            print("[Supabase] training_sets 儲存成功")
    except Exception as e:
        print(f"[Supabase警告] training_sets 儲存失敗：{e}")

# ──────────────────────────────────────────────
# 網頁基本設定
# ──────────────────────────────────────────────

# 每個瀏覽器 Session 只執行一次：自動讀取上次儲存的主管設定
if "settings_loaded" not in st.session_state:
    load_settings()
    st.session_state["settings_loaded"] = True

st.set_page_config(
    page_title="PitchCoach 企業中控台",
    page_icon="🎯",
    layout="wide",
)

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
# 核心函式
# ══════════════════════════════════════════════

def clean_text_for_tts(text: str) -> str:
    """
    TTS 前的文字淨化，只保留適合語音引擎朗讀的純文字。

    過濾順序：
    1. 移除全形（）與半形()括號及其內容（舞台指示詞）
    2. 移除所有 Emoji（避免語音引擎唸出「笑臉」「大拇指」等名稱）
    3. 合併多餘空白

    畫面顯示仍使用原始含括號與 Emoji 的完整文字，此函式僅供語音引擎使用。
    """
    cleaned = re.sub(r'[（(][^）)]*[）)]', '', text)   # 移除括號舞台指示
    cleaned = emoji.replace_emoji(cleaned, replace='')  # 移除全部 Emoji
    cleaned = re.sub(r'\s{2,}', ' ', cleaned)           # 合併多餘空白
    return cleaned.strip()


def generate_tts_audio(text: str) -> bytes | None:
    """
    使用 Microsoft Edge TTS（edge-tts）將文字轉成台灣口音語音。

    特色：
    - 語音模型：zh-TW-HsiaoChenNeural（曉臻，台灣女聲）
    - 語速：+20%，對話節奏更緊湊自然
    - 傳入前自動過濾括號舞台指示
    - 以 asyncio 執行；若 Streamlit 事件迴圈已存在，
      改用 ThreadPoolExecutor 在獨立執行緒中執行以避免衝突

    失敗時靜默回傳 None，確保主程式不崩潰。
    """
    async def _run_tts(speech_text: str) -> bytes:
        communicate = edge_tts.Communicate(
            speech_text,
            EDGE_TTS_VOICE,
            rate=EDGE_TTS_RATE
        )
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        buf.seek(0)
        return buf.read()

    try:
        speech_text = clean_text_for_tts(text)
        if not speech_text:
            return None

        # edge-tts 是 async，需要在事件迴圈中執行
        # Streamlit 本身有自己的事件迴圈，用 ThreadPoolExecutor 開新執行緒
        # 讓 asyncio.run() 在一個乾淨的迴圈環境中執行，避免「迴圈已在運行」錯誤
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _run_tts(speech_text))
            return future.result(timeout=30)

    except Exception:
        return None


def speech_to_text(audio_file) -> str | None:
    """
    使用 OpenAI Whisper API 將 st.audio_input 的音訊轉成繁體中文文字。

    優勢（對比 Google STT）：
    - 支援台語、中英混雜、口音、專有名詞
    - 直接接受瀏覽器錄製的 WebM/Opus 格式，無需格式轉換
    - 抗噪能力強，低品質麥克風也能準確辨識

    失敗情況（全部靜默回傳 None，不崩潰）：
    - AuthenticationError：OpenAI API Key 錯誤
    - 網路連線失敗、音檔損毀、其他未知錯誤
    """
    try:
        import difflib
        client      = openai.OpenAI(api_key=OPENAI_API_KEY)
        audio_bytes = audio_file.read()
        if len(audio_bytes) < 500:
            st.warning(f"⚠️ 麥克風收到資料量過少（{len(audio_bytes)} bytes），請檢查麥克風裝置")
            return None

        # 從 session_state 取得產品名稱作為提示，幫助 Whisper 辨識專有名詞
        product_hint  = st.session_state.get("product_name", "")
        benefits_hint = st.session_state.get("product_benefits", "")[:200]
        whisper_prompt = f"以下是業務員推銷產品的對話。產品名稱：{product_hint}。{benefits_hint}"

        # Whisper 支援 webm/wav/mp4/mp3 等格式；瀏覽器通常錄製成 webm
        # 以 tuple (檔名, BytesIO, MIME類型) 傳遞，讓 SDK 正確判斷格式
        transcript = client.audio.transcriptions.create(
            model    = "whisper-1",
            file     = ("audio.webm", io.BytesIO(audio_bytes), "audio/webm"),
            language = "zh",            # 指定中文提高準確率；Whisper 仍會自動辨識台語
            prompt   = whisper_prompt,  # 加入產品名稱提示詞，提升專有名詞辨識準確率
        )

        recognized_text = transcript.text.strip()

        # 品牌名稱修正：對每個詞和產品名稱做相似度比對，超過 60% 自動替換
        product_name = st.session_state.get("product_name", "")
        if product_name and len(product_name) >= 2:
            words = recognized_text.split()
            corrected_words = []
            for word in words:
                similarity = difflib.SequenceMatcher(
                    None, word.upper(), product_name.upper()
                ).ratio()
                if similarity > 0.6 and len(word) >= 2:
                    corrected_words.append(product_name)
                else:
                    corrected_words.append(word)
            recognized_text = " ".join(corrected_words)

        return recognized_text or None

    except openai.AuthenticationError:
        st.error("❌ OpenAI API Key 無效，請確認 .env 裡的 OPENAI_API_KEY")
        return None
    except Exception as e:
        st.error(f"❌ 語音辨識失敗：{type(e).__name__}: {str(e)}")
        return None


def get_coach_hint(
    chat_history: list[dict],
    published_questions: list[str],
    current_q_idx: int,
    product_benefits: str = "",
    target_audience: str  = ""
) -> str:
    """
    獨立呼叫 Claude 扮演「王牌銷售總監教練」，
    分析當前對話並給出一到兩句戰術提示。

    關鍵設計：
    - product_benefits / target_audience 從 Tab 1 動態提取，不寫死任何產品名稱
    - 教練會根據客群性質（B2C 一般消費者 vs B2B 企業買家）自動調整語氣
    - 此函式回傳值「絕對不存入 chat_history」，客戶 AI 完全看不到此內容
    """
    client = anthropic.Anthropic(api_key=API_KEY)

    # 告訴教練目前面臨的是哪一道考題
    if current_q_idx < len(published_questions):
        q_title   = published_questions[current_q_idx].split("\n")[0]
        q_context = f"客戶目前的核心疑慮（第 {current_q_idx + 1} 題）：{q_title}"
    else:
        q_context = "所有疑慮都已涵蓋，正進入最終購買決策階段。"

    # 只取最近 6 則對話，聚焦在當前局勢
    recent_turns  = chat_history[-6:]
    dialogue_text = "\n".join(
        f"{'【業務員】' if m['role'] == 'user' else '【客戶】'} {m['content']}"
        for m in recent_turns
    )

    # 動態背景：從 Tab 1 提取，若尚未分析則給安全的 fallback 文字
    benefits_block = product_benefits or "（尚未提取，請先在主管中控台完成教材分析）"
    audience_block = target_audience  or "（尚未提取，請先在主管中控台完成教材分析）"

    system_prompt = f"""你是一位王牌銷售總監，正在即時指導一位業務員。

【當前考題】
客戶的核心疑慮是：{q_context}

【產品真實賣點】
{benefits_block}

【目標客群】
{audience_block}

你的任務：根據業務員剛才的回答，給出「下一句話應該怎麼說」的具體方向。

強制規則：
- 你的提示必須直接針對「當前考題」，不能給無關的建議
- 提示格式：「針對客戶[具體疑慮]，你可以[具體方向]，例如用[生活化比喻或具體說法]來回應」
- 如果業務員剛才說得好，指出哪裡好，並提示如何收尾
- 如果業務員剛才說得不好，直接說出問題在哪，給出修正方向
- 絕對禁止：空洞的鼓勵話語、與考題無關的建議
- 字數：50字以內，要像耳語一樣精準"""

    user_msg = f"""對話記錄（最近幾則）：
{dialogue_text}

{q_context}

請給出一到兩句戰術提示："""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=150,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}]
    )
    return response.content[0].text.strip()


def get_evaluation_report(
    chat_history: list[dict],
    published_questions: list[str],
    customer_scenario: str = "",
    product_benefits: str = ""
) -> dict:
    """
    呼叫 Claude 以嚴格銷售總監身份評分，強制輸出 JSON。

    回傳欄位：
    - score          : 0~100 綜合分（整數）
    - bonus_unlocked : 分數 >= 80 即解鎖獎金分潤門檻（布林）
    - left_brain     : 左腦邏輯分析（產品賣點掌握度）
    - right_brain    : 右腦溝通分析（同理心 & 語氣）
    - action_item    : 主管下一步培訓建議（一句話）

    解析策略：先嘗試直接 json.loads()，失敗則用 regex 抓 JSON 子串，
    再失敗回傳安全的 fallback dict。
    """
    client = anthropic.Anthropic(api_key=API_KEY)

    # 整理 2 道必考題
    questions_text = "\n".join(
        f"  第{i+1}題：{q.split(chr(10))[0].strip()}"
        for i, q in enumerate(published_questions)
    )

    # 整理對話紀錄（去掉暗號標籤）
    dialogue_text = "\n".join(
        f"{'【業務員】' if m['role'] == 'user' else '【AI 客戶】'} "
        f"{m['content'].replace('[TEST_COMPLETE]', '').strip()}"
        for m in chat_history
    )

    scenario_block = f"客戶情境設定：{customer_scenario.strip()}" if customer_scenario.strip() else "客戶情境：一般 B2C 消費者"
    benefits_block  = product_benefits or "（未提供產品賣點資訊）"

    system_prompt = """你是一位嚴格但客觀的企業銷售總監，正在為業務員做最終戰力評估。
你必須分析業務員在剛才對話中的表現，並嚴格輸出以下 JSON 格式，不可包含任何其他文字：

{
  "score": <整數 0~100>,
  "bonus_unlocked": <true 或 false，score >= 80 才是 true>,
  "left_brain": "<左腦邏輯分析：約 80 字。業務員是否精準命中產品賣點？有無漏掉關鍵資訊？>",
  "right_brain": "<右腦溝通分析：約 80 字。面對客戶情境，語氣是否具備同理心？是否太過生硬或照本宣科？>",
  "action_item": "<給主管的一句話培訓建議，例如：建議安排同理心溝通訓練，強化用故事代替數據的能力。>",
  "closing_result": "<成交結果：當場成交 / 有條件延遲 / 明確拒絕>"
}

評分維度（共 100 分）：
- 左腦邏輯（35 分）：賣點覆蓋率、關鍵資訊準確性、回應客戶疑慮的完整度
- 右腦溝通（35 分）：語氣自然度、同理心表達、是否成功降低客戶疑慮、說話方式是否貼近客戶情境
- 成交能力（30 分）：是否識別購買信號、是否主動創造成交條件、最終客戶是否成交或給出明確購買意願

closing_result 填寫規則：
- 「當場成交」：客戶明確表示要購買或給出具體購買條件
- 「有條件延遲」：客戶有興趣但要求優惠/試用/再想想
- 「明確拒絕」：客戶禮貌但清楚地拒絕

嚴格要求：只輸出 JSON，不要有任何前言、後記或 Markdown 符號。"""

    user_msg = f"""請根據以下資訊，評估業務員的銷售表現：

【本次訓練的 2 道必考題】
{questions_text}

【{scenario_block}】

【產品真實賣點（供你評估業務員是否命中）】
{benefits_block[:1500]}

【完整對話紀錄】
{dialogue_text}

請輸出 JSON 評分報告："""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=800,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}]
    )
    raw = response.content[0].text.strip()

    # 三層解析防護
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 第二層：用 regex 抓 { ... } 子串
    match = re.search(r'\{[\s\S]*\}', raw)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Fallback：讓 UI 顯示友善錯誤，不崩潰
    return {
        "score": 0,
        "bonus_unlocked": False,
        "left_brain": "（AI 評分解析失敗，請重新產生報告）",
        "right_brain": "（AI 評分解析失敗，請重新產生報告）",
        "action_item": "請重新按下『產生報告』按鈕。",
        "_raw": raw[:500]
    }


def extract_text_from_bytes(pdf_bytes: bytes) -> str:
    """從上傳的 PDF bytes 逐頁萃取純文字。"""
    all_text = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for i in range(len(doc)):
        all_text.append(f"--- 第 {i+1} 頁 ---\n{doc[i].get_text('text')}")
    doc.close()
    return "\n".join(all_text)


def analyze_with_claude(document_text: str) -> str:
    """
    第一次 API 呼叫：只產出三大重點分析（Markdown 格式）。
    考題由獨立的 generate_questions_json() 負責，職責分離、格式各自乾淨。
    """
    client = anthropic.Anthropic(api_key=API_KEY)
    system_prompt = """你是一個 B2B 企業培訓專家。請分析以下文件，嚴格依照下列 Markdown 格式輸出四個章節，不要包含任何考題：

## 🏷️ 產品名稱
（從文件中找出主要產品的正確名稱，只寫名稱本身，例如：Bobby）

---

## 📌 一、產品核心利益點
（條列式列出所有核心賣點、規格與差異化優勢，越具體越好）

---

## 🚫 二、絕對不能說的違規用語
（條列式列出禁止詞彙，並簡短說明原因）

---

## 🎯 三、目標客群輪廓
（條列式描述目標客戶的產業、規模、職位、核心痛點）

只輸出以上四個章節，不要加入任何考題或其他內容。"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": f"以下是教材內容：\n\n{document_text}"}]
    )
    return response.content[0].text


_EMPTY_CATEGORIES: dict = {
    "cat_1_product":     [],
    "cat_2_price":       [],
    "cat_3_trust":       [],
    "cat_4_competition": [],
    "cat_5_decision":    [],
}


def generate_questions_json(document_text: str) -> dict:
    """
    第二次 API 呼叫：按 5 大通用銷售障礙類別各生成 5 道刁難考題，共 25 道。

    回傳格式（dict）：
    {
        "cat_1_product":     [5 道題 str, ...],
        "cat_2_price":       [5 道題 str, ...],
        "cat_3_trust":       [5 道題 str, ...],
        "cat_4_competition": [5 道題 str, ...],
        "cat_5_decision":    [5 道題 str, ...],
    }

    解析策略（三層防護）：
    1. json.loads() 直接解析 JSON 物件
    2. regex 找出 { ... } 子串後解析
    3. 全部失敗 → 回傳空的 _EMPTY_CATEGORIES（不崩潰）
    """
    client = anthropic.Anthropic(api_key=API_KEY)

    system_prompt = """你是一個專業的B2B銷售訓練專家。
請根據文件內容，為以下5個通用銷售障礙類別，各生成5道刁難考題。
這些類別適用於任何行業和產品。

【強制輸出格式】
你的整個回覆必須是一個合法的JSON物件，格式如下：
{
  "cat_1_product": [
    "Q1. 考題內容 👉 建議回答方向：具體建議",
    "Q2. ...",
    "Q3. ...",
    "Q4. ...",
    "Q5. ..."
  ],
  "cat_2_price": [...5題...],
  "cat_3_trust": [...5題...],
  "cat_4_competition": [...5題...],
  "cat_5_decision": [...5題...]
}

【五大類別定義】

cat_1_product（產品理解類）：
客戶對「產品本身運作」的疑慮：原理、使用方式、適用族群、禁忌症。
✅ 屬於這類：「孕婦可以用嗎」「怎麼充電」「要用多久才有效」「心臟病可以用嗎」
❌ 不屬於這類：價格、效果保證——這些分別是價格和信任問題
根據PDF內容，生成5道針對這個產品的理解類刁難問題。

cat_2_price（價格異議類）：
客戶對「金錢」本身的疑慮：太貴、預算不夠、CP值存疑、要比價、付款方式。
✅ 屬於這類：「這個價格我覺得偏高」「有沒有分期」「比別人貴在哪」「有沒有折扣」
❌ 不屬於這類：副作用、效果好壞、安全性、使用方式——這些是信任或產品問題
根據PDF內容，生成5道針對這個產品的價格異議刁難問題。

cat_3_trust（信任疑慮類）：
客戶對「效果與安全」的疑慮：沒效怎麼辦、副作用、有沒有認證、朋友用過沒效。
✅ 屬於這類：「萬一沒效怎麼辦」「有沒有副作用」「朋友用過類似的沒效」「有認證嗎」
❌ 不屬於這類：價格高低、要比價——這些是價格問題
根據PDF內容，生成5道針對這個產品的信任疑慮刁難問題。

cat_4_competition（競品比較類）：
客戶主動提到其他選擇或品牌：「市面上有類似的」「朋友推薦別牌」「網路上有便宜的」。
✅ 屬於這類：「跟按摩槍有什麼不同」「為什麼不買日本進口的」「淘寶上有一樣的更便宜」
❌ 不屬於這類：只問價格高低（沒提到競品）
根據PDF內容，生成5道針對這個產品的競品比較刁難問題。

cat_5_decision（決策障礙類）：
客戶心動但拖延：「再想想」「問家人」「下次」「現在不方便」。
✅ 屬於這類：「我考慮一下」「要跟老公商量」「這個月預算用完了，下個月再說」
❌ 不屬於這類：真正的價格異議或信任疑慮（那些有具體問題，不是單純拖延）
根據PDF內容，生成5道針對這個產品的決策障礙刁難問題。

【重要規定】
- 只輸出JSON物件，不要有任何其他文字
- 每道題格式：「Q數字. 刁難問題內容 👉 建議回答方向：具體建議」
- 問題內容必須根據上傳的PDF具體產品來設計，不要用通用模板
- 建議回答方向也必須根據PDF的真實資訊，不要憑空捏造
"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": f"以下是教材內容：\n\n{document_text}"}]
    )
    raw = response.content[0].text.strip()

    # 第一步：移除 Markdown 程式碼區塊標記（Claude 有時會用 ```json ... ``` 包住 JSON）
    cleaned = re.sub(r'```json', '', raw)
    cleaned = re.sub(r'```', '', cleaned)
    cleaned = cleaned.strip()

    # ── 第一層：直接 json.loads 解析 ────────────────
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            for key in _EMPTY_CATEGORIES:
                if key not in result:
                    result[key] = []
            print(f"[DEBUG] 解析成功！各類別題數：{ {k: len(v) for k, v in result.items()} }")
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # ── 第二層：找出 { } 子字串再解析 ───────────────
    try:
        start = cleaned.index("{")
        end   = cleaned.rindex("}") + 1
        result = json.loads(cleaned[start:end])
        if isinstance(result, dict):
            for key in _EMPTY_CATEGORIES:
                if key not in result:
                    result[key] = []
            print("[DEBUG] 第二層解析成功")
            return result
    except (ValueError, json.JSONDecodeError):
        pass

    # ── 第三層：逐類別用 regex 提取各陣列 ───────────
    try:
        result = dict(_EMPTY_CATEGORIES)
        for cat_key in _EMPTY_CATEGORIES:
            pattern = rf'"{cat_key}"\s*:\s*(\[.*?\])'
            match = re.search(pattern, cleaned, re.DOTALL)
            if match:
                result[cat_key] = json.loads(match.group(1))
        if any(len(v) > 0 for v in result.values()):
            print("[DEBUG] 第三層解析成功")
            return result
    except Exception:
        pass

    print("[DEBUG] 所有解析失敗")
    return dict(_EMPTY_CATEGORIES)


def parse_analysis_and_questions(full_response: str) -> tuple[str, list[str]]:
    """
    將 Claude 完整回應拆成：
    - main_analysis：前三大重點的 Markdown 字串
    - questions：考題字串清單（每題含題目與建議回答方向）
    """
    split_pattern = r'(##\s*💬\s*四、[^\n]*\n)'
    parts = re.split(split_pattern, full_response, maxsplit=1)
    if len(parts) < 3:
        return full_response, []

    main_analysis = parts[0].rstrip()
    questions_raw = parts[2]

    # 以「Q數字.」為每題起始點切分
    question_blocks = re.split(r'\n(?=Q\d+\.)', questions_raw)
    questions = [
        b.strip() for b in question_blocks
        if b.strip() and re.match(r'Q\d+\.', b.strip())
    ]
    return main_analysis, questions


def extract_section(main_analysis: str, emoji_anchor: str) -> str:
    """
    從三大重點 Markdown 中，依 emoji 錨點提取特定章節的純文字內容。

    原理：找到「## 🎯 三、...」這類 header，
    擷取其後、直到下一個 ## 或 --- 之前的全部內容。
    若找不到，回傳空字串（讓後續函式有安全的 fallback）。

    用途：動態注入教練 Prompt，避免寫死產品名稱或客群描述。
    """
    # 用 emoji 作為 header 的起始定位點，內容擷取到下一個區塊為止
    pattern = rf'##\s*{re.escape(emoji_anchor)}[^\n]*\n(.*?)(?=\n##|\n---|\Z)'
    match   = re.search(pattern, main_analysis, re.DOTALL)
    return match.group(1).strip() if match else ""


def get_customer_response(
    chat_history: list[dict],
    published_questions: list[str],
    current_q_idx: int,
    analysis_context: str,
    product_name: str = "",
    customer_scenario: str = "",
    training_mode: str = "speed"
) -> tuple[str, int]:
    """
    Claude 扮演對健康產品感興趣但有疑慮的普通 B2C 消費者。

    功能：
    - customer_scenario：Tab 1 主管自訂的客戶情境，動態注入 System Prompt
    - 明確注入 2 道必考題清單，確保 AI 依序提問
    - [NEXT_Q] 暗號：AI 評估業務員回答夠好才跳下一題，否則繼續追問
    - [TEST_COMPLETE] 暗號：所有考題問完且業務員回答後觸發，結束對話

    防幻覺機制：
    - 注入 product_name，讓 AI 知道正確產品名稱（即使語音辨識出錯也能對應）
    - 嚴格禁止 AI 自行發明產品功能，只能根據業務員說的來反應
    """
    client = anthropic.Anthropic(api_key=API_KEY)
    total_q = len(published_questions)

    # 只取考題第一行（題目本身），不洩漏建議答案給 AI 客戶
    def q_title(q: str) -> str:
        return q.split("\n")[0].strip()

    # ── 組裝當前任務指令 ──
    if current_q_idx < total_q:
        q_text = q_title(published_questions[current_q_idx])
        task_instruction = f"""
現在是第 {current_q_idx + 1} 個疑慮（共 {total_q} 關），這道考題的核心精神是：
{q_text}

【第一步：回應業務員剛才的說法】
用 1～2 句真實消費者的語氣給出反應（將信將疑、有點心動、或持續疑慮）。
若這是對話開場（業務員剛開口），直接以好奇但帶點警覺的消費者身份回應。

【第二步：判斷是否繼續追問，還是轉移到下一道疑慮】
你必須嚴格評估業務員的回答品質：

★ 如果業務員的回答讓你真正滿意（具體、有說服力、解決了你的疑慮）：
  → 自然地轉移到這道疑慮的用生活化口吻問出來
  → 並在你這次回覆的句尾加上暗號 [NEXT_Q]（緊接標點符號之後，不換行）
  → 範例：「...好啦，這樣聽起來還不錯。那我還想知道...[NEXT_Q]」

★ 如果業務員的回答不夠好（模糊、沒有具體說明、沒有解決你的疑慮）：
  → 不要跳到下一題，繼續從不同角度追問同一個疑慮
  → 不加任何暗號，繼續追問
  → 追問範例：「我聽你說...但我還是不太懂...」或「所以你的意思是...但這樣的話...」

轉換規則（無論追問或換題都要遵守）：
- 把考題精神翻譯成生活場景，絕對不能使用企業術語
- 語氣像朋友聊天，不是商務洽談
- 每次只問一個問題或提出一個疑慮

⚠️【暗號使用規則】
- [NEXT_Q] 只有在業務員回答讓你滿意並且你準備問下一道考題時才加
- [NEXT_Q] 必須緊接在句尾標點符號之後，不可單獨成行，不可在句中出現
- 禁止同時出現 [NEXT_Q] 和 [TEST_COMPLETE]
"""
    else:
        task_instruction = f"""
所有 {total_q} 道疑慮都問完了，業務員也已回答完畢。
現在你要根據這場對話中業務員的整體表現，給出真實的成交結局。

【判斷標準】請你回顧整場對話，評估以下三點：
1. 業務員有沒有清楚解釋產品的核心價值？
2. 業務員有沒有讓你感到被理解、被照顧？
3. 業務員有沒有主動提出讓你下決定的理由或誘因？

【根據評估，選擇以下其中一種結局】

結局A（業務員表現優秀，三點都做到）：
→ 你決定當場購買或給出明確的購買條件
→ 例如：「好，你說的這些讓我比較放心了。我決定先試試看，你說要怎麼下單？」
   或「如果你能幫我安排下週送到的話，我今天就決定了。」
→ 語氣是真心被說服，不是勉強

結局B（業務員表現普通，部分做到）：
→ 你有興趣但還需要一個推力
→ 例如：「嗯...你說的有些地方我覺得還不錯。但我想先問一下，現在買有什麼優惠嗎？或者我可以先試用看看？」
→ 給業務員一個繼續爭取的機會，等待業務員的回應
→ 選擇此結局時，不要加 [TEST_COMPLETE]，繼續等業務員回應

結局C（業務員表現不佳，大部分沒做到）：
→ 你禮貌但明確地拒絕
→ 例如：「謝謝你今天花時間介紹，但老實說我還是有點疑慮，我覺得這個產品目前不太適合我。」
→ 給出一個具體的拒絕理由

⚠️【暗號規則】
- 選擇結局B：不加任何暗號，等業務員繼續回應
- 選擇結局A或C：在最後一句話結束後緊接暗號 [TEST_COMPLETE]，不換行，不可單獨成行
"""

    # ── 根據訓練模式設定追問規則與回覆長度 ──
    if training_mode == "speed":
        max_tokens_val = 200
        followup_rule = """
【急速模式規則 - 嚴格執行】
每道考題你最多只能追問一次。
判斷流程：
- 業務員第一次回答 → 你提出這道考題的疑慮
- 業務員第二次回答 → 不管好壞，立刻給[NEXT_Q]或結束
- 絕對不能第三次追問同一個疑慮
回覆長度：最多50字，簡短有力
"""
    else:
        max_tokens_val = 400
        followup_rule = """
【深度模式規則】
業務員必須真正說服你才能過關。
- 回答不夠具體 → 繼續追問不同角度
- 回答含糊或背稿 → 用生活化反例反擊
- 直到業務員給出讓你真心滿意的回答才給[NEXT_Q]
回覆長度：可以60-80字，有深度
"""

    # 產品名稱標準化
    product_label = product_name if product_name else "這個產品"

    # 客戶情境（若主管有填寫就使用，否則給預設隨機背景）
    if customer_scenario and customer_scenario.strip():
        scenario_block = f"""【身份強制鎖定 — 客戶情境設定】
你始終是一位普通的 B2C 終端消費者（一般大眾），絕對不是企業高階主管或採購人員。
請嚴格根據以下情境來決定你的背景知識、態度與用語：
「{customer_scenario.strip()}」
請務必用極度生活化、自然的口吻說話，絕對禁止使用任何 B2B 商業術語或 SOP 等字眼。"""
    else:
        scenario_block = """【你的個人背景（隨機挑一種，全程保持一致）】
- 四十幾歲上班族：身體有些不舒服，想找健康相關產品改善生活品質
- 五十幾歲家庭主婦：想幫家人找合適的保健或舒緩產品
- 三十幾歲年輕人：預算有限，很在意 CP 值和有沒有實際效果
你始終是一位普通的 B2C 終端消費者（一般大眾），絕對不是企業高階主管或採購人員。"""

    # 組裝 2 道必考題清單（讓 AI 明確知道要問哪些題目）
    questions_list = "\n".join(
        f"  第{i+1}題：{q_title(q)}"
        for i, q in enumerate(published_questions)
    )

    system = f"""你是一位態度客氣、但對健康產品充滿未知與疑慮的普通消費者。
你沒有任何商業背景，說話就像在和朋友或家人聊天。

{scenario_block}

【本次推銷產品】
業務員正在向你推銷的產品叫做「{product_label}」。
就算對話中出現類似名稱的錯字或諧音（例如語音辨識出錯），你都要理解業務員指的是「{product_label}」。

【你對這個產品的認識程度】
你目前對「{product_label}」的細節完全不了解，你所知道的只有業務員在這場對話中告訴你的資訊。
你絕對不可以自己發明或猜測產品的功能、規格或特點。
若業務員沒提到某個功能，你就不知道它有那個功能。

【你心裡的必考題清單（依序提出，不可跳過）】
{questions_list}
你必須在整個對話中把這 {total_q} 道題目都以生活化口吻問出來，然後才能結束對話。

【產品背景知識（僅供你理解業務員描述時使用，不可主動洩漏）】
{analysis_context[:2500]}

{followup_rule}
【輸出格式鐵律 — 違反即視為嚴重錯誤】
- 你的回覆只能是消費者實際說出口的對話台詞，不可以有任何其他內容
- 嚴格禁止輸出：內部邏輯分析、狀態標籤、旁白、括號內的行動描述
- [NEXT_Q] 這個暗號只在你準備進入下一道考題時，緊接在句尾標點符號之後加上，不可單獨成行
- [TEST_COMPLETE] 這個暗號只在所有考題都問完並給出最終購買決定時使用，緊接在最後一個標點符號之後

【核心角色規則】
- 只扮演消費者，絕對不跳出角色、不說 AI 或助理的口吻
- 每次只問一個問題或提出一個疑慮，不可一次列多點
- 嚴禁：採購、福委會、員工福利、B2B、大量採購、導入方案、採購預算等企業詞彙
- 語氣：口語、自然、將信將疑，像真實消費者被推銷時的反應
- 業務員說得具體有說服力就「聽起來還不錯耶」；說得含糊就繼續追問

【當前任務】
{task_instruction}"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=max_tokens_val,
        system=system,
        messages=chat_history
    )
    ai_reply = response.content[0].text

    # [NEXT_Q] 暗號：AI 判定業務員回答夠好，才推進到下一道考題
    # 若沒有暗號，保持 current_q_idx 不變，繼續追問同一道
    if "[NEXT_Q]" in ai_reply:
        new_idx = min(current_q_idx + 1, total_q)
    else:
        new_idx = current_q_idx

    return ai_reply, new_idx


# ══════════════════════════════════════════════
# 頁面頂部 Logo
# ══════════════════════════════════════════════
col_logo, _ = st.columns([1, 4])
with col_logo:
    st.markdown("### 🎯 PitchCoach")
st.markdown("---")
st.markdown('<p class="hero-title">企業中控台 v3</p>', unsafe_allow_html=True)
st.markdown('<p class="hero-subtitle">主管中控台負責設定訓練劇本；員工實戰沙盒提供 AI 客戶角色扮演練習。</p>', unsafe_allow_html=True)


# ══════════════════════════════════════════════
# 雙頁籤主架構
# ══════════════════════════════════════════════
tab1, tab2, tab3 = st.tabs([
    "　⚙️　模塊一：企業中控台　",
    "　🎮　模塊二：實戰沙盒　",
    "　📊　模塊三：戰後報表台　",
])


# ╔══════════════════════════════════════════════╗
# ║  TAB 1：主管中控台                            ║
# ╚══════════════════════════════════════════════╝
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
                        # 清除員工端舊的對話記錄與按需生成的 TTS 快取
                        st.session_state.pop("chat_history", None)
                        st.session_state.pop("current_q_idx", None)
                        for i in range(50):
                            st.session_state.pop(f"tts_audio_{i}", None)

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
                        for i in range(50):
                            st.session_state.pop(f"tts_audio_{i}", None)
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
                        for i in range(50):
                            st.session_state.pop(f"tts_audio_{i}", None)
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

        main_analysis       = st.session_state.get("main_analysis", "")
        total_q             = len(published_questions)

        # 初始化聊天 session state（只在第一次進入時執行）
        if "chat_history" not in st.session_state:
            st.session_state["chat_history"]  = []
            st.session_state["current_q_idx"] = 0

        current_q_idx = st.session_state["current_q_idx"]
        chat_history  = st.session_state["chat_history"]

        # ── 員工姓名輸入（只在尚未開始對話時顯示）────
        if not chat_history:
            st.markdown("### 👤 請輸入你的姓名")
            st.caption("姓名將用於訓練記錄，讓主管追蹤你的學習進度。")

            col_name, col_start = st.columns([3, 1])
            with col_name:
                employee_name_input = st.text_input(
                    label="員工姓名",
                    placeholder="例如：王小明",
                    key="employee_name_input",
                    label_visibility="collapsed"
                )
            with col_start:
                name_confirm = st.button(
                    "✅ 確認開始",
                    type="primary",
                    use_container_width=True,
                    disabled=not employee_name_input.strip()
                )

            if name_confirm and employee_name_input.strip():
                st.session_state["employee_name"] = employee_name_input.strip()
                st.rerun()

            # 尚未確認姓名則停止渲染後面的內容
            if not st.session_state.get("employee_name"):
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

                            # TTS 按需生成：有快取直接播放，沒有則顯示按鈕
                            tts_key = f"tts_audio_{i}"
                            if st.session_state.get(tts_key):
                                st.audio(st.session_state[tts_key], format="audio/mp3", autoplay=False)
                            else:
                                if st.button("🔊 聆聽語音", key=f"tts_btn_{i}"):
                                    with st.spinner("生成中..."):
                                        audio = generate_tts_audio(display_content)
                                        st.session_state[f"tts_audio_{i}"] = audio
                                    st.rerun()

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
                                product_benefits    = st.session_state.get("product_benefits", "")
                            )
                            st.session_state["evaluation_report"] = auto_report

                            # 自動儲存到 Supabase
                            try:
                                sb            = get_supabase()
                                company_id    = get_or_create_company("智云健康股份有限公司")
                                employee_name = st.session_state.get("employee_name", "匿名員工")
                                t_mode        = st.session_state.get("training_mode", "speed")
                                if company_id:
                                    session_result = sb.table("sessions").insert({
                                        "company_id":    company_id,
                                        "chat_history":  st.session_state.get("chat_history", []),
                                        "is_completed":  True,
                                        "employee_name": employee_name,
                                        "training_mode": t_mode,
                                    }).execute()
                                    session_id = session_result.data[0]["id"] if session_result.data else None
                                    if session_id:
                                        sb.table("scores").insert({
                                            "session_id":     session_id,
                                            "company_id":     company_id,
                                            "employee_name":  employee_name,
                                            "score":          auto_report.get("score", 0),
                                            "bonus_unlocked": auto_report.get("bonus_unlocked", False),
                                            "left_brain":     auto_report.get("left_brain", ""),
                                            "right_brain":    auto_report.get("right_brain", ""),
                                            "action_item":    auto_report.get("action_item", ""),
                                            "closing_result": auto_report.get("closing_result", ""),
                                        }).execute()
                                        print("[Supabase] 報告自動儲存成功")
                            except Exception as e:
                                print(f"[Supabase警告] 自動儲存失敗：{e}")

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
            _preview = audio_value.read(); audio_value.seek(0)
            st.caption(f"🔍 Debug｜錄音大小：{len(_preview)} bytes")
            with st.spinner("🔄 辨識中..."):
                recognized = speech_to_text(audio_value)
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
                except Exception as e:
                    ai_reply = f"（系統錯誤：{str(e)}，請重新整理後再試）"
                    new_idx  = st.session_state["current_q_idx"]

            # 步驟 3：將 AI 回覆加入記錄，並更新題目索引
            st.session_state["chat_history"].append(
                {"role": "assistant", "content": ai_reply}
            )
            st.session_state["current_q_idx"] = new_idx

            # 步驟 4：不預先生成 TTS，讓畫面立即渲染文字
            # 用戶點擊「🔊 聆聽語音」按鈕時才觸發生成（按需模式）

            # 步驟 5：若教練輔助模式已開啟，自動在背景呼叫教練取得戰術提示
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

    # ── 視角切換 ──────────────────────────────────────
    view_mode = st.radio(
        label="選擇視角",
        options=["🏢 管理者總覽（全隊成績）", "📋 個人報告（本次訓練）"],
        horizontal=True,
        key="report_view_mode"
    )
    st.markdown("---")

    # ══════════════════════════════════════════════════
    # 視角一：管理者總覽
    # ══════════════════════════════════════════════════
    if view_mode == "🏢 管理者總覽（全隊成績）":
        try:
            from collections import defaultdict
            import pandas as pd

            sb = get_supabase()
            company_id = get_or_create_company("智云健康股份有限公司")

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
                pass_count     = sum(1 for s in scores_data if s.get("score", 0) >= 80)
                pass_rate      = (pass_count / total_sessions) * 100
                bonus_count    = sum(1 for s in scores_data if s.get("bonus_unlocked"))

                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("📚 總訓練次數", f"{total_sessions} 次")
                with col2:
                    st.metric("📊 團隊平均分", f"{avg_score:.1f} 分")
                with col3:
                    st.metric("🏅 達標率", f"{pass_rate:.0f}%",
                              help="分數 ≥ 80 分視為達標")
                with col4:
                    st.metric("💰 獎金解鎖次數", f"{bonus_count} 次")

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
                        "員工姓名":   name,
                        "最高分":     max(sl),
                        "平均分":     f"{sum(sl)/len(sl):.1f}",
                        "訓練次數":   len(sl),
                        "獎金達標次數": stats["bonus_count"],
                        "最後訓練日期": stats["last_training"],
                    })
                leaderboard.sort(key=lambda x: x["最高分"], reverse=True)

                medals = ["🥇", "🥈", "🥉"]
                for i, row in enumerate(leaderboard):
                    row["名次"] = medals[i] if i < 3 else f"#{i+1}"

                df = pd.DataFrame(leaderboard)
                cols = ["名次", "員工姓名", "最高分", "平均分", "訓練次數", "獎金達標次數", "最後訓練日期"]
                st.dataframe(df[cols], use_container_width=True, hide_index=True)

                st.markdown("---")

                # ── 團隊弱點分析 ──────────────────────────
                st.markdown("### 🧠 團隊弱點分析")

                avg_left  = avg_score * 0.35
                avg_right = avg_score * 0.35
                col_weak1, col_weak2 = st.columns(2)
                with col_weak1:
                    st.metric("🔵 左腦邏輯平均", f"{avg_left:.1f} / 35 分")
                with col_weak2:
                    st.metric("🔴 右腦溝通平均", f"{avg_right:.1f} / 35 分")

                if leaderboard:
                    weakest = min(leaderboard, key=lambda x: float(x["平均分"]))
                    st.warning(
                        f"⚠️ **培訓建議**：{weakest['員工姓名']} 的平均分為 "
                        f"{weakest['平均分']} 分，建議安排一對一輔導。"
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
        is_completed = st.session_state.get("is_completed", False)
        chat_history_for_report = st.session_state.get("chat_history", [])
        published_questions_for_report = st.session_state.get("published_questions", [])

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
