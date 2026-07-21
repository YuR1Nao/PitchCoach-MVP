import streamlit as st
import json
from supabase import create_client, Client
from config import (SUPABASE_URL, SUPABASE_KEY)


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
            qbc = row.get("questions_by_category") or {}
            for cat, qs in qbc.items():
                if cat not in combined_by_category:
                    combined_by_category[cat] = []
                combined_by_category[cat].extend(qs)
            combined_questions.extend(row.get("questions") or [])

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
            "id, filename, questions_by_category, created_at, is_active"
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
        for row in result.data:
            combined_questions.extend(row.get("questions") or [])

        latest = result.data[-1]

        return {
            "main_analysis":     latest.get("main_analysis", ""),
            "product_name":      latest.get("product_name", ""),
            "product_benefits":  latest.get("product_benefits", ""),
            "customer_scenario": latest.get("customer_scenario", ""),
            "questions":         combined_questions,
        }
    except Exception as e:
        print(f"[Supabase警告] get_company_training_material 失敗：{e}")
        return {}
