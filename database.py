import streamlit as st
import json
import random
from supabase import create_client, Client
from config import (SUPABASE_URL, SUPABASE_KEY, CATEGORY_LABELS)


def get_supabase() -> Client:
    """建立並回傳 Supabase client 實例。"""
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def load_settings() -> None:
    """
    程式啟動時還原主管設定到 session_state。

    只查詢當前登入公司（依 session_state 的 company_id）自己的 Supabase 資料。
    查無資料代表這是全新公司或尚未上傳教材，屬於正常狀態，直接維持空白，
    不會再 fallback 到任何本機快取檔案（避免不同公司的資料互相污染）。

    只還原 session_state 中尚未存在的鍵，避免覆蓋當次已操作的資料。
    """
    company_id = st.session_state.get("company_id", "")
    if not company_id:
        print("[Supabase警告] load_settings：session_state 尚無 company_id，略過")
        return

    try:
        sb = get_supabase()

        result = sb.table("training_sets").select("*").eq(
            "company_id", company_id
        ).eq(
            "is_published", True
        ).eq(
            "is_active", True
        ).order(
            "created_at", desc=False
        ).execute()

        if not result.data:
            # 全新公司或尚未上傳教材，這是正常狀態，不是錯誤
            print("[Supabase] load_settings：此公司尚無已發布的訓練設定（正常）")
            return

        combined_questions = []
        combined_by_category = {}
        for row in result.data:
            excluded = set(row.get("excluded_questions") or [])
            qbc = row.get("questions_by_category") or {}
            for cat, qs in qbc.items():
                if cat not in combined_by_category:
                    combined_by_category[cat] = []
                combined_by_category[cat].extend(q for q in qs if q not in excluded)
            combined_questions.extend(q for q in (row.get("questions") or []) if q not in excluded)

        latest = result.data[-1]
        all_filenames = "、".join(r.get("filename", "") for r in result.data)

        mapping = {
            "main_analysis":         latest.get("main_analysis"),
            "questions":             combined_questions,
            "analyzed_filename":     all_filenames,
            "product_name":          latest.get("product_name"),
            "product_benefits":      latest.get("product_benefits"),
            "target_audience":       latest.get("target_audience"),
            "published_questions":   latest.get("published_questions"),
            "customer_scenario":     latest.get("customer_scenario"),
            "task_published":        latest.get("is_published", False),
            "questions_by_category": combined_by_category,
        }
        for key, val in mapping.items():
            if key not in st.session_state and val is not None and val != [] and val != "":
                st.session_state[key] = val

        print("[Supabase] load_settings 成功")

    except Exception as e:
        # 這裡只會是真正的連線／查詢錯誤（例如 Supabase 暫時連不上），
        # 不再 fallback 到本機 JSON，避免不同公司的資料互相污染
        print(f"[Supabase警告] load_settings 失敗：{e}")
        st.warning(f"⚠️ 讀取公司設定時發生問題，部分資料可能需要重新整理頁面：{str(e)}")


def get_or_create_company(name: str = "") -> str:
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


def get_all_training_sets(company_id: str) -> list:
    """取得公司所有已發布的訓練集，供主管管理 PDF 列表使用"""
    try:
        sb = get_supabase()
        result = sb.table("training_sets").select(
            "id, filename, questions_by_category, created_at, is_active, excluded_questions"
        ).eq("company_id", company_id).eq(
            "is_published", True
        ).order("created_at", desc=False).execute()
        return result.data if result.data else []
    except Exception as e:
        print(f"[Supabase警告] get_all_training_sets 失敗：{e}")
        return []


def delete_training_set(training_set_id: str) -> bool:
    """刪除指定的訓練集（單份 PDF）"""
    try:
        sb = get_supabase()
        sb.table("training_sets").delete().eq("id", training_set_id).execute()
        return True
    except Exception as e:
        print(f"[Supabase警告] delete_training_set 失敗：{e}")
        return False


def toggle_training_set_active(training_set_id: str, is_active: bool) -> bool:
    """切換指定訓練集的啟用/停用狀態"""
    try:
        sb = get_supabase()
        sb.table("training_sets").update(
            {"is_active": is_active}
        ).eq("id", training_set_id).execute()
        return True
    except Exception as e:
        print(f"[Supabase警告] toggle_training_set_active 失敗：{e}")
        return False


def save_training_set_file(company_id: str, filename: str, questions_by_category: dict,
                            questions: list, product_name: str = "", main_analysis: str = "",
                            product_benefits: str = "", target_audience: str = "") -> bool:
    """
    儲存「單一份」PDF 自己的萃取結果為一筆獨立的 training_sets 紀錄。

    每次上傳新PDF、AI萃取完成後應立即呼叫這個函式，只存這份文件自己的
    題目，絕對不要存累積合併後的題庫——「累積」這件事只應該發生在讀取端
    （load_settings / get_company_training_material 已經是正確的合併邏輯，
    會把公司底下所有啟用中的紀錄逐筆加總），寫入端如果也存累積後的內容，
    兩邊疊加會造成題目重複膨脹。
    """
    if not company_id:
        st.warning("⚠️ 尚未取得公司身份，無法儲存教材")
        return False
    try:
        sb = get_supabase()
        sb.table("training_sets").insert({
            "company_id":            company_id,
            "filename":              filename,
            "product_name":          product_name,
            "main_analysis":         main_analysis,
            "product_benefits":      product_benefits,
            "target_audience":       target_audience,
            "questions":             questions,
            "published_questions":   [],
            "customer_scenario":     "",
            "questions_by_category": questions_by_category,
            "is_published":          True,
            "is_active":             True,
        }).execute()
        print(f"[Supabase] training_sets 儲存成功（單一文件：{filename}）")
        return True
    except Exception as e:
        st.error(f"⚠️ Supabase 儲存失敗：{str(e)}，請重新嘗試上傳")
        return False


def update_training_set_question(training_set_id: str, category_key: str, question_index: int,
                                   new_text: str = None, delete: bool = False) -> bool:
    """
    更新或刪除某一筆 training_sets 紀錄裡，指定分類、指定索引位置的單一題目。

    new_text 有值時：把該位置的題目文字整個替換成 new_text（呼叫端應組好完整格式
    「題目 👉 建議回答方向：答案」再傳進來）。
    delete=True 時：把該位置的題目從陣列中移除。
    兩者互斥，只會執行其中一種操作。

    因為 questions_by_category 是一個「值為陣列」的巢狀 JSON 結構，Supabase
    沒辦法直接更新陣列裡的單一元素，所以做法是：先把整份 questions_by_category
    抓下來、在 Python 裡改好，再整份寫回去。同時重新展平 questions 欄位，
    確保這筆紀錄裡的兩個欄位彼此保持一致，不會各自代表不同版本的內容。
    """
    try:
        sb = get_supabase()
        result = sb.table("training_sets").select(
            "questions_by_category"
        ).eq("id", training_set_id).execute()
        if not result.data:
            return False

        qbc = result.data[0].get("questions_by_category") or {}
        cat_qs = list(qbc.get(category_key, []))
        if question_index < 0 or question_index >= len(cat_qs):
            return False

        if delete:
            cat_qs.pop(question_index)
        elif new_text is not None:
            cat_qs[question_index] = new_text
        else:
            return False
        qbc[category_key] = cat_qs

        flat_questions = []
        for qs in qbc.values():
            flat_questions.extend(qs)

        sb.table("training_sets").update({
            "questions_by_category": qbc,
            "questions": flat_questions,
        }).eq("id", training_set_id).execute()
        return True
    except Exception as e:
        st.error(f"⚠️ 更新題目失敗：{str(e)}")
        print(f"[Supabase警告] update_training_set_question 失敗：{e}")
        return False


def select_next_questions(company_id: str, employee_name: str, questions_by_category: dict) -> tuple:
    """
    隨機挑戰模式的核心抽題邏輯（網頁版main.py、LINE bot共用同一份）。

    強制順序覆蓋法：
    階段A（覆蓋模式）：只要還有任何啟用中的分類存在「沒練過的題目」，就按
    CATEGORY_LABELS固定順序輪替分類，優先把每個分類的題目都摸過一輪，
    確保新人不會漏練任何一種疑慮。
    階段B（分數優先模式）：全部分類都覆蓋完畢後，排除上一次剛練過的分類，
    剩下的分類用「平均分數越低、被選中機率越高」的加權隨機去選，弱點會
    更常被抽到，但不會每次都卡在同一類。

    分類選定後，題目一律「沒看過優先、次數最少優先、都差不多才隨機」去挑，
    最多抽2題，分類只有1題就只出1題，不勉強湊數。

    回傳 (selected_questions: list[str], selected_category: str)。
    如果完全沒有可用題目，回傳 ([], "")，呼叫端應自行 fallback 到
    扁平列表隨機抽題（相容沒有分類資料的舊資料）。
    """
    disabled_cats = get_disabled_categories(company_id)
    available_cats = [
        cat for cat, qs in questions_by_category.items()
        if qs and cat not in disabled_cats
    ]
    if not available_cats:
        available_cats = [cat for cat, qs in questions_by_category.items() if qs]
    if not available_cats:
        return [], ""

    category_scores: dict = {}
    question_counts: dict = {}
    last_cat = None
    try:
        sb = get_supabase()
        hist_result = sb.table("scores").select(
            "score, practiced_questions, created_at"
        ).eq(
            "company_id", company_id
        ).eq(
            "employee_name", employee_name
        ).order("created_at", desc=False).execute()
        hist_rows = hist_result.data or []
        for row in hist_rows:
            pq = row.get("practiced_questions") or []
            score_v = row.get("score", 0)
            for item in pq:
                cat = item.get("category", "")
                q_text = item.get("question", "")
                if not cat or not q_text:
                    continue
                category_scores.setdefault(cat, []).append(score_v)
                question_counts.setdefault(cat, {})
                question_counts[cat][q_text] = question_counts[cat].get(q_text, 0) + 1
        if hist_rows:
            last_pq = hist_rows[-1].get("practiced_questions") or []
            if last_pq:
                last_cat = last_pq[0].get("category", None)
    except Exception as e:
        print(f"[Supabase警告] select_next_questions 讀取練習歷史失敗，改用純隨機抽題：{e}")

    cats_with_uncovered = [
        cat for cat in available_cats
        if any(q not in question_counts.get(cat, {}) for q in questions_by_category[cat])
    ]
    rotation_order = list(CATEGORY_LABELS.keys())

    if cats_with_uncovered:
        if last_cat in rotation_order:
            start_idx = rotation_order.index(last_cat) + 1
        else:
            start_idx = 0
        selected_cat = None
        for i in range(len(rotation_order)):
            candidate = rotation_order[(start_idx + i) % len(rotation_order)]
            if candidate in cats_with_uncovered:
                selected_cat = candidate
                break
        if selected_cat is None:
            selected_cat = cats_with_uncovered[0]
    else:
        candidates = list(available_cats)
        if len(candidates) > 1 and last_cat in candidates:
            candidates = [c for c in candidates if c != last_cat]
        avg = {
            c: (sum(category_scores[c]) / len(category_scores[c]))
            if category_scores.get(c) else 50
            for c in candidates
        }
        weights = [max(1, 101 - avg[c]) for c in candidates]
        selected_cat = random.choices(candidates, weights=weights, k=1)[0]

    cat_questions = list(questions_by_category[selected_cat])
    cat_q_counts = question_counts.get(selected_cat, {})
    q_with_counts = [(q, cat_q_counts.get(q, 0)) for q in cat_questions]
    random.shuffle(q_with_counts)
    q_with_counts.sort(key=lambda x: x[1])
    selected_questions = [q for q, _ in q_with_counts[:min(2, len(q_with_counts))]]

    return selected_questions, selected_cat


def toggle_question_included(training_set_id: str, question_text: str, included: bool) -> bool:
    """
    切換單一題目是否納入員工隨機挑戰模式的抽題範圍。
    included=False：把這題加進該筆紀錄的 excluded_questions 清單（暫時
    排除，題目本身不會被刪除，隨時可以再打勾恢復）。
    included=True：把它從清單中移除，恢復正常參與抽題。
    """
    try:
        sb = get_supabase()
        result = sb.table("training_sets").select("excluded_questions").eq("id", training_set_id).execute()
        if not result.data:
            return False
        excluded = list(result.data[0].get("excluded_questions") or [])
        if included:
            excluded = [q for q in excluded if q != question_text]
        else:
            if question_text not in excluded:
                excluded.append(question_text)
        sb.table("training_sets").update({"excluded_questions": excluded}).eq("id", training_set_id).execute()
        return True
    except Exception as e:
        print(f"[Supabase警告] toggle_question_included 失敗：{e}")
        return False


def save_settings() -> None:
    """
    將目前的任務發布設定（已選定的2題、客戶情境）同步更新到這家公司
    所有的 training_sets 紀錄上。

    這裡是 update（不是 insert）：每份PDF自己的題目內容已經在上傳當下
    由 save_training_set_file() 各自存成獨立一筆，這裡不應該再重複寫入
    整包題庫，只需要更新「發布任務」這個公司層級的設定即可。
    """
    company_id = st.session_state.get("company_id", "")
    if not company_id:
        st.warning("⚠️ 尚未取得公司身份，無法儲存設定")
        return

    try:
        sb = get_supabase()
        sb.table("training_sets").update({
            "published_questions":   st.session_state.get("published_questions", []),
            "customer_scenario":     st.session_state.get("customer_scenario", ""),
            "is_published":          True,
        }).eq("company_id", company_id).execute()
        print("[Supabase] 任務發布設定更新成功")
    except Exception as e:
        st.error(f"⚠️ Supabase 儲存失敗：{str(e)}，請重新嘗試發布")


def get_disabled_categories(company_id: str) -> list:
    """取得這家公司目前暫時停用的分類清單（主管手動關閉，不影響題庫本身，
    只影響員工隨機挑戰模式抽不抽得到）"""
    try:
        sb = get_supabase()
        result = sb.table("companies").select("disabled_categories").eq("id", company_id).execute()
        if result.data and result.data[0].get("disabled_categories"):
            return result.data[0]["disabled_categories"]
        return []
    except Exception as e:
        print(f"[Supabase警告] get_disabled_categories 失敗：{e}")
        return []


def set_disabled_categories(company_id: str, disabled_categories: list) -> bool:
    """設定這家公司目前暫時停用的分類清單"""
    try:
        sb = get_supabase()
        sb.table("companies").update({
            "disabled_categories": disabled_categories
        }).eq("id", company_id).execute()
        return True
    except Exception as e:
        print(f"[Supabase警告] set_disabled_categories 失敗：{e}")
        return False


def get_company_by_access_code(access_code: str):
    """依 access_code 查詢公司，回傳 dict 或 None"""
    try:
        sb = get_supabase()
        result = sb.table("companies").select(
            "id, name, access_code, admin_password_hash"
        ).eq("access_code", access_code).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[Supabase警告] get_company_by_access_code 失敗：{e}")
        return None


def get_employee_by_username(username: str):
    """依 username 查詢員工帳號，回傳 dict（含 company_id、employee_name）或 None"""
    try:
        sb = get_supabase()
        result = sb.table("employees").select(
            "id, company_id, employee_name, username, password_hash"
        ).eq("username", username).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[Supabase警告] get_employee_by_username 失敗：{e}")
        return None


def get_company_name_by_id(company_id: str) -> str:
    """依 company_id 查詢公司名稱"""
    try:
        sb = get_supabase()
        result = sb.table("companies").select("name").eq("id", company_id).execute()
        return result.data[0]["name"] if result.data else ""
    except Exception as e:
        print(f"[Supabase警告] get_company_name_by_id 失敗：{e}")
        return ""


def set_company_credentials(company_id: str, access_code: str, admin_password_hash: str) -> bool:
    """設定（或覆寫）指定公司的登入代號與管理員密碼"""
    try:
        sb = get_supabase()
        sb.table("companies").update({
            "access_code": access_code,
            "admin_password_hash": admin_password_hash
        }).eq("id", company_id).execute()
        return True
    except Exception as e:
        st.error(f"⚠️ 設定公司帳密失敗：{str(e)}")
        print(f"[Supabase警告] set_company_credentials 失敗：{e}")
        return False


def create_employee_account(company_id: str, employee_name: str, username: str, password_hash: str) -> bool:
    """新增一位員工的登入帳號"""
    try:
        sb = get_supabase()
        sb.table("employees").insert({
            "company_id":    company_id,
            "employee_name": employee_name,
            "username":      username,
            "password_hash": password_hash,
        }).execute()
        return True
    except Exception as e:
        st.error(f"⚠️ 建立員工帳號失敗：{str(e)}")
        print(f"[Supabase警告] create_employee_account 失敗：{e}")
        return False


def list_all_companies() -> list:
    """列出所有公司，供平台管理頁面下拉選單使用"""
    try:
        sb = get_supabase()
        result = sb.table("companies").select("id, name, access_code").order("name").execute()
        return result.data if result.data else []
    except Exception as e:
        print(f"[Supabase警告] list_all_companies 失敗：{e}")
        return []


def get_company_training_material(company_id: str) -> dict:
    """
    依 company_id 直接查詢該公司已發布的訓練教材，回傳純 dict。
    不寫入 st.session_state，供不依賴 Streamlit 的服務（例如 LINE Bot）使用。
    找不到資料時回傳空 dict，呼叫端要自行處理「尚無教材」的情況。
    """
    try:
        sb = get_supabase()
        result = sb.table("training_sets").select("*").eq(
            "company_id", company_id
        ).eq(
            "is_published", True
        ).eq(
            "is_active", True
        ).order(
            "created_at", desc=False
        ).execute()

        if not result.data:
            return {}

        combined_questions = []
        combined_by_category = {}
        for row in result.data:
            excluded = set(row.get("excluded_questions") or [])
            combined_questions.extend(q for q in (row.get("questions") or []) if q not in excluded)
            qbc = row.get("questions_by_category") or {}
            for cat, qs in qbc.items():
                combined_by_category.setdefault(cat, [])
                combined_by_category[cat].extend(q for q in qs if q not in excluded)

        latest = result.data[-1]

        return {
            "main_analysis":     latest.get("main_analysis", ""),
            "product_name":      latest.get("product_name", ""),
            "product_benefits":  latest.get("product_benefits", ""),
            "customer_scenario": latest.get("customer_scenario", ""),
            "questions":         combined_questions,
            "questions_by_category": combined_by_category,
        }
    except Exception as e:
        print(f"[Supabase警告] get_company_training_material 失敗：{e}")
        return {}
