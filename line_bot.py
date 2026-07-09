import os
import io
import bcrypt
import random
import threading
from datetime import datetime, timezone

from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, PushMessageRequest, TextMessage,
    QuickReply, QuickReplyItem, MessageAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, AudioMessageContent

from database import (
    get_supabase, get_employee_by_username, get_company_name_by_id,
    get_company_training_material,
)
from ai_services import get_customer_response, get_evaluation_report, speech_to_text

app = Flask(__name__)

configuration = Configuration(access_token=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.environ.get("LINE_CHANNEL_SECRET"))


# ── 小工具：讀寫 line_sessions（取代網頁版的 st.session_state）──
def get_session(line_user_id: str):
    sb = get_supabase()
    result = sb.table("line_sessions").select("*").eq("line_user_id", line_user_id).execute()
    return result.data[0] if result.data else None


def save_session(line_user_id: str, fields: dict):
    sb = get_supabase()
    payload = {"line_user_id": line_user_id, **fields,
               "updated_at": datetime.now(timezone.utc).isoformat()}
    sb.table("line_sessions").upsert(payload, on_conflict="line_user_id").execute()


def check_and_lock_event(line_user_id: str, message_id: str):
    """
    用 LINE 訊息的 message_id 判斷這個事件是不是 LINE 因為 webhook 逾時
    未收到 200 回應而自動重送的同一個事件。

    回傳 (is_duplicate, session)：
    - is_duplicate=True 代表這個 message_id 已經處理過，呼叫端要直接
      略過，避免重複呼叫 Claude、重複寫入 Supabase。
    - 若不是重複事件，會立刻把這個 message_id 記錄成「處理中」標記，
      再讓背景執行緒繼續做真正的處理。
    """
    session = get_session(line_user_id)
    if session and session.get("last_processed_message_id") == message_id:
        return True, session
    save_session(line_user_id, {"last_processed_message_id": message_id})
    return False, session


def reply(reply_token: str, texts, quick_items=None):
    """
    僅用於「處理中請稍候」這類立即回應——趁 reply_token 還新鮮時用掉。
    真正的對話內容/報告一律改用 push()，因為那些內容需要先等 Claude
    運算完成，時間一長 reply_token 幾乎必定已經過期。
    """
    if isinstance(texts, str):
        texts = [texts]
    messages = []
    trimmed = texts[:5]
    for i, t in enumerate(trimmed):
        qr = QuickReply(items=quick_items) if (quick_items and i == len(trimmed) - 1) else None
        messages.append(TextMessage(text=t, quick_reply=qr))
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=messages)
        )


def push(line_user_id: str, texts, quick_items=None):
    """真正的對話回覆／報告一律透過這個函式送出，不受 reply_token 時效限制。"""
    if isinstance(texts, str):
        texts = [texts]
    messages = []
    trimmed = texts[:5]
    for i, t in enumerate(trimmed):
        qr = QuickReply(items=quick_items) if (quick_items and i == len(trimmed) - 1) else None
        messages.append(TextMessage(text=t, quick_reply=qr))
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(to=line_user_id, messages=messages)
        )


def mode_quick_reply():
    return [
        QuickReplyItem(action=MessageAction(label="⚡ 急速模式", text="急速模式")),
        QuickReplyItem(action=MessageAction(label="🔥 深度模式", text="深度模式")),
    ]


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@app.route("/", methods=["GET"])
def health_check():
    return "PitchCoach LINE Bot is running."


@handler.add(MessageEvent, message=TextMessageContent)
def on_text(event):
    line_user_id = event.source.user_id
    message_id = event.message.id
    user_text = event.message.text.strip()

    is_dup, session = check_and_lock_event(line_user_id, message_id)
    if is_dup:
        print(f"[LINE Bot] 偵測到 LINE 重送同一則訊息，略過重複處理：{message_id}")
        return

    if session and session.get("state") == "in_conversation":
        try:
            reply(event.reply_token, "🤖 訓練小幫手思考中，請稍候幾秒鐘...")
        except Exception as e:
            print(f"[LINE Bot] 處理中提示送出失敗（可忽略，不影響正式回覆）：{e}")

    threading.Thread(target=safe_handle_incoming, args=(line_user_id, user_text), daemon=True).start()


@handler.add(MessageEvent, message=AudioMessageContent)
def on_audio(event):
    line_user_id = event.source.user_id
    message_id = event.message.id

    is_dup, session = check_and_lock_event(line_user_id, message_id)
    if is_dup:
        print(f"[LINE Bot] 偵測到 LINE 重送同一則語音訊息，略過重複處理：{message_id}")
        return

    try:
        reply(event.reply_token, "🤖 語音辨識 + 思考中，請稍候幾秒鐘...")
    except Exception as e:
        print(f"[LINE Bot] 處理中提示送出失敗（可忽略，不影響正式回覆）：{e}")

    threading.Thread(target=safe_handle_audio, args=(line_user_id, message_id), daemon=True).start()


def safe_handle_incoming(line_user_id: str, user_text: str):
    try:
        handle_incoming(line_user_id, user_text)
    except Exception as e:
        print(f"[LINE Bot錯誤] handle_incoming: {type(e).__name__}: {e}")
        try:
            push(line_user_id, "抱歉，系統剛剛忙線了一下，可以再說一次嗎？")
        except Exception as e2:
            print(f"[LINE Bot錯誤] 連備援訊息都送不出去：{e2}")


def safe_handle_audio(line_user_id: str, message_id: str):
    try:
        with ApiClient(configuration) as api_client:
            audio_bytes = MessagingApiBlob(api_client).get_message_content(message_id)

        session = get_session(line_user_id) or {}
        product_name = ""
        if session.get("state") == "in_conversation" and session.get("company_id"):
            material = get_company_training_material(session["company_id"])
            product_name = material.get("product_name", "")

        text = speech_to_text(
            io.BytesIO(audio_bytes),
            hint_text=product_name,
            product_name=product_name,
            audio_filename="audio.m4a",
            audio_mime_type="audio/m4a",
        )
        if not text:
            push(line_user_id, "抱歉，我剛剛沒有聽清楚，可以再說一次，或用打字的也可以：")
            return
        handle_incoming(line_user_id, text)
    except Exception as e:
        print(f"[LINE Bot錯誤] safe_handle_audio: {type(e).__name__}: {e}")
        try:
            push(line_user_id, "抱歉，語音辨識剛剛出了點問題，可以用打字的試試看嗎？")
        except Exception as e2:
            print(f"[LINE Bot錯誤] 連備援訊息都送不出去：{e2}")


def handle_incoming(line_user_id: str, user_text: str):
    session = get_session(line_user_id)

    if session is None:
        save_session(line_user_id, {"state": "awaiting_username"})
        push(line_user_id,
             "嗨！我是 PitchCoach 訓練小幫手 👋\n"
             "開始之前，先跟你核對一下身份。\n"
             "請輸入你的員工帳號：")
        return

    state = session.get("state", "awaiting_username")

    if state == "awaiting_username":
        save_session(line_user_id, {"state": "awaiting_password", "pending_username": user_text})
        push(line_user_id, "收到，請輸入密碼：")
        return

    if state == "awaiting_password":
        username = session.get("pending_username", "") or ""
        employee = get_employee_by_username(username)
        if employee and bcrypt.checkpw(user_text.encode(), employee["password_hash"].encode()):
            save_session(line_user_id, {
                "state": "awaiting_mode",
                "employee_id": employee["id"],
                "company_id": employee["company_id"],
                "employee_name": employee["employee_name"],
                "pending_username": None,
            })
            push(line_user_id,
                 [f"驗證成功！歡迎回來，{employee['employee_name']} 👋",
                  "準備好要練習了嗎？選一個模式開始吧："],
                 quick_items=mode_quick_reply())
        else:
            save_session(line_user_id, {"state": "awaiting_username", "pending_username": None})
            push(line_user_id, "帳號或密碼不正確，請重新輸入你的員工帳號：")
        return

    if state == "awaiting_mode":
        if "深度" in user_text:
            mode = "deep"
        elif "急速" in user_text or "速" in user_text:
            mode = "speed"
        else:
            push(line_user_id, "請點選下面其中一個模式喔：", quick_items=mode_quick_reply())
            return

        material = get_company_training_material(session["company_id"])
        questions_pool = material.get("questions", [])
        if not questions_pool:
            push(line_user_id, "目前你們公司還沒有上傳訓練教材，麻煩先請主管到 PitchCoach 後台上傳，稍後再回來練習喔！")
            return

        picked = random.sample(questions_pool, min(2, len(questions_pool)))
        save_session(line_user_id, {
            "state": "in_conversation",
            "training_mode": mode,
            "current_q_idx": 0,
            "chat_history": [],
            "picked_questions": picked,
        })
        mode_label = ("⚡ 急速模式：每題最多追問一次，快速決勝負" if mode == "speed"
                       else "🔥 深度模式：AI 嚴格追問，答不好絕不放過")
        push(line_user_id, [
            mode_label,
            f"請向接下來出現的 AI 客戶進行推銷，共有 {len(picked)} 道關卡，開始吧！你可以直接打字，或傳語音訊息給我。",
        ])
        return

    if state == "in_conversation":
        chat_history = session.get("chat_history") or []
        chat_history.append({"role": "user", "content": user_text})

        picked_questions = session.get("picked_questions") or []
        material = get_company_training_material(session["company_id"])

        ai_reply, new_idx = get_customer_response(
            chat_history=chat_history,
            published_questions=picked_questions,
            current_q_idx=session.get("current_q_idx", 0),
            analysis_context=material.get("main_analysis", ""),
            product_name=material.get("product_name", ""),
            customer_scenario=material.get("customer_scenario", ""),
            training_mode=session.get("training_mode", "speed"),
        )

        chat_history.append({"role": "assistant", "content": ai_reply})
        is_complete = "[TEST_COMPLETE]" in ai_reply
        clean_reply = ai_reply.replace("[NEXT_Q]", "").replace("[TEST_COMPLETE]", "").strip()

        if is_complete:
            report = get_evaluation_report(
                chat_history=chat_history,
                published_questions=picked_questions,
                customer_scenario=material.get("customer_scenario", ""),
                product_benefits=material.get("product_benefits", ""),
                training_mode=session.get("training_mode", "speed"),
            )

            sb = get_supabase()
            sb.table("scores").insert({
                "company_id":        session["company_id"],
                "employee_name":     session.get("employee_name", ""),
                "score":             report.get("score", 0),
                "training_mode":     session.get("training_mode", "speed"),
                "left_brain_score":  report.get("left_brain_score", 0),
                "right_brain_score": report.get("right_brain_score", 0),
                "closing_score":     report.get("closing_score", 0),
                "left_brain":        report.get("left_brain", ""),
                "right_brain":       report.get("right_brain", ""),
                "action_item":       report.get("action_item", ""),
                "closing_result":    report.get("closing_result", ""),
                "strength":          report.get("strength", ""),
                "improvement_tips":  report.get("improvement_tips", []),
            }).execute()

            save_session(line_user_id, {
                "state": "awaiting_mode",
                "training_mode": None,
                "current_q_idx": 0,
                "chat_history": [],
                "picked_questions": [],
            })

            tips = report.get("improvement_tips", [])
            report_messages = [clean_reply, f"📊 這次的綜合分數：{report.get('score', 0)} 分"]
            if report.get("strength"):
                report_messages.append(f"✨ 表現亮點：{report['strength']}")
            if tips:
                report_messages.append(f"🎯 下次練習重點：{tips[0]}")
            report_messages.append("要再練一次嗎？選一個模式開始：")

            push(line_user_id, report_messages[:4], quick_items=mode_quick_reply())
        else:
            save_session(line_user_id, {
                "chat_history": chat_history,
                "current_q_idx": new_idx,
            })
            push(line_user_id, clean_reply)
        return

    save_session(line_user_id, {"state": "awaiting_username"})
    push(line_user_id, "咦，好像哪裡卡住了，我們重新開始吧！請輸入你的員工帳號：")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
