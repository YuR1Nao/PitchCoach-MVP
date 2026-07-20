import streamlit as st
import anthropic
import openai
import edge_tts
import asyncio
import concurrent.futures
import re
import json
import io
import emoji
import fitz
import random
import difflib
from config import (API_KEY, OPENAI_API_KEY, EDGE_TTS_VOICE,
                    EDGE_TTS_RATE, CATEGORY_LABELS, TOTAL_QUESTION_LIMIT)


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

    except Exception as e:
        print(f"[TTS錯誤] {type(e).__name__}: {str(e)}")
        return None


def speech_to_text(
    audio_file,
    hint_text: str = "",
    product_name: str = "",
    audio_filename: str = "audio.webm",
    audio_mime_type: str = "audio/webm",
) -> str | None:
    """
    使用 OpenAI Whisper API 將音訊轉成繁體中文文字。

    優勢（對比 Google STT）：
    - 支援台語、中英混雜、口音、專有名詞
    - 直接接受瀏覽器錄製的 WebM/Opus 格式，無需格式轉換
    - 抗噪能力強，低品質麥克風也能準確辨識

    audio_filename / audio_mime_type：
    - 這個函式被網頁版（瀏覽器錄音，實際格式是 webm/opus）和 LINE 版
      （LINE 官方下載的語音訊息，實際格式是 m4a）共用，兩邊音檔的真實
      容器格式不同，必須各自傳入正確的副檔名與 MIME 類型，Whisper 才能
      正確解碼；標錯格式時常常不會丟出例外，而是安靜地回傳空字串或
      亂碼，不容易從錯誤訊息察覺。
    - 網頁版沿用預設值（webm），呼叫端不需修改。
    - LINE 版呼叫時必須傳入 audio_filename="audio.m4a"、
      audio_mime_type="audio/m4a"。

    失敗情況（全部靜默回傳 None，不崩潰）：
    - AuthenticationError：OpenAI API Key 錯誤
    - 網路連線失敗、音檔損毀、其他未知錯誤
    """
    try:
        client      = openai.OpenAI(api_key=OPENAI_API_KEY)
        audio_bytes = audio_file.read()
        if len(audio_bytes) < 500:
            print(f"⚠️ 麥克風收到資料量過少（{len(audio_bytes)} bytes）")
            return None

        # 副檔名與 MIME 類型必須對應音檔的真實格式，Whisper 才能正確解碼
        transcript = client.audio.transcriptions.create(
            model    = "whisper-1",
            file     = (audio_filename, io.BytesIO(audio_bytes), audio_mime_type),
            language = "zh",
            prompt   = hint_text if hint_text else "業務推廣，產品說明，客戶疑慮",
        )

        recognized_text = transcript.text.strip()

        # 品牌名稱修正：對每個詞和產品名稱做相似度比對，超過 60% 自動替換
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
        print("❌ OpenAI API Key 無效，請確認 .env 裡的 OPENAI_API_KEY")
        return None
    except Exception as e:
        print(f"❌ 語音辨識失敗：{type(e).__name__}: {str(e)}")
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

    # 告訴教練目前面臨的是哪一道考題（僅作參考，不強制教練依此判斷）
    if current_q_idx < len(published_questions):
        q_title   = published_questions[current_q_idx].split("\n")[0]
        q_context = f"系統排定的下一道疑慮（僅供參考）：{q_title}"
    else:
        q_context = "系統顯示所有疑慮都已涵蓋（僅供參考）。"

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

【最優先原則】
你必須根據「對話記錄」裡客戶最後一句話實際在問什麼、擔心什麼來給建議，
不要根據下方的系統排定題目來判斷，因為系統題號可能已經推進到下一題，
但客戶剛才那句話可能還在講前一個疑慮。永遠以客戶最後一句話的真實內容為準。

{q_context}

【產品真實賣點】
{benefits_block}

【目標客群】
{audience_block}

你的任務：仔細閱讀對話記錄中客戶最後一句話，抓出他真正在問的具體問題，
針對「那個具體問題」給出「下一句話應該怎麼說」的方向。

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
        model="claude-sonnet-5",
        thinking={"type": "disabled"},
        max_tokens=300,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}]
    )
    return response.content[0].text.strip()


def get_evaluation_report(
    chat_history: list[dict],
    published_questions: list[str],
    customer_scenario: str = "",
    product_benefits: str = "",
    training_mode: str = "speed"
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

    _scoring_block = (
        """
【急速模式 — 不評估成交能力】
- 左腦邏輯（50 分）：賣點覆蓋率、關鍵資訊準確性、回應客戶疑慮的完整度
- 右腦溝通（50 分）：語氣自然度、同理心表達、是否成功降低客戶疑慮、說話方式是否貼近客戶情境
- 成交能力：本模式不評估，closing_result 固定填「急速模式不評估成交」
"""
        if training_mode == "speed" else
        """
【深度模式 — 完整評估三大維度】
- 左腦邏輯（35 分）：賣點覆蓋率、關鍵資訊準確性、回應客戶疑慮的完整度
- 右腦溝通（35 分）：語氣自然度、同理心表達、是否成功降低客戶疑慮、說話方式是否貼近客戶情境
- 成交能力（30 分）：是否識別購買信號、是否主動創造成交條件、最終客戶是否成交或給出明確購買意願
"""
    )

    _score_fields_block = (
        '"left_brain_score": <左腦邏輯分數，整數 0~50，必須與 left_brain 文字評語互相對應>,\n'
        '  "right_brain_score": <右腦溝通分數，整數 0~50，必須與 right_brain 文字評語互相對應>,\n'
        '  "closing_score": 0,'
        if training_mode == "speed" else
        '"left_brain_score": <左腦邏輯分數，整數 0~35，必須與 left_brain 文字評語互相對應>,\n'
        '  "right_brain_score": <右腦溝通分數，整數 0~35，必須與 right_brain 文字評語互相對應>,\n'
        '  "closing_score": <成交能力分數，整數 0~30，必須與 closing_result 的結果互相對應>,'
    )

    _closing_rule_block = (
        """closing_result 填寫規則（急速模式）：
- 急速模式不評估成交，closing_result 必須固定填寫「急速模式不評估成交」這個字串
- 絕對不可以填寫「當場成交」「有條件延遲」「明確拒絕」這三個選項中的任何一個，
  即使對話內容看起來像是在談合作意願、預算、導入時程等商業決策，也一律固定填
  「急速模式不評估成交」，不要自己判斷要不要成交"""
        if training_mode == "speed" else
        """closing_result 填寫規則（深度模式）：
- 「當場成交」：客戶明確表示要購買或給出具體購買條件
- 「有條件延遲」：客戶有興趣但要求優惠/試用/再想想
- 「明確拒絕」：客戶禮貌但清楚地拒絕"""
    )

    system_prompt = f"""你是一位嚴格但客觀的企業銷售總監，正在為業務員做最終戰力評估。
你必須分析業務員在剛才對話中的表現，並嚴格輸出以下 JSON 格式，不可包含任何其他文字：

{{
  "score": <整數 0~100>,
  {_score_fields_block}
  "left_brain": "<左腦邏輯分析：約 80 字。業務員是否精準命中產品賣點？有無漏掉關鍵資訊？>",
  "right_brain": "<右腦溝通分析：約 80 字。面對客戶情境，語氣是否具備同理心？是否太過生硬或照本宣科？>",
  "action_item": "<給主管的一句話培訓建議，例如：建議安排同理心溝通訓練，強化用故事代替數據的能力。>",
  "closing_result": "<成交結果：當場成交 / 有條件延遲 / 明確拒絕>",
  "strength": "<業務員這次做得最好的一件事，一句話，例如：你在回應價格異議時引用了具體數據，讓客戶信服度明顯提升。>",
  "improvement_tips": [
    "<第一個具體改善建議，針對這次對話的真實缺失，例如：客戶詢問副作用時你沒有正面回應，下次先承認疑慮再轉向優勢。>",
    "<第二個具體改善建議>",
    "<第三個具體改善建議>"
  ]
}}

評分維度（共 100 分）：
""" + _scoring_block + f"""
{_closing_rule_block}

left_brain_score / right_brain_score / closing_score 填寫規則：
- 這三個數字必須是你打出 left_brain、right_brain、closing_result 文字評語時實際依據的分數，不可以事後隨便填一個跟文字評語矛盾的數字
- 三個分數加總後應該非常接近（但不強制等於）最終的 score

improvement_tips 填寫規則：
- 必須針對這次對話的真實問題，不能給空洞建議
- 每條建議格式：「你＿＿，下次建議＿＿」
- 如果業務員表現很好，tips 可以少於 3 條，但至少 1 條

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
        model="claude-sonnet-5",
        thinking={"type": "disabled"},
        max_tokens=1500,
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
        "left_brain_score": 0,
        "right_brain_score": 0,
        "closing_score": 0,
        "left_brain": "（AI 評分解析失敗，請重新產生報告）",
        "right_brain": "（AI 評分解析失敗，請重新產生報告）",
        "action_item": "請重新按下『產生報告』按鈕。",
        "strength": "（解析失敗）",
        "improvement_tips": [],
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
        model="claude-sonnet-5",
        thinking={"type": "disabled"},
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
    "cat_org_trust":     [],
    "cat_rules_info":    [],
}


def _trim_questions_to_limit(questions_dict: dict, limit: int = TOTAL_QUESTION_LIMIT) -> tuple[dict, dict]:
    """
    程式碼層的保險：無論 AI 是否確實遵守 system prompt 裡「總數最多
    {limit} 題」的指令，這裡都會實際加總確認，超過上限時才裁切，
    確保資料庫不會被灌入超量題目（LLM 對數字上限的遵守度不是100%可靠，
    不能只靠 prompt 文字約束）。

    裁切策略：每次裁掉「目前題目數最多的那個類別」的最後一題，
    輪流裁到總數符合上限為止，避免像切片一樣讓後面的類別被整批犧牲，
    盡量維持各類別的覆蓋度與多樣性。

    回傳：(裁切後的 questions_dict, meta)
    meta 包含 total_generated（AI原本生成的總數）、
    total_kept（裁切後保留的總數）、was_trimmed（是否有發生裁切）。
    """
    trimmed = {k: list(v) for k, v in questions_dict.items()}
    total_generated = sum(len(v) for v in trimmed.values())

    while sum(len(v) for v in trimmed.values()) > limit:
        richest_cat = max(trimmed, key=lambda k: len(trimmed[k]))
        trimmed[richest_cat].pop()

    total_kept = sum(len(v) for v in trimmed.values())
    meta = {
        "total_generated": total_generated,
        "total_kept": total_kept,
        "was_trimmed": total_generated > limit,
    }
    return trimmed, meta


def generate_questions_json(document_text: str, existing_questions: list = None) -> tuple[dict, dict]:
    """
    第二次 API 呼叫：按 7 大類別智能生成考題（其中6類是模擬客戶疑慮/反對
    的刁難考題，1類是中性、不帶負面情緒的規則詢問），
    全部類別「加總」最多 TOTAL_QUESTION_LIMIT（目前=30）題，
    各類別彼此之間不設個別上限，完全依材料在各類別的豐富程度動態分配。

    回傳格式：tuple(questions_dict, meta)
    questions_dict：
    {
        "cat_1_product":     [N 道題 str, ...],
        "cat_2_price":       [N 道題 str, ...],
        "cat_3_trust":       [N 道題 str, ...],
        "cat_4_competition": [N 道題 str, ...],
        "cat_5_decision":    [N 道題 str, ...],
        "cat_org_trust":     [N 道題 str, ...],
        "cat_rules_info":    [N 道題 str, ...],
    }
    （N 由 AI 依材料豐富度自行判斷，各類別題數可能不同，加總不超過上限；
    cat_org_trust 是組織/商業模式信任疑慮，跟 cat_3_trust 的產品信任疑慮不同；
    cat_rules_info 是中性、不帶疑慮語氣的規則/資格詢問，跟其他類別的「刁難」
    性質不同，只有材料涉及直銷、保險、加盟等需要建立組織信任或有制度規則的
    商業模式時才會有內容）

    meta：{"total_generated": int, "total_kept": int, "was_trimmed": bool}
    供呼叫端判斷是否需要提醒使用者「材料豐富度超過單次上限，已裁切」。

    解析策略（三層防護）：
    1. json.loads() 直接解析 JSON 物件
    2. regex 找出 { ... } 子串後解析
    3. 全部失敗 → 使用空的 _EMPTY_CATEGORIES
    最後一律經過 _trim_questions_to_limit() 做總量保險裁切。
    """
    client = anthropic.Anthropic(api_key=API_KEY)

    # 組出「避免重複」提示區塊：只取既有題目裡「題目本身」（👉之前的部分），
    # 不含建議回答方向，保持prompt精簡。沒有既有題目時（例如公司第一次上傳）
    # 這段會是空字串，system_prompt裡對應的位置也會自然變成空白，不影響出題。
    existing_block = ""
    if existing_questions:
        _existing_q_only = []
        for _q in existing_questions:
            _q_part = _q.split("👉")[0].strip()
            if _q_part:
                _existing_q_only.append(_q_part)
        if _existing_q_only:
            _existing_list_str = "\n".join(f"- {q}" for q in _existing_q_only)
            existing_block = f"""
【避免重複】
以下是這家公司題庫裡已經存在的題目（只列出題目本身，不含建議回答方向），
生成新題目時請參考：
{_existing_list_str}

- 如果新題目問法跟上面任何一題幾乎一模一樣（只是換了幾個字、語序調整、
  同義詞替換），視為重複，不要生成
- 如果新題目雖然討論同一個疑慮方向，但用了不同的情境、不同的具體細節、
  或不同的切入角度來問，仍然算是有效的新題目，可以生成——不需要因為
  「主題聽起來類似」就跳過，只擋「幾乎照抄」的重複
- 如果這份文件裡沒有任何真正新的素材、內容全部跟既有題目重複，該類別
  可以回傳空陣列 []，不用勉強湊題
"""

    system_prompt = """你是一個專業的銷售訓練專家。
請根據文件內容，為以下7個類別智能判斷並生成考題。

【核心原則】
- 只根據文件中確實存在的內容出題
- 如果某個類別在文件中找不到足夠的素材，請回傳空陣列 []，絕對不要捏造問題
- 寧可少出題，也不要生成不符合這個產品常識的問題
- 每道題必須是客戶在真實情境下可能說出的話，不是產品說明
- 不要在題目裡出現「文件」「第N頁」等詞彙
- 除了 cat_rules_info 以外的6個類別，題目應該帶有疑慮、反對、或挑戰意味；
  cat_rules_info 則相反，題目應該是中性、單純好奇的規則/資格詢問，不要為
  了讓題目「聽起來像刁難」而刻意加入負面情緒，這類題目一樣重要
{EXISTING_QUESTIONS_BLOCK}
【強制輸出格式】
你的整個回覆必須是一個合法的JSON物件：
{
  "cat_1_product": ["Q1. 客戶問題 👉 建議回答方向：具體建議", "Q2. ..."],
  "cat_2_price": ["Q1. 客戶問題 👉 建議回答方向：具體建議"],
  "cat_3_trust": [],
  "cat_4_competition": [],
  "cat_5_decision": ["Q1. 客戶問題 👉 建議回答方向：具體建議"],
  "cat_org_trust": ["Q1. 客戶問題 👉 建議回答方向：具體建議"],
  "cat_rules_info": ["Q1. 客戶問題 👉 建議回答方向：具體建議"]
}

【七大類別定義】

cat_1_product（產品理解類）：
客戶對產品本身的疑慮：使用方式、適用族群、禁忌症、注意事項。
✅ 屬於這類：「孕婦可以用嗎」「要用多久才有效」「心臟病可以用嗎」
❌ 不屬於這類：價格、效果保證
⚠️ 只出文件中有明確提到的功能或特性相關問題，不要假設產品有任何未提到的功能

cat_2_price（價格異議類）：
客戶對金錢的疑慮：太貴、預算不夠、CP值、要比價、付款方式。
✅ 屬於這類：「這個價格偏高」「有沒有分期」「有沒有折扣」
⚠️ 如果文件沒有提到價格資訊，仍可根據產品性質出通用價格異議題

cat_3_trust（信任疑慮類）：
客戶對「產品本身」效果與安全的疑慮：沒效怎麼辦、副作用、有沒有認證。
✅ 屬於這類：「萬一沒效怎麼辦」「有沒有副作用」「有認證嗎」
❌ 不屬於這類：能不能保證我賺得到錢、能不能保證我升上代理商/某個資格、
獎金/分紅是不是真的——這些是「商業承諾」的保證，不是「產品效果」的保證，
屬於 cat_org_trust，不要因為題目裡出現「保證」兩個字就直接歸類到這裡，
要先判斷保證的對象是產品還是收入/晉升
⚠️ 只根據文件中有提到的認證、保證、效果說明來出題

cat_4_competition（競品比較類）：
客戶提到其他選擇：市面上有類似的、網路上有便宜的。
⚠️ 只有在文件中有明確提到競品差異或市場定位時才出這類題，否則回傳 []

cat_5_decision（決策障礙類）：
客戶心動但拖延：「再想想」「問家人」「下次再說」。
✅ 屬於這類：「我考慮一下」「要跟家人商量」「這個月預算用完了」
⚠️ 這類題通常適用於任何產品，可以根據產品客群特性調整

cat_org_trust（組織與商業模式疑慮類）：
客戶對「這家公司／這個經營方式／這個商業模式」本身的懷疑，不是對產品本身
效果或安全的懷疑，這兩種疑慮性質不同，不要混為一談。
✅ 屬於這類：「這是不是直銷／老鼠會」「是不是要拉我當下線／會員」「你們公司
到底想幹嘛」「聽起來像話術／情感壓力」「說的願景／數字是不是誇大」「這是不
是騙人的公司」「能不能保證我賺得到錢」「能不能保證我升上某個代理等級／資格」
「獎金/分紅/收入承諾是不是真的」——只要疑慮的對象是「收入、獎金、晉升、
組織承諾」，即使題目裡沒有明講「老鼠會」，也屬於這一類
❌ 不屬於這類：產品有沒有效、產品貴不貴、跟其他家產品比較——這些仍應歸類到
cat_1_product／cat_2_price／cat_3_trust／cat_4_competition；純粹好奇規則
怎麼運作、語氣中性不帶懷疑的提問（例如「要達到什麼條件才能升級」）歸類到
cat_rules_info，不是這裡——差別在於語氣是「質疑」還是「單純詢問」
⚠️ 這類疑慮常見於直銷、保險、加盟、社群電商這種「需要先讓客戶信任組織／經
營者本身，才能談產品」的商業模式。如果文件是單純零售型產品說明（例如一般
3C 產品、家電），材料裡通常不會有這類疑慮內容，此時應誠實回傳空陣列 []，
不要為了填滿這個類別而勉強生成不符合材料情境的題目

cat_rules_info（規則與資格說明類）：
客戶單純好奇、想搞懂制度規則、資格門檻、專有名詞定義，語氣中性，不帶疑慮、
質疑或反對情緒——這跟其他類別「有疑慮/想刁難」的出發點不同，是客戶單純
「想知道」，業務員需要能清楚、正確地把規則講明白。
✅ 屬於這類：「要達到什麼條件才能升級／晉升」「這個專有名詞是什麼意思」
「這個制度的門檻／資格怎麼算」「多久之後可以做這件事」
❌ 不屬於這類：帶有懷疑或反對語氣的提問（例如「這個門檻是不是故意設很
高」這種該歸類到 cat_org_trust，不是這裡）；純粹的效果保證疑慮仍歸
cat_3_trust
⚠️ 這類題通常出現在有明確制度、等級、資格門檻的商業模式（例如直銷的
代理等級、保險的保費級距、會員制度的權益門檻）。如果材料本身沒有這種
制度/規則的複雜度，材料裡通常不會有這類內容，此時應誠實回傳空陣列 []

【好題目 vs 壞題目對照範例】

以下範例可以幫助你判斷什麼是合格的題目（這些只是格式範例，實際內容必須根據上傳文件的真實產品）：

✅ 好題目（有憑有據，符合常識）：
「這個要用多久才會有效？我平常很忙沒時間管」
（原因：文件裡通常會提到使用頻率或見效時間，這是客戶真實會問的問題）

「我看網路上有人說用了之後反而更不舒服，這是正常的嗎？」
（原因：文件裡如果提到「好轉反應」或副作用說明，這類疑慮才有依據回答）

❌ 壞題目（脫離常識或無中生有）：
「請問這個要怎麼充電？」
（原因：如果文件從未提及產品需要充電，此題就是憑空捏造，違反產品常識）

「請問文件第3頁提到的認證是真的嗎？」
（原因：題目裡不該出現「文件第幾頁」這種結構性詞彙，客戶不會這樣說話）

自我檢查：出題前，先問自己「文件裡有沒有明確的依據支持這一題？」
如果答案是「沒有」，這一題就不該出現，該類別就少出一題或回傳空陣列。

【重要規定】
- 只輸出JSON物件，不要有任何其他文字
- 每道題格式：「Q數字. 客戶說的話 👉 建議回答方向：具體建議」
- 客戶問題必須是客戶會說的話，不是產品說明
- 建議回答方向必須根據文件的真實資訊撰寫，不要憑空捏造
- 全部7個類別「加總」最多{TOTAL_LIMIT}題，各類別彼此之間不設個別上限，
  完全由你依材料在各類別的豐富程度智能分配（某類別材料特別豐富可以多分配
  幾題，某類別材料稀薄就少分配甚至回傳空陣列）
- 沒有足夠素材的類別請回傳空陣列 []
- 品質優先於數量：絕對禁止為了「湊到{TOTAL_LIMIT}題」而生成語意重複、只是
  換句話說的相似題目，也絕對禁止為了展示自己能生成很多題目而降低出題標準。
  每一題都必須通過前面「自我檢查」段落的標準，寧可總數只有一半但每題都
  高品質，也不要硬湊滿但其中有多題重複或牽強
""".replace("{TOTAL_LIMIT}", str(TOTAL_QUESTION_LIMIT)).replace("{EXISTING_QUESTIONS_BLOCK}", existing_block)

    response = client.messages.create(
        model="claude-sonnet-5",
        thinking={"type": "disabled"},
        max_tokens=8192,
        system=system_prompt,
        messages=[{"role": "user", "content": f"以下是教材內容：\n\n{document_text}"}]
    )
    raw = response.content[0].text.strip()

    # 第一步：移除 Markdown 程式碼區塊標記（Claude 有時會用 ```json ... ``` 包住 JSON）
    cleaned = re.sub(r'```json', '', raw)
    cleaned = re.sub(r'```', '', cleaned)
    cleaned = cleaned.strip()

    parsed_result = None

    # ── 第一層：直接 json.loads 解析 ────────────────
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            for key in _EMPTY_CATEGORIES:
                if key not in result:
                    result[key] = []
            print(f"[DEBUG] 解析成功！各類別題數：{ {k: len(v) for k, v in result.items()} }")
            parsed_result = result
    except (json.JSONDecodeError, ValueError):
        pass

    # ── 第二層：找出 { } 子字串再解析 ───────────────
    if parsed_result is None:
        try:
            start = cleaned.index("{")
            end   = cleaned.rindex("}") + 1
            result = json.loads(cleaned[start:end])
            if isinstance(result, dict):
                for key in _EMPTY_CATEGORIES:
                    if key not in result:
                        result[key] = []
                print("[DEBUG] 第二層解析成功")
                parsed_result = result
        except (ValueError, json.JSONDecodeError):
            pass

    # ── 第三層：逐類別用 regex 提取各陣列 ───────────
    if parsed_result is None:
        try:
            result = dict(_EMPTY_CATEGORIES)
            for cat_key in _EMPTY_CATEGORIES:
                pattern = rf'"{cat_key}"\s*:\s*(\[.*?\])'
                match = re.search(pattern, cleaned, re.DOTALL)
                if match:
                    result[cat_key] = json.loads(match.group(1))
            if any(len(v) > 0 for v in result.values()):
                print("[DEBUG] 第三層解析成功")
                parsed_result = result
        except Exception:
            pass

    if parsed_result is None:
        print("[DEBUG] 所有解析失敗")
        parsed_result = dict(_EMPTY_CATEGORIES)

    # ── 程式碼層保險：無論AI是否確實遵守總數上限，這裡實際加總並視需要裁切 ──
    trimmed_result, trim_meta = _trim_questions_to_limit(parsed_result, TOTAL_QUESTION_LIMIT)
    if trim_meta["was_trimmed"]:
        print(f"[DEBUG] 題目超過上限，已從 {trim_meta['total_generated']} 題裁切為 {trim_meta['total_kept']} 題")

    return trimmed_result, trim_meta


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
    Claude 扮演對「這個產品/服務」有疑慮、需要被說服的角色，身份完全由
    customer_scenario 決定（無自訂情境時 fallback 為一般 B2C 消費者）。

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

⚠️【收尾規則 — 避免不公平評分】
如果你選擇結局A或C（對話會就此結束），這句話必須是「完整的收尾」，絕對禁止
在這句話裡順便拋出一個全新、業務員完全沒有機會回應的問題或疑慮（例如報價
方式、後續流程、客製化需求這類還沒被討論過的新話題）。既然這句話講完對話
就結束了，任何在這句話裡才第一次提出的新問題，業務員都不可能有機會回答，
之後評分若把「沒回應這個來不及回答的新問題」算成業務員的缺失，對業務員
並不公平。如果你原本想順帶問一個新問題，請把它拿掉，只保留對整場對話的
真實反應與收尾決定即可。

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
        realism_rule = ""
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
        realism_rule = """
【深度模式 — 真實感加強規則】
- 表達疑慮時不必每次都講得明確具體，可以偶爾用模糊、不直接的方式帶過
  （例如「我再想想」「這個我要問一下我老公」「感覺怪怪的，先這樣好了」），
  逼業務員主動追問、挖掘出你真正在意的點，不要把疑慮直接攤開給他猜。
- 如果業務員後續的回答讓你對前面已經放行過的疑慮又產生一絲懷疑，可以用一句話
  自然帶回去（例如「欸等等，你剛剛講的跟你前面說的好像有點不一樣」），這只是
  語氣上的反覆與真實感，不代表要重新評估那個疑慮本身，仍然依照當前這一題的
  追問規則繼續判斷。
"""

    # 產品名稱標準化
    product_label = product_name if product_name else "這個產品"

    # 客戶情境（若主管有填寫就使用，否則給預設隨機背景）
    # 修正：主管自訂情境時，身份完全依情境內容決定，不再強制鎖定為 B2C 消費者、
    # 不再禁止 B2B 用語，這樣才能支援保險、房仲、企業客戶等非健康產品情境
    if customer_scenario and customer_scenario.strip():
        scenario_block = f"""【身份設定 — 依情境決定】
請嚴格根據以下情境來決定你的身份、背景知識、態度與用語，這個情境設定的
優先權高於任何其他預設假設：
「{customer_scenario.strip()}」
請根據這個情境判斷合理的說話方式：如果情境描述的是一般消費者，就用生活化口吻；
如果情境描述的是企業主管、採購人員或專業人士，就自然使用符合該身份的用語與
考量點（例如導入、預算、資源分配等），不要因為這是業務練習就強制假裝成一般
消費者，也不要刻意迴避符合角色身份的專業詞彙。"""
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

    # 急速模式維持嚴格依序，深度模式放寬順序限制、允許疑慮交織
    if training_mode == "speed":
        questions_order_rule = f"""【你心裡的必考題清單（依序提出，不可跳過）】
{questions_list}
你必須在整個對話中把這 {total_q} 道題目都以生活化口吻問出來，然後才能結束對話。"""
    else:
        questions_order_rule = f"""【你心裡的必考題清單】
{questions_list}
你必須在整個對話中把這 {total_q} 道題目都以生活化口吻問出來，然後才能結束對話，
但不強制依照條列順序一題一題乾淨地問——可以視對話自然發展微調順序、或在同一句話
裡順帶夾帶另一個疑慮的影子，讓對話更像真實客戶疑慮交織在一起的樣子，而不是照表
操課。"""

    system = f"""你正在扮演一個對「{product_label}」這個產品或服務有疑慮、需要被業務員說服的角色。
你的身份、背景與說話方式，完全由下方的情境設定決定，不要自行假設。

{scenario_block}

【本次推銷產品】
業務員正在向你推銷的產品叫做「{product_label}」。
就算對話中出現類似名稱的錯字或諧音（例如語音辨識出錯），你都要理解業務員指的是「{product_label}」。

【你對這個產品的認識程度】
你目前對「{product_label}」的細節完全不了解，你所知道的只有業務員在這場對話中告訴你的資訊。
你絕對不可以自己發明或猜測產品的功能、規格或特點。
若業務員沒提到某個功能，你就不知道它有那個功能。

{questions_order_rule}

【產品背景知識（僅供你理解業務員描述時使用，不可主動洩漏）】
{analysis_context[:2500]}

{followup_rule}
{realism_rule}
【輸出格式鐵律 — 違反即視為嚴重錯誤】
- 你的回覆只能是角色實際說出口的對話台詞，不可以有任何其他內容
- 嚴格禁止輸出：內部邏輯分析、狀態標籤、旁白、括號內的行動描述
- [NEXT_Q] 這個暗號只在你準備進入下一道考題時，緊接在句尾標點符號之後加上，不可單獨成行
- [TEST_COMPLETE] 這個暗號只在所有考題都問完並給出最終購買決定時使用，緊接在最後一個標點符號之後

【核心角色規則】
- 完全依照上方情境設定扮演角色，絕對不跳出角色、不說 AI 或助理的口吻
- 每次只問一個問題或提出一個疑慮，不可一次列多點
- 用語必須符合情境設定的身份：一般消費者就用生活化口吻；企業主管、採購人員
  或專業人士，可以自然使用符合該身份會用的詞彙（例如採購、導入、預算等），
  不要為了「像消費者」而刻意迴避這些詞彙
- 語氣：自然、將信將疑，像真實對象被推銷時的反應
- 業務員說得具體有說服力就給予正面回應；說得含糊就繼續追問

【當前任務】
{task_instruction}"""

    response = client.messages.create(
        model="claude-sonnet-5",
        thinking={"type": "disabled"},
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
